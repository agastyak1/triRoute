# TriRoute

One Claude Code. Three subscriptions. Zero API keys.

TriRoute is a **local, loopback-only LLM gateway** that lets [Claude Code](https://docs.anthropic.com/en/docs/claude-code) route to **Claude Pro/Max**, **ChatGPT Plus/Pro**, and **Google AI Pro** subscription backends through a single `/model` picker — no per-token API billing, no second CLI, no credential juggling.

```
                        ┌────────────────────────┐
                        │   claude-gw (wrapper)  │  ← isolated env, host config untouched
                        └───────────┬────────────┘
                                    │ POST /v1/messages (Anthropic format)
                                    ▼
        ┌──────────────────────────────────────────────────────┐
        │        LiteLLM Proxy  127.0.0.1:4000  (1 worker)     │
        │        + sitecustomize.py wire bridge                │
        │                                                      │
        │  claude-*        → Anthropic OAuth (native passthrough)
        │  claude-gpt*     → chatgpt.com Codex Responses API   │
        │  claude-gemini-* → Google Cloud Code (Antigravity)   │
        └───────────┬───────────────┬───────────────┬──────────┘
                    ▼               ▼               ▼
              Claude Pro/Max   ChatGPT Plus     Google AI Pro
              (OAuth PKCE)     (Codex OAuth)    (Cloud Code OAuth)
```

## What it is / isn't

- **Is**: a personal, single-user, macOS-local gateway. All ports bind `127.0.0.1` only. All secrets live in `~/ai-gateway` (chmod 600) and never enter this repo.
- **Isn't**: a production service, a multi-tenant gateway, or official API billing. Subscription OAuth surfaces are first-party CLI protocols; providers can change them at any time, and routing subscriptions through a gateway may conflict with provider terms. **Personal use at your own risk.**

## Architecture in one paragraph

Claude Code is launched via `~/ai-gateway/bin/claude-gw`, which injects `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` + gateway master key into a **subshell only** — your `~/.claude/settings.json`, dotfiles, and normal `claude` usage are never modified. LiteLLM serves `/v1/models` so Claude Code's gateway model discovery populates `/model` with every `claude-*` alias. A `sitecustomize.py` bridge (forked from [TokenGateway](https://github.com/eduardopessin/tokengateway), MIT) performs per-request OAuth refresh (atomic + `flock`, race-safe against single-use rotating refresh tokens) and wire translation: Anthropic traffic passes through natively; GPT traffic is translated to the OpenAI **Responses** API on the Codex endpoint; Gemini traffic to Google's internal **Cloud Code** API, preserving `thoughtSignature` for multi-turn tool state.

## Model aliases

| Alias | Backend | Wire translation | Notes |
|---|---|---|---|
| `claude-opus` / `claude-sonnet` / `claude-haiku` | Claude Pro/Max | none (native passthrough + OAuth headers) | |
| `claude-gpt` | ChatGPT Plus/Pro | Anthropic ↔ OpenAI Responses | medium reasoning |
| `claude-gpt-fast` | ChatGPT Plus/Pro | Anthropic ↔ OpenAI Responses | low reasoning |
| `claude-gemini-pro` / `-flash` | Google AI Pro | Anthropic ↔ Cloud Code SSE | `thoughtSignature` preserved |

Aliases deliberately start with `claude-` so Claude Code's discovery filter shows them all.

## Quickstart

```bash
git clone <this repo> && cd TriRoute
bash tests/run_tests.sh                       # offline unit/behavior suite (no docker, no network)
./install.sh                                  # preflight, deploy ~/ai-gateway, start stack
# authenticate your subscriptions (browser logins — human step by design):
python3 ~/ai-gateway/scripts/auth_helper.py claude
python3 ~/ai-gateway/scripts/auth_helper.py openai
# google needs GOOGLE_CLIENT_SECRET in ~/ai-gateway/config/.env first:
python3 ~/ai-gateway/scripts/auth_helper.py google
# or simply open http://127.0.0.1:3737 and click Connect for each provider
python3 ~/ai-gateway/tests/validate.py        # full validation matrix
~/ai-gateway/bin/claude-gw                    # then: /model
```

Full walkthrough with troubleshooting: **[docs/MANUAL_TESTS.md](docs/MANUAL_TESTS.md)**.

## Layout

```
TriRoute/
├── install.sh / uninstall.sh   # idempotent deploy to ~/ai-gateway
├── compose.yaml                # loopback-only containers
├── config/litellm.yaml         # alias catalog + routing
├── litellm-plugin/             # wire bridge (TokenGateway fork, see docs/upstream-patches.md)
├── bin/claude-gw               # isolated launcher — no host config mutation
├── scripts/                    # auth_helper (PKCE), start/stop/health
├── tests/                      # wire-contract unit tests + e2e validation matrix
├── vendor/tokengateway/        # upstream MIT code (dashboard) + minimal local patches
└── docs/                       # architecture, plans, manual test guide
```

Runtime state (`config/.env`, `data/credentials.json`, `data/session_cache.db`, logs) lives only in `~/ai-gateway/`, is gitignored by design, and is removed wholesale by `uninstall.sh`.

## Operational commands

```bash
docker compose -p triroute -f ~/ai-gateway/compose.yaml up -d      # start
docker compose -p triroute -f ~/ai-gateway/compose.yaml down       # stop
bash ~/ai-gateway/scripts/health_check.sh                          # fast diagnosis
docker compose -p triroute -f ~/ai-gateway/compose.yaml logs -f litellm
```

## Hard invariants (enforced)

1. `127.0.0.1` only — compose port bindings, dashboard server, PKCE listeners, and the install-time `lsof` audit.
2. Single LiteLLM worker — prevents cross-process token-refresh races on rotating refresh tokens.
3. Fail-closed master key — the bridge kills the proxy at boot if `LITELLM_MASTER_KEY` is missing/short/known-default (TG-004).
4. Atomic credential writes — temp file + `os.replace` + `flock` + `chmod 600`.
5. Zero host-config mutation — nothing outside `~/ai-gateway` is ever written by this project.

## Credits & license

Wire bridge and dashboard OAuth implementation forked from [TokenGateway](https://github.com/eduardopessin/tokengateway) (MIT, Eduardo Bonassio); wire transformers reference [oh-my-pi](https://github.com/can1357/oh-my-pi). All local deviations are documented in [docs/upstream-patches.md](docs/upstream-patches.md). This project: MIT.
