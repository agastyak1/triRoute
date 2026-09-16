# TriRoute architecture (implementation notes)

Formal plans: [plans/hardened-build-plan.md](plans/hardened-build-plan.md) (canonical) and
[plans/original-build-plan.md](plans/original-build-plan.md) (background + rationale).
This file records only what the hardened plan left open, plus request-path details.

## Request path: /v1/messages → providers

```
Claude Code ──POST /v1/messages──▶ LiteLLM proxy (workers=1, master key auth)
  │
  ├─ model=claude-{opus,sonnet,haiku}
  │    └─ Native Anthropic messages passthrough (no schema translation).
  │       Auth: litellm.yaml resolves os.environ/ANTHROPIC_OAUTH_TOKEN once →
  │       the sentinel "sk-ant-oat-triroute-managed". At header-build time the
  │       bridge swaps it for a live OAuth token (Bearer + oauth beta are then
  │       handled by litellm's native OAuth key support).
  │       /v1/messages/count_tokens goes through the same swap.
  │
  ├─ model=claude-gpt{,-fast}
  │    └─ litellm messages-adapter converts Anthropic→OpenAI shape → Router.acompletion
  │       → bridge intercepts ("gpt-5" substring) → builds a Responses-API payload
  │       (input items: message / function_call / function_call_output, orphan-pair
  │       repair) → chatgpt.com/backend-api/codex/responses SSE → re-emits
  │       ModelResponseStream chunks → adapter converts back to Anthropic SSE.
  │
  └─ model=claude-gemini-{pro,flash}
       └─ same adapter path → bridge intercepts ("gemini") → v1internal
          streamGenerateContent (Antigravity envelope, project id from credentials,
          parametersJsonSchema tools) → SSE → Anthropic SSE. functionCall parts
          carry thoughtSignature; signatures cached in data/session_cache.db keyed
          by call_id so turn 2+ re-attaches them across proxy restarts.
```

## Token lifecycle (the failure mode that shapes everything)

Anthropic and OpenAI rotate **single-use** refresh tokens. Two processes spending
the same refresh token = `invalid_grant` = re-login. Serialization layers, in
order of strength:

1. Single uvicorn worker (no multi-process writer inside the proxy).
2. `threading.Lock()` around refresh decisions (in-process).
3. mtime-triggered `reload()` of `data/credentials.json` before deciding to
   refresh — adopted rotation wins over re-spending the stored token.
4. Cross-process `flock` on `credentials.json.lock` around every read-modify-
   write; writes are tmp+fsync+`os.replace` with `chmod 600`.
5. Dashboard's periodic refresher is disabled (`OAUTH_SWEEP=off`); the bridge
   is the sole automatic refresher. Dashboard logins (browser flow) still write
   through the same file and are picked up via (3).

## Why the launcher wrapper exists

`~/.claude/settings.json` is the standard place for `ANTHROPIC_BASE_URL`, but
editing it would break the user's non-gateway Claude Code usage and violate the
zero-mutation invariant. `bin/claude-gw` injects the same variables into one
process tree: `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` (master key),
`CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW=250000`,
and `unset ANTHROPIC_API_KEY/CLAUDE_API_KEY` to prevent bypass.

## Threat model

- Attacker on LAN / internet: no surface (every listener is 127.0.0.1; verified by `lsof` audit at install and in health_check).
- Another local user: can reach loopback services if same account (accepted; macOS single-user assumption).
- Repo leakage: `.env`, `data/`, `logs/` are gitignored; runtime lives outside the repo entirely.
- Upstream fallback keys (TG-004): startup aborts if master key is a known default or < 16 chars.
- Subscription ToS: the honest risk — first-party consumer OAuth surfaces accessed by non-first-party software. Local single-user use only.
