/**
 * Credential persistence.
 *
 * Tokens live in a single 0600 JSON file next to the app. Refresh tokens are
 * long-lived, so the file is the app's most sensitive artifact.
 */

import { chmod, mkdir, rename, rm, stat, unlink } from "node:fs/promises";
import { isRecord, readNumber, readString } from "./guards";
import { type ProviderId, isProviderId } from "./providers";

const STORE_PATH = process.env.CREDENTIALS_PATH || `${import.meta.dir}/../credentials.json`;
const SEED_PATH = process.env.CREDENTIALS_SEED_PATH || "/app/credentials-seed/credentials.json";

export interface StoredCredential {
	access: string;
	refresh?: string;
	/** Epoch ms at which `access` stops working. */
	expires?: number;
	email?: string;
	/** Google Cloud project backing an Antigravity account. */
	projectId?: string;
	/** Free-form plan label surfaced by the provider. */
	plan?: string;
	authorizedAt?: number;
}

export type CredentialMap = Partial<Record<ProviderId, StoredCredential>>;

/**
 * A rotated token must survive a failed write. The Kubernetes pod mounts this
 * store from a Secret, so the FS is read-only there and `Bun.write` throws
 * EROFS — which used to reject the whole refresh and leave the caller on a dead
 * access token. Persistence is therefore best-effort: the overlay keeps the
 * live credential for the process lifetime (omp's broker likewise keeps a
 * refreshed row usable independently of the durable write). `null` marks a
 * deletion that could not be persisted.
 */
const overlay = new Map<ProviderId, StoredCredential | null>();
let persistFailure: string | undefined;

/** Last persistence error, or undefined when the store is writable. */
export function persistenceError(): string | undefined {
	return persistFailure;
}

/**
 * Set when the store exists but cannot be parsed. A tolerant reader returning
 * `{}` is fine; letting the next write persist that `{}` is not — it wipes
 * every refresh token and costs a full re-login on all providers.
 */
let storeUnreadable = false;

function applyOverlay(credentials: CredentialMap): CredentialMap {
	for (const [provider, value] of overlay) {
		if (value === null) delete credentials[provider];
		else credentials[provider] = value;
	}
	return credentials;
}

export async function loadCredentials(): Promise<CredentialMap> {
	const credentials: CredentialMap = {};
	let file = Bun.file(STORE_PATH);
	const seedFile = Bun.file(SEED_PATH);

	// 1. Load from seed file if present
	if (await seedFile.exists()) {
		try {
			const seedParsed: unknown = await seedFile.json();
			if (isRecord(seedParsed)) {
				for (const [provider, value] of Object.entries(seedParsed)) {
					if (!isProviderId(provider) || !isRecord(value)) continue;
					const access = readString(value.access);
					if (!access) continue;
					credentials[provider] = {
						access,
						refresh: readString(value.refresh),
						expires: readNumber(value.expires),
						email: readString(value.email),
						projectId: readString(value.projectId),
						plan: readString(value.plan),
						authorizedAt: readNumber(value.authorizedAt),
					};
				}
			}
		} catch (e) {
			console.warn(`[store] error reading seed: ${e}`);
		}
	}

	// 2. Overwrite with STORE_PATH if present and readable
	if (await file.exists()) {
		try {
			const parsed: unknown = await file.json();
			if (!isRecord(parsed)) {
				storeUnreadable = true;
				console.warn(`[store] STORE_PATH is not a JSON object; writes blocked to prevent token loss`);
			} else {
				storeUnreadable = false;
				for (const [provider, value] of Object.entries(parsed)) {
					if (!isProviderId(provider) || !isRecord(value)) continue;
					const access = readString(value.access);
					if (!access) continue;
					credentials[provider] = {
						access,
						refresh: readString(value.refresh) ?? credentials[provider]?.refresh,
						expires: readNumber(value.expires) ?? credentials[provider]?.expires,
						email: readString(value.email) ?? credentials[provider]?.email,
						projectId: readString(value.projectId) ?? credentials[provider]?.projectId,
						plan: readString(value.plan) ?? credentials[provider]?.plan,
						authorizedAt: readNumber(value.authorizedAt) ?? credentials[provider]?.authorizedAt,
					};
				}
			}
		} catch (error) {
			storeUnreadable = true;
			console.warn(`[store] STORE_PATH unreadable: ${error}`);
		}
	} else {
		storeUnreadable = false;
	}

	return applyOverlay(credentials);
}

async function syncToKubernetesSecrets(credentials: CredentialMap): Promise<void> {
	try {
		const tokenPath = "/var/run/secrets/kubernetes.io/serviceaccount/token";
		const caPath = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt";
		const nsPath = "/var/run/secrets/kubernetes.io/serviceaccount/namespace";

		const tokenFile = Bun.file(tokenPath);
		if (!(await tokenFile.exists())) return;

		const saToken = (await tokenFile.text()).trim();
		const ns = (await Bun.file(nsPath).text()).trim();
		const host = process.env.KUBERNETES_SERVICE_HOST || "kubernetes.default.svc";
		const port = process.env.KUBERNETES_SERVICE_PORT || "443";
		const k8sBase = `https://${host}:${port}`;
		const caFile = Bun.file(caPath);
		const tls = (await caFile.exists()) ? { ca: caFile } : undefined;
		const failures: string[] = [];
		const patchSecret = async (url: string, body: string): Promise<void> => {
			const response = await fetch(url, {
				method: "PATCH",
				headers: {
					Authorization: `Bearer ${saToken}`,
					"Content-Type": "application/strategic-merge-patch+json",
				},
				body,
				signal: AbortSignal.timeout(10_000),
				...(tls ? { tls } : {}),
			});
			if (!response.ok) throw new Error(`HTTP ${response.status}`);
		};

		// 1. Sync Secret quota-dashboard-credentials in current namespace
		const qCredsJson = JSON.stringify(credentials, null, 2);
		const qCredsB64 = Buffer.from(qCredsJson).toString("base64");
		const patchQuotaBody = JSON.stringify({
			data: {
				"credentials.json": qCredsB64,
			},
		});

		try {
			await patchSecret(`${k8sBase}/api/v1/namespaces/${ns}/secrets/quota-dashboard-credentials`, patchQuotaBody);
		} catch (error) {
			failures.push(`quota-dashboard-credentials: ${error}`);
		}

		// 2. Sync Secret litellm-secrets in litellm namespace
		const litellmData: Record<string, string | null> = {
			OPENAI_CODEX_OAUTH_TOKEN: null,
			OPENAI_CODEX_REFRESH_TOKEN: null,
			ANTHROPIC_OAUTH_TOKEN: null,
			ANTHROPIC_REFRESH_TOKEN: null,
			GOOGLE_ANTIGRAVITY_OAUTH_TOKEN: null,
			GOOGLE_ANTIGRAVITY_REFRESH_TOKEN: null,
			GOOGLE_ANTIGRAVITY_PROJECT_ID: null,
		};
		if (credentials["openai-codex"]?.access) {
			litellmData["OPENAI_CODEX_OAUTH_TOKEN"] = Buffer.from(credentials["openai-codex"].access).toString("base64");
		}
		if (credentials["openai-codex"]?.refresh) {
			litellmData["OPENAI_CODEX_REFRESH_TOKEN"] = Buffer.from(credentials["openai-codex"].refresh).toString("base64");
		}
		if (credentials["anthropic"]?.access) {
			litellmData["ANTHROPIC_OAUTH_TOKEN"] = Buffer.from(credentials["anthropic"].access).toString("base64");
		}
		if (credentials["anthropic"]?.refresh) {
			litellmData["ANTHROPIC_REFRESH_TOKEN"] = Buffer.from(credentials["anthropic"].refresh).toString("base64");
		}
		if (credentials["google-antigravity"]?.access) {
			litellmData["GOOGLE_ANTIGRAVITY_OAUTH_TOKEN"] = Buffer.from(credentials["google-antigravity"].access).toString("base64");
		}
		if (credentials["google-antigravity"]?.refresh) {
			litellmData["GOOGLE_ANTIGRAVITY_REFRESH_TOKEN"] = Buffer.from(credentials["google-antigravity"].refresh).toString("base64");
		}
		if (credentials["google-antigravity"]?.projectId) {
			litellmData["GOOGLE_ANTIGRAVITY_PROJECT_ID"] = Buffer.from(credentials["google-antigravity"].projectId).toString("base64");
		}

		try {
			await patchSecret(
				`${k8sBase}/api/v1/namespaces/litellm/secrets/litellm-secrets`,
				JSON.stringify({ data: litellmData }),
			);
		} catch (error) {
			failures.push(`litellm-secrets: ${error}`);
		}
		if (failures.length > 0) {
			console.warn(`[store] Kubernetes Secret sync incomplete: ${failures.join("; ")}`);
		} else {
			console.log(`[store] Kubernetes Secrets synchronized successfully`);
		}
	} catch (e) {
		console.warn(`[store] Error syncing Kubernetes Secrets: ${e}`);
	}
}

/**
 * Serializes every mutation. Refreshes run concurrently and Anthropic/OpenAI
 * rotate refresh tokens, so two interleaved read-modify-write cycles can drop
 * a rotation — and the superseded token is already dead upstream, which costs
 * a full re-login. The queue makes each mutation observe the previous write.
 */
let writeQueue: Promise<void> = Promise.resolve();
const LOCK_TIMEOUT_MS = 30_000;
const LOCK_STALE_MS = 10 * 60_000;

async function withStoreLock<T>(operation: () => Promise<T>): Promise<T> {
	const lockPath = `${STORE_PATH}.lockdir`;
	const deadline = Date.now() + LOCK_TIMEOUT_MS;
	while (true) {
		try {
			await mkdir(lockPath, { mode: 0o700 });
			break;
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
			try {
				const details = await stat(lockPath);
				if (Date.now() - details.mtimeMs > LOCK_STALE_MS) {
					await rm(lockPath, { recursive: true, force: true });
					continue;
				}
			} catch {
				continue;
			}
			if (Date.now() >= deadline) throw new Error("timed out waiting for the credentials writer lock");
			await Bun.sleep(50);
		}
	}
	try {
		return await operation();
	} finally {
		await rm(lockPath, { recursive: true, force: true }).catch(() => {});
	}
}

function mutate(apply: (credentials: CredentialMap) => void): Promise<void> {
	const next = writeQueue.then(async () => {
		const credentials = await withStoreLock(async () => {
			const loaded = await loadCredentials();
			apply(loaded);
			if (storeUnreadable) {
				persistFailure = "credentials file unreadable — write blocked to prevent token loss";
				console.warn(`[store] ${persistFailure}`);
				return undefined;
			}
			try {
				const tmpPath = `${STORE_PATH}.${process.pid}.${crypto.randomUUID()}.tmp`;
				try {
					await Bun.write(tmpPath, `${JSON.stringify(loaded, null, 2)}\n`);
					await chmod(tmpPath, 0o600);
					await rename(tmpPath, STORE_PATH);
					await chmod(STORE_PATH, 0o600);
				} finally {
					await unlink(tmpPath).catch(() => {});
				}
				persistFailure = undefined;
			} catch (error) {
				// Read-only mount (K8s Secret): the overlay already holds the value, so
				// the refresh still counts. Losing the write must not fail it.
				persistFailure = error instanceof Error ? error.message : String(error);
				console.warn(`[store] persistence failed (using in-memory overlay): ${persistFailure}`);
			}
			return loaded;
		});
		if (!credentials) return;
		await syncToKubernetesSecrets(credentials);
	});
	// Keep the chain alive after a rejection so one failure cannot wedge the queue.
	writeQueue = next.catch(() => {});
	return next;
}

export function saveCredential(provider: ProviderId, credential: StoredCredential): Promise<void> {
	return mutate(credentials => {
		const previous = credentials[provider];
		const merged: StoredCredential = {
			...previous,
			...credential,
			refresh: credential.refresh ?? previous?.refresh,
			projectId: credential.projectId ?? previous?.projectId,
		};
		overlay.set(provider, merged);
		credentials[provider] = merged;
	});
}

export function deleteCredential(provider: ProviderId): Promise<void> {
	return mutate(credentials => {
		overlay.set(provider, null);
		delete credentials[provider];
	});
}
