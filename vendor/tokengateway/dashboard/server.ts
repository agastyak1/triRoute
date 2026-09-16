/**
 * Quota Dashboard — autonomous subscription-limit viewer.
 *
 * Owns its OAuth flows and talks to each provider's usage endpoint directly;
 * nothing here depends on another agent or CLI being installed.
 */

import { isRecord, readString } from "./src/guards";
import {
	beginLogin,
	cancelLogin,
	completeLoginWithCode,
	isDefinitiveOAuthFailure,
	refreshCredential,
} from "./src/oauth";
import { PROVIDERS, PROVIDER_IDS, isProviderId } from "./src/providers";
import { deleteCredential, loadCredentials, saveCredential } from "./src/store";
import { fetchAllUsage, clearCooldown } from "./src/usage";
import { HTML } from "./src/ui";

const PORT = Number(process.env.PORT ?? 3737);
const DASHBOARD_API_KEY = process.env.DASHBOARD_API_KEY?.trim() ?? "";
const DASHBOARD_COOKIE = "triroute_dashboard_session";

if (!DASHBOARD_API_KEY) {
	console.error("DASHBOARD_API_KEY is required; refusing to start an unauthenticated dashboard");
	process.exit(78);
}

function constantTimeEqual(left: string, right: string): boolean {
	let diff = left.length ^ right.length;
	const length = Math.max(left.length, right.length);
	for (let index = 0; index < length; index++) {
		diff |= (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
	}
	return diff === 0;
}

function bearerKey(req: Request): string | undefined {
	const authorization = req.headers.get("authorization") ?? "";
	const match = authorization.match(/^Bearer\s+(.+)$/i);
	return match?.[1];
}

function cookieKey(req: Request): string | undefined {
	const cookies = req.headers.get("cookie") ?? "";
	for (const part of cookies.split(";")) {
		const [name, ...value] = part.trim().split("=");
		if (name !== DASHBOARD_COOKIE) continue;
		try {
			return decodeURIComponent(value.join("="));
		} catch {
			return undefined;
		}
	}
	return undefined;
}

function isAuthorized(req: Request): boolean {
	return [bearerKey(req), cookieKey(req)].some(value => Boolean(value && constantTimeEqual(value, DASHBOARD_API_KEY)));
}

function isDashboardMutation(req: Request): boolean {
	return req.headers.has("authorization") || req.headers.get("x-requested-with") === "triroute-dashboard";
}

/** In-flight logins, so the UI can poll for completion and surface failures. */
interface LoginState {
	url: string;
	status: "pending" | "done" | "error";
	message?: string;
}
const logins: Map<string, LoginState> = new Map();

async function handleApi(req: Request, url: URL): Promise<Response> {
	if (!isAuthorized(req)) {
		return Response.json(
			{ error: "dashboard authentication required" },
			{ status: 401, headers: { "WWW-Authenticate": "Bearer" } },
		);
	}
	if (req.method === "POST" && !isDashboardMutation(req)) {
		return Response.json({ error: "missing dashboard request header" }, { status: 403 });
	}
	if (url.pathname === "/api/status") {
		const credentials = await loadCredentials();
		return Response.json({
			providers: PROVIDER_IDS.map(id => ({
				id,
				label: PROVIDERS[id].label,
				connected: Boolean(credentials[id]),
				email: credentials[id]?.email,
				plan: credentials[id]?.plan,
				login: logins.get(id),
			})),
		});
	}

	if (url.pathname === "/api/usage") {
		return Response.json({ generatedAt: Date.now(), reports: await fetchAllUsage() });
	}
	if (url.pathname === "/api/credentials" && req.method === "GET") {
		const credentials = await loadCredentials();
		return Response.json(credentials, { headers: { "cache-control": "no-store" } });
	}
	const credProviderMatch = url.pathname.match(/^\/api\/credentials\/([\w-]+)$/);
	if (credProviderMatch && req.method === "GET") {
		const provider = credProviderMatch[1];
		if (!isProviderId(provider)) return Response.json({ error: "invalid provider" }, { status: 400 });
		const credentials = await loadCredentials();
		const cred = credentials[provider];
		if (!cred) return Response.json({ error: "credential not found" }, { status: 404 });
		return Response.json(cred, { headers: { "cache-control": "no-store" } });
	}
	if (url.pathname === "/api/credentials" && req.method === "POST") {
		const payload: unknown = await req.json();
		if (!isRecord(payload)) return Response.json({ error: "invalid payload" }, { status: 400 });
		let savedCount = 0;
		for (const [key, val] of Object.entries(payload)) {
			if (isProviderId(key) && isRecord(val)) {
				const access = readString(val.access) ?? readString(val.accessToken);
				const refresh = readString(val.refresh) ?? readString(val.refreshToken) ?? "";
				const expires = typeof val.expires === "number" ? val.expires : Date.now() + 3600 * 1000;
				if (access) {
					await saveCredential(key, {
						access,
						refresh,
						expires,
						email: readString(val.email),
						plan: readString(val.plan),
						projectId: readString(val.projectId) ?? readString(val.project_id),
						authorizedAt: typeof val.authorizedAt === "number" ? val.authorizedAt : Date.now(),
					});
					clearCooldown(key);
					savedCount++;
				}
			}
		}
		clearCooldown();
		return Response.json({ ok: true, saved: savedCount });
	}

	const loginMatch = url.pathname.match(/^\/api\/login\/([\w-]+)$/);
	if (loginMatch && req.method === "POST") {
		const provider = loginMatch[1];
		if (!isProviderId(provider)) return Response.json({ error: "invalid provider" }, { status: 400 });
		try {
			const { url: authUrl, completion } = await beginLogin(provider);
			logins.set(provider, { url: authUrl, status: "pending" });
			// Deliberately not awaited: the browser step gates completion.
			completion.then(
				() => logins.set(provider, { url: authUrl, status: "done" }),
				(error: unknown) =>
					logins.set(provider, {
						url: authUrl,
						status: "error",
						message: error instanceof Error ? error.message : String(error),
					}),
			);
			return Response.json({ url: authUrl });
		} catch (error) {
			return Response.json(
				{ error: error instanceof Error ? error.message : String(error) },
				{ status: 500 },
			);
		}
	}

	const codeMatch = url.pathname.match(/^\/api\/login\/([\w-]+)\/code$/);
	if (codeMatch && req.method === "POST") {
		const provider = codeMatch[1];
		if (!isProviderId(provider)) return Response.json({ error: "invalid provider" }, { status: 400 });
		const payload: unknown = await req.json();
		const code = isRecord(payload) ? readString(payload.code) ?? "" : "";
		try {
			await completeLoginWithCode(provider, code);
			logins.delete(provider);
			return Response.json({ ok: true });
		} catch (error) {
			return Response.json(
				{ error: error instanceof Error ? error.message : String(error) },
				{ status: 400 },
			);
		}
	}

	const logoutMatch = url.pathname.match(/^\/api\/logout\/([\w-]+)$/);
	if (logoutMatch && req.method === "POST") {
		const provider = logoutMatch[1];
		if (!isProviderId(provider)) return Response.json({ error: "invalid provider" }, { status: 400 });
		cancelLogin(provider);
		logins.delete(provider);
		await deleteCredential(provider);
		return Response.json({ ok: true });
	}

	return Response.json({ error: "not found" }, { status: 404 });
}

Bun.serve({
	hostname: "127.0.0.1",
	port: PORT,
	async fetch(req) {
		const url = new URL(req.url);
		if (url.pathname === "/") {
			return new Response(HTML, {
				headers: {
					"content-type": "text/html; charset=utf-8",
					"cache-control": "no-store",
					"set-cookie": `${DASHBOARD_COOKIE}=${encodeURIComponent(DASHBOARD_API_KEY)}; Path=/; HttpOnly; SameSite=Strict`,
				},
			});
		}
		if (url.pathname.startsWith("/api/")) {
			try {
				return await handleApi(req, url);
			} catch (error) {
				return Response.json(
					{ error: error instanceof Error ? error.message : String(error) },
					{ status: 500 },
				);
			}
		}
		return new Response("Not found", { status: 404 });
	},
});

/**
 * Proactive OAuth refresh sweep, mirroring omp's auth-broker refresher: every
 * REFRESH_INTERVAL_MS, renew any credential expiring within REFRESH_SKEW_MS.
 * Without it the dashboard only discovered a dead token when a usage fetch
 * failed — and that failure froze the provider's card on stale numbers.
 */
const REFRESH_INTERVAL_MS = 60_000;
const REFRESH_SKEW_MS = 5 * 60_000;
let sweeping = false;

async function refreshSweep(): Promise<void> {
	if (sweeping) return;
	sweeping = true;
	try {
		const credentials = await loadCredentials();
		const deadline = Date.now() + REFRESH_SKEW_MS;
		await Promise.all(
			Object.entries(credentials).map(async ([provider, credential]) => {
				if (!isProviderId(provider) || !credential?.refresh) return;
				// Unknown expiry stays for the lazy path in `ensureFresh`; sweeping it
				// every minute would hammer the token endpoint for no gain.
				if (typeof credential.expires !== "number" || !Number.isFinite(credential.expires)) return;
				if (credential.expires > deadline) return;
				try {
					await refreshCredential(provider, credential);
					console.log(`[refresh] ${provider}: token refreshed`);
				} catch (error) {
					const msg = error instanceof Error ? error.message : String(error);
					if (isDefinitiveOAuthFailure(error)) {
						console.warn(`[refresh] ${provider}: definitive failure, requires re-authentication: ${msg}`);
					} else {
						console.warn(`[refresh] ${provider}: transient failure, will retry on next sweep: ${msg}`);
					}
				}
			}),
		);
	} finally {
		sweeping = false;
	}
}

// Immediate startup sweep: pods mount initial credentials from seed secrets which
// may be expired; the initial read must refresh rather than serving dead tokens.
// TriRoute: the LiteLLM bridge owns refresh in single-host Docker deployments;
// OAUTH_SWEEP=off stops this process from spending rotating refresh tokens too.
if ((process.env.OAUTH_SWEEP ?? "on") !== "off") {
	void refreshSweep();
	setInterval(() => void refreshSweep(), REFRESH_INTERVAL_MS);
} else {
	console.log("[refresh] sweep disabled via OAUTH_SWEEP=off (LiteLLM bridge owns refresh)");
}

console.log(`Quota Dashboard running at http://localhost:${PORT}`);
