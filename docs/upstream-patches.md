# Local patches vs upstream

Vendored code: `vendor/tokengateway/` (MIT, Eduardo Bonassio). Diff intent, file by file:

## `litellm-plugin/sitecustomize.py` (forked to project root)
Upstream targets Kubernetes (token persistence = K8s Secret PATCH). For the
single-host Docker deployment this fork changes:

1. **Persistence** → `data/credentials.json`: inter-process lock
   (`credentials.json.lockdir` + `flock`), unique tmp files + fsync + atomic
   replace, chmod 600, corrupt-store refusal (never overwrite an unreadable
   store). Same JSON schema as the dashboard
   (`anthropic | openai-codex | google-antigravity` → `{access, refresh, expires(ms), projectId, ...}`)
   so all three components share one credential state.
2. **Race-safety** → refresh decisions re-check the file while holding the
   shared lock and adopt externally rotated tokens (dashboard login,
   auth_helper, or a previous process) instead of re-spending a dead refresh
   token. Re-auth merges per-provider entries so an omitted refresh token or
   project id never deletes the working one.
3. **Anthropic path** → LiteLLM now has native OAuth key support
   (`sk-ant-oat*` ⇒ `Authorization: Bearer` + oauth beta). The bridge keeps
   `os.environ` resolution working by shipping a **sentinel** key in
   litellm.yaml and swapping in the live token at header-build time
   (`get_anthropic_headers`, `optionally_handle_anthropic_oauth` + both import
   sites: count_tokens, messages passthrough). This gives native, unmodified
   Claude Code ↔ Anthropic Messages passthrough.
4. **thoughtSignature cache** → same in-memory dict, plus SQLite
   `data/session_cache.db` persistence (hardened plan §7) and a 24 h sweep,
   with a lock around shared-connection access and a commit on expiry sweep.
5. **Fail-closed boot** → `os._exit(78)` when `LITELLM_MASTER_KEY` is
   unset/short (< 32 chars)/whitespace/known-default, checked before any
   third-party import; bridge patch-setup failure also exits instead of
   running unpatched. Provider streams raise on malformed or unterminated
   SSE rather than returning partial successes, and the silent Gemini
   model-downgrade fallback is removed.

Wire translation logic (Codex Responses payloads, Antigravity envelopes,
usage normalization, spend logging) is **unchanged** and covered by upstream
contract tests in `tests/`.

## `dashboard/server.ts`
- `Bun.serve` binds `hostname: "127.0.0.1"` (upstream: any-interface default).
- Refresher honors `OAUTH_SWEEP=off` → bridge owns refresh; avoids two
  processes racing on single-use rotating tokens.
- API routes require `DASHBOARD_API_KEY` (Bearer header or same-origin
  HttpOnly session cookie); browser mutations also require the dashboard
  request header. Missing key refuses to start (exit 78).

## `dashboard/src/oauth.ts`
- PKCE callback listeners: `0.0.0.0` bind removed (127.0.0.1 + ::1 retained;
  `localhost` may resolve to either family).
- OAuth `state` is required on callback and pasted-code exchange.
- Token refresh is single-flight per provider.

## `dashboard/src/store.ts`
- Atomic unique-tmp writes, shared `.lockdir` with the Python bridge, merge
  (never drop a stored refresh/project id), corrupt-store refusal, K8s sync
  that checks HTTP status and clears removed credentials.

## `dashboard/src/ui.ts`, `dashboard/src/usage.ts`
- Escaped provider-supplied HTML, http(s)-only dashboard links, error-aware
  status responses, no fabricated quota numbers or hardcoded cluster specs.

## Not vendored (deliberately unused locally)
- `desktop/` (Tauri) — the dashboard's browser flow or `scripts/auth_helper.py`
  cover authentication without a desktop build.
- `deploy/kubernetes/` — out of scope for a local-only gateway.
