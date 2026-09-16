# Local patches vs upstream

Vendored code: `vendor/tokengateway/` (MIT, Eduardo Bonassio). Diff intent, file by file:

## `litellm-plugin/sitecustomize.py` (forked to project root)
Upstream targets Kubernetes (token persistence = K8s Secret PATCH). For the
single-host Docker deployment this fork changes:

1. **Persistence** → `data/credentials.json`: flock-guarded read-modify-write,
   tmp+fsync+atomic replace, chmod 600. Same JSON schema as the dashboard
   (`anthropic | openai-codex | google-antigravity` → `{access, refresh, expires(ms), projectId, ...}`)
   so all three components share one credential state.
2. **Race-safety** → before *any* refresh the manager re-checks the file
   (mtime) and adopts externally rotated tokens (dashboard login, auth_helper,
   or a previous process) instead of re-spending a dead refresh token.
3. **Anthropic path** → LiteLLM now has native OAuth key support
   (`sk-ant-oat*` ⇒ `Authorization: Bearer` + oauth beta). The bridge keeps
   `os.environ` resolution working by shipping a **sentinel** key in
   litellm.yaml and swapping in the live token at header-build time
   (`get_anthropic_headers`, `optionally_handle_anthropic_oauth` + both import
   sites: count_tokens, messages passthrough). This gives native, unmodified
   Claude Code ↔ Anthropic Messages passthrough.
4. **thoughtSignature cache** → same in-memory dict, plus SQLite
   `data/session_cache.db` persistence (hardened plan §7) and a 24 h sweep.
5. **Fail-closed boot** → `os._exit(78)` when `LITELLM_MASTER_KEY` is
   unset/short/known-default.

Wire translation logic (Codex Responses payloads, Antigravity envelopes,
usage normalization, spend logging) is **unchanged** and covered by upstream
contract tests in `tests/`.

## `dashboard/server.ts`
- `Bun.serve` binds `hostname: "127.0.0.1"` (upstream: any-interface default).
- Refresher honors `OAUTH_SWEEP=off` → bridge owns refresh; avoids two
  processes racing on single-use rotating tokens.

## `dashboard/src/oauth.ts`
- PKCE callback listeners: `0.0.0.0` bind removed (127.0.0.1 + ::1 retained;
  `localhost` may resolve to either family).

## Not vendored (deliberately unused locally)
- `desktop/` (Tauri) — the dashboard's browser flow or `scripts/auth_helper.py`
  cover authentication without a desktop build.
- `deploy/kubernetes/` — out of scope for a local-only gateway.
