# TriRoute — build tracker

## Status legend
[x] done · [ ] open · [M] manual step (human OAuth)

## Plan execution

- [x] Repo scaffold: structure, README, .gitignore, docs/, private remote
- [x] Upstream research: TokenGateway bridge + real OAuth client IDs verified against source
- [x] compose.yaml — loopback-only, single worker, pinned healthcheck, flock-shared data dir
- [x] config/litellm.yaml — 7 aliases, windows per hardened plan §3/§8
- [x] Wire bridge fork (`litellm-plugin/sitecustomize.py`)
  - [x] credentials.json persistence (atomic + flock), replaces K8s secret sync
  - [x] external-rotation adoption (mtime sync) before any refresh → no invalid_grant races
  - [x] Anthropic OAuth sentinel swap for native /v1/messages + count_tokens paths
  - [x] Codex Responses bridge + reasoning state (inherited, contract-tested)
  - [x] Cloud Code bridge + thoughtSignature → SQLite `session_cache.db` persistence
  - [x] fail-closed LITELLM_MASTER_KEY guard (TG-004)
  - [x] upstream contract tests green (4 files, run in CI-less loop)
- [x] scripts/auth_helper.py — real client IDs, PKCE (S256), Google no-PKCE+secret, 120 s loopback listener, dashboard-compatible schema
- [x] bin/claude-gw — isolated subshell launcher (no ~/.claude, no dotfiles)
- [x] install.sh / uninstall.sh — idempotent, phases per plan §11
- [x] scripts/start.sh / stop.sh / health_check.sh
- [x] tests/validate.py — matrix: health, discovery, text, streaming, tool call, multi-turn per provider
- [x] Vendored dashboard patches: 127.0.0.1 bind, OAUTH_SWEEP ownership, PKCE listener 0.0.0.0 removal
- [x] docs: architecture, upstream-patches, plans
- [ ] Build verification run: install.sh on this machine (containers up, health, discovery, bridge logs)
- [ ] Negative test: insecure master key aborts container
- [ ] docs/MANUAL_TESTS.md — OAuth walkthrough + matrix instructions [M] for user execution
- [ ] User OAuth: claude / openai / google [M]
- [ ] Full validation matrix pass [M, then fix any bridge fallout]
- [ ] Final security audit + README polish + closeout commit

## Known deviations from the hardened plan (all deliberate)
1. Plan §8 uses `chatgpt/` + `google-cloudcode/` LiteLLM providers — these do not exist in the pinned image; bridge intercepts on model-substring matching instead (upstream-proven pattern). Same aliases, same UX.
2. Plan §10 auth_helper client IDs were placeholders (`codex-cli-client-id`, wrong Anthropic UUID, fake Google ID, PKCE on Google) — replaced with the real first-party client registrations from upstream source (Google's Antigravity client explicitly must NOT send PKCE).
3. Plan §11 installer auto-launches OAuth browsers — removed per operator instruction; auth is a documented manual step.
4. Bubble Tea TUI (original plan §57) not adopted — hardened plan supersedes it with plain phase output; identical preflight/auth/install/test behavior.
5. thoughtSignature cache uses SQLite (plan §7) but keeps the in-memory dict as hot path.
