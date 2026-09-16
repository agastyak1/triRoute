# Manual tests & operations guide

Everything here runs on **your** machine, by **your** hand, deliberately:
the installer creates `~/ai-gateway/` and OAuth requires a real browser login.
Neither has been executed by the build agent beyond a single verified
install→uninstall cycle (state now: fully removed; `git log` proves the code paths).

Offline guarantees (already proven, see `tests/run_tests.sh`): wire contracts,
token race behavior, persistence, fail-closed key guard, loopback invariants.

---

## 0. Install (creates `~/ai-gateway`, touches nothing else)

```bash
cd TriRoute
./install.sh
```

Expected tail:
```
[PASS] LiteLLM healthy on 127.0.0.1:4000 (single worker)
[PASS] loopback confinement verified (127.0.0.1:4000)
[PASS] model discovery returns all gateway aliases
[MANUAL] not authenticated: <each provider you haven't logged in yet>
```

If Docker isn't running, `install.sh` fails at preflight before writing anything.

## 1. Authenticate subscriptions (pick ONE mechanism per provider)

### Option A — dashboard (recommended)
Open http://127.0.0.1:3737 → **Connect** per provider. Browser completes the
provider login; the dashboard catches the loopback callback and writes
`~/ai-gateway/data/credentials.json`. The bridge picks it up on the next
request — no restart needed.

### Option B — CLI
```bash
python3 ~/ai-gateway/scripts/auth_helper.py claude     # Claude Pro/Max
python3 ~/ai-gateway/scripts/auth_helper.py openai     # ChatGPT Plus/Pro
# Google only: set GOOGLE_CLIENT_SECRET= in ~/ai-gateway/config/.env first
python3 ~/ai-gateway/scripts/auth_helper.py google     # Google AI Pro
```
Each opens the browser once and listens 120s on the provider's whitelisted
loopback port (54545 / 1455 / 51121). Success prints
`success: <provider> authenticated (<email>) -> ...`.

> ⚠ If a browser page shows "connection refused" on the callback URL: the
> 120s listener expired or the port was busy — rerun the command.
> ⚠ Never run two login flows for the same provider concurrently; a spent
> one-time code just fails, but racing refresh tokens revokes the grant
> (worst case: log in again).

Verify without dumping secrets:
```bash
python3 -c "import json;d=json.load(open('$HOME/ai-gateway/data/credentials.json'));print({k:bool(v.get('refresh')) for k,v in d.items()})"
```

## 2. Gateway health

```bash
bash ~/ai-gateway/scripts/health_check.sh
```

## 3. End-to-end validation matrix

```bash
python3 ~/ai-gateway/tests/validate.py --require
```

Checks per authenticated provider: basic text → SSE streaming → forced tool
call (arg JSON validated) → multi-turn tool round-trip. Exit 0 only when
everything passes. Single provider: `--provider claude|gpt|gemini`.

## 4. The actual user story

```bash
~/ai-gateway/bin/claude-gw      # interactive Claude Code
```
- `/model` should list `claude-opus, claude-sonnet, claude-haiku, claude-gpt,
  claude-gpt-fast, claude-gemini-pro, claude-gemini-flash` under "From gateway".
- Run one real task per provider and **require a tool call** (e.g. "list files,
  then summarize this repo"). GPT + Gemini multi-turn tool use is the
  historically fragile part.
- Long session check: auto-compaction fires around 250k tokens
  (`CLAUDE_CODE_AUTO_COMPACT_WINDOW`), not before.

## 5. Provider-specific probes

| Provider | What to watch | Fallback if broken |
|---|---|---|
| claude-* | Native passthrough; thinking blocks + tool streams intact | check `docker compose -p triroute logs litellm` for oauth header errors → re-login |
| claude-gpt | Reasoning summary shows up; 2nd tool turn works | bridge logs `[TokenManager] codex token refreshed`; `invalid_grant` → re-auth |
| claude-gemini-* | `thoughtSignature` reuse across turns; 1M context | project id missing → re-run google auth (dashboard re-login also refreshes it) |

## 6. Teardown

```bash
~/TriRoute-source/uninstall.sh    # or: cd TriRoute && ./uninstall.sh
# add --purge-images to also delete the LiteLLM image (~1GB)
```
Removes exactly: the 2 containers and `~/ai-gateway`. Nothing else exists to remove.

## 7. Known accepted limitations

1. Subscription-terms risk is yours, not the code's — this uses first-party
   consumer OAuth surfaces; providers can break or disapprove it at any time.
2. Upstream internal model IDs (`gpt-5.6-terra/luna`, `claude-opus-4-7`, ...)
   rotate with provider releases; edit `config/litellm.yaml` + the bridge's
   `TRIROUTE_ALIAS_BACKEND` (a unit test fails if they drift) and re-run `./install.sh`.
3. `invalid_grant` after long downtime = refresh-token expiry → re-auth that provider.
4. LiteLLM version pin: `main-latest` tracks upstream fast; pin a tag in
   `compose.yaml` if a release breaks the bridge's monkey-patches.
