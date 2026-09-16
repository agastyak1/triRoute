#!/usr/bin/env bash
# TriRoute — autonomous, idempotent installer.
# Deploys an isolated gateway at ~/ai-gateway. NEVER edits ~/.claude, shell
# profiles, or anything outside ~/ai-gateway. OAuth is deliberately NOT run
# here: see docs/MANUAL_TESTS.md (browser logins are a human step by design).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${HOME}/ai-gateway"
LOG_DIR="${INSTALL_ROOT}/logs"

mkdir -p "$LOG_DIR" 2>/dev/null || true
exec > >(tee -a "${LOG_DIR}/install.log" 2>/dev/null || cat) 2>&1

log()  { echo -e "\033[1;34m[INFO]\033[0m $*"; }
pass() { echo -e "\033[1;32m[PASS]\033[0m $*"; }
fail() { echo -e "\033[1;31m[FAIL]\033[0m $*" >&2; exit 1; }

# ─── PHASE 1: PREFLIGHT (read-only) ─────────────────────────────────────────
log "Phase 1: preflight"

[[ "$(uname)" == "Darwin" ]] || fail "target OS must be macOS"
pass "OS: macOS ($(uname -m))"

command -v git      >/dev/null 2>&1 || fail "git is not installed (brew install git)"
command -v docker   >/dev/null 2>&1 || fail "docker CLI not found — install Docker Desktop"
docker info         >/dev/null 2>&1 || fail "docker daemon is not running — start Docker Desktop"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 is required"
command -v claude   >/dev/null 2>&1 || fail "Claude Code CLI not found (npm install -g @anthropic-ai/claude-code)"
command -v python3  >/dev/null 2>&1 || fail "python3 is required (auth helper + validation)"
command -v openssl  >/dev/null 2>&1 || fail "openssl is required (key generation)"
pass "git / docker / compose / claude / python3 / openssl present"

# ─── PHASE 2: ISOLATED RUNTIME ROOT ─────────────────────────────────────────
log "Phase 2: syncing runtime to ${INSTALL_ROOT}"

mkdir -p "$INSTALL_ROOT" "$INSTALL_ROOT/data" "$LOG_DIR"

# Copy project files, but NEVER overwrite live secrets or credentials.
rsync -a \
  --exclude 'data/' \
  --exclude 'logs/' \
  --exclude 'config/.env' \
  --exclude '.git/' \
  --exclude 'docs/plans/' \
  --exclude '.DS_Store' \
  "${REPO_DIR}/" "${INSTALL_ROOT}/"

chmod 700 "$INSTALL_ROOT/data" "$INSTALL_ROOT/config" 2>/dev/null || true
touch "$INSTALL_ROOT/data/.gitkeep"
pass "runtime tree synced (existing .env and credentials preserved)"

ENV_FILE="${INSTALL_ROOT}/config/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  [[ -f "${REPO_DIR}/.env.example" ]] || fail ".env.example missing"
  cp "${REPO_DIR}/.env.example" "$ENV_FILE"
  NEW_KEY="sk-local-$(openssl rand -hex 24)"
  if [[ "$(uname -m)" == "arm64" ]] || true; then
    # portable in-place update without sed -i platform differences
    python3 - "$ENV_FILE" "$NEW_KEY" <<'PYEOF'
import sys
path, key = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines(True)
out = [("LITELLM_MASTER_KEY=" + key + "\n") if l.startswith("LITELLM_MASTER_KEY=") else l for l in lines]
open(path, "w").writelines(out)
PYEOF
  fi
  chmod 600 "$ENV_FILE"
  pass "generated config/.env with fresh master key (chmod 600)"
fi

KEY="$(grep '^LITELLM_MASTER_KEY=' "$ENV_FILE" | cut -d '=' -f2- || true)"
[[ -n "$KEY" ]] || fail "LITELLM_MASTER_KEY empty in ${ENV_FILE}"
case "$KEY" in
  sk-change-me*|sk-quota-gateway*|sk-1234*) fail "LITELLM_MASTER_KEY is a known upstream default — delete ${ENV_FILE} and re-run" ;;
esac

# Port availability only matters if the stack is not already ours.
if ! docker inspect -f '{{.State.Running}}' triroute_litellm 2>/dev/null | grep -q true; then
  for port in 4000 3737; do
    if lsof -iTCP:"$port" -sTCP:LISTEN -P -n 2>/dev/null | grep -qv "127.0.0.1"; then
      fail "port $port is in use on a non-loopback interface — stop the other service first"
    elif lsof -iTCP:"$port" -sTCP:LISTEN -P -n >/dev/null 2>&1; then
      fail "port $port is already in use by another process"
    fi
  done
  pass "ports 4000/3737 available"
fi

# ─── PHASE 3: SERVICE DEPLOYMENT ────────────────────────────────────────────
log "Phase 3: building and starting containers (first run pulls ~1GB for LiteLLM)"

cd "$INSTALL_ROOT"
docker compose -p triroute build --if-not-exists dashboard >/dev/null 2>&1 || \
  docker compose -p triroute build dashboard
docker compose -p triroute up -d --remove-orphans

READY=0
for _ in $(seq 1 20); do
  if curl -sf http://127.0.0.1:4000/health/liveliness >/dev/null 2>&1; then READY=1; break; fi
  sleep 3
done
[[ $READY -eq 1 ]] || { docker compose -p triroute logs --tail 50 litellm; fail "litellm failed to become healthy"; }
pass "LiteLLM healthy on 127.0.0.1:4000 (single worker)"

lsof -iTCP:4000 -sTCP:LISTEN -P -n | grep -q "127.0.0.1" || fail "litellm escaped loopback binding"
pass "loopback confinement verified (127.0.0.1:4000)"

# Model discovery assertion
MODELS_JSON="$(curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:4000/v1/models)" || true
for alias in claude-opus claude-sonnet claude-haiku claude-gpt claude-gemini-pro claude-gemini-flash; do
  echo "$MODELS_JSON" | grep -q "$alias" || fail "discovery missing $alias"
done
pass "model discovery returns all gateway aliases"

# ─── PHASE 4: AUTH STATE (manual by design — agent never opens browsers) ────
log "Phase 4: subscription auth state"

CREDS="${INSTALL_ROOT}/data/credentials.json"
for pair in "anthropic:Claude Pro/Max" "openai-codex:ChatGPT Plus/Pro" "google-antigravity:Google AI Pro"; do
  provider="${pair%%:*}"; label="${pair##*:}"
  if [[ -f "$CREDS" ]] && python3 -c "import json,sys; sys.exit(0 if '$provider' in json.load(open('$CREDS')) else 1)" 2>/dev/null; then
    pass "authenticated: $label"
  else
    echo -e "\033[1;33m[MANUAL]\033[0m not authenticated: $label"
  fi
done

# ─── RESULT ─────────────────────────────────────────────────────────────────
cat <<EOF

TriRoute gateway installed and healthy.

  Launch Claude Code:   ${INSTALL_ROOT}/bin/claude-gw
  Health check:         ${INSTALL_ROOT}/scripts/health_check.sh
  Validation matrix:    python3 ${INSTALL_ROOT}/tests/validate.py
  Dashboard (optional): http://127.0.0.1:3737
  Stop / start:         docker compose -p triroute -f ${INSTALL_ROOT}/compose.yaml {down,up -d}

Next step: complete the OAuth logins listed above — see docs/MANUAL_TESTS.md.
EOF
