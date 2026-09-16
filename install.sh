#!/usr/bin/env bash
# TriRoute — autonomous, idempotent installer.
# Deploys an isolated gateway at ~/ai-gateway. NEVER edits ~/.claude, shell
# profiles, or anything outside ~/ai-gateway. OAuth is deliberately NOT run
# here: see docs/MANUAL_TESTS.md (browser logins are a human step by design).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${HOME}/ai-gateway"
LOG_DIR="${INSTALL_ROOT}/logs"

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
command -v lsof    >/dev/null 2>&1 || fail "lsof is required (loopback audit)"
pass "git / docker / compose / claude / python3 / openssl present"

# Prevent two installers from racing while they copy the runtime or recreate
# the Compose stack. The lock lives inside the isolated runtime root.
mkdir -p "$INSTALL_ROOT"
LOCK_DIR="${INSTALL_ROOT}/.install.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  fail "another TriRoute install is already running (${LOCK_DIR})"
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

mkdir -p "$LOG_DIR" || fail "cannot create ${LOG_DIR}"
exec > >(tee -a "${LOG_DIR}/install.log") 2>&1

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

chmod 700 "$INSTALL_ROOT/data" "$INSTALL_ROOT/config"
touch "$INSTALL_ROOT/data/.gitkeep"
pass "runtime tree synced (existing .env and credentials preserved)"

ENV_FILE="${INSTALL_ROOT}/config/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  [[ -f "${REPO_DIR}/.env.example" ]] || fail ".env.example missing"
  cp "${REPO_DIR}/.env.example" "$ENV_FILE"
  NEW_KEY="sk-local-$(openssl rand -hex 24)"
  DASHBOARD_KEY="$(openssl rand -hex 32)"
  python3 - "$ENV_FILE" "$NEW_KEY" "$DASHBOARD_KEY" <<'PYEOF'
import sys
import os
import tempfile

path, key, dashboard_key = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path).read().splitlines(True)
seen_dashboard = False
out = []
for line in lines:
    if line.startswith("LITELLM_MASTER_KEY="):
        out.append("LITELLM_MASTER_KEY=" + key + "\n")
    elif line.startswith("DASHBOARD_API_KEY="):
        out.append("DASHBOARD_API_KEY=" + dashboard_key + "\n")
        seen_dashboard = True
    else:
        out.append(line)
if not seen_dashboard:
    out.append("DASHBOARD_API_KEY=" + dashboard_key + "\n")
directory = os.path.dirname(path) or "."
fd, tmp = tempfile.mkstemp(prefix=".env.", dir=directory)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        fd = None
        fh.writelines(out)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
finally:
    if fd is not None:
        os.close(fd)
    if os.path.exists(tmp):
        os.unlink(tmp)
PYEOF
  pass "generated config/.env with fresh master key (chmod 600)"
fi

# Upgrade an older installation that predates dashboard authentication.
DASHBOARD_KEY="$(grep '^DASHBOARD_API_KEY=' "$ENV_FILE" | cut -d '=' -f2- || true)"
if [[ -z "$DASHBOARD_KEY" ]]; then
  DASHBOARD_KEY="$(openssl rand -hex 32)"
  python3 - "$ENV_FILE" "$DASHBOARD_KEY" <<'PYEOF'
import os
import sys
import tempfile

path, key = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    lines = fh.read().splitlines(True)
replaced = False
out = []
for line in lines:
    if line.startswith("DASHBOARD_API_KEY="):
        out.append("DASHBOARD_API_KEY=" + key + "\n")
        replaced = True
    else:
        out.append(line)
if not replaced:
    out.append("DASHBOARD_API_KEY=" + key + "\n")
directory = os.path.dirname(path) or "."
fd, tmp = tempfile.mkstemp(prefix=".env.", dir=directory)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        fd = None
        fh.writelines(out)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
finally:
    if fd is not None:
        os.close(fd)
    if os.path.exists(tmp):
        os.unlink(tmp)
PYEOF
fi
[[ ${#DASHBOARD_KEY} -ge 32 ]] || fail "DASHBOARD_API_KEY must be at least 32 characters"
chmod 600 "$ENV_FILE"

KEY="$(grep '^LITELLM_MASTER_KEY=' "$ENV_FILE" | cut -d '=' -f2- || true)"
[[ -n "$KEY" ]] || fail "LITELLM_MASTER_KEY empty in ${ENV_FILE}"
[[ ${#KEY} -ge 32 ]] || fail "LITELLM_MASTER_KEY must be at least 32 characters"
[[ "$KEY" != *[[:space:]]* ]] || fail "LITELLM_MASTER_KEY must not contain whitespace"
case "$KEY" in
  sk-change-me*|sk-quota-gateway*|sk-1234*) fail "LITELLM_MASTER_KEY is a known upstream default — delete ${ENV_FILE} and re-run" ;;
esac

# Port availability only matters if the stack is not already ours.
if ! docker inspect -f '{{.State.Running}}' triroute_litellm 2>/dev/null | grep -q true; then
  for port in 4000 3737; do
    if lsof -iTCP:"$port" -sTCP:LISTEN -P -n >/dev/null 2>&1; then
      fail "port $port is already in use by another process"
    fi
  done
  pass "ports 4000/3737 available"
fi

# ─── PHASE 3: SERVICE DEPLOYMENT ────────────────────────────────────────────
log "Phase 3: building and starting containers (first run pulls ~1GB for LiteLLM)"

cd "$INSTALL_ROOT"
docker compose -p triroute build dashboard
docker compose -p triroute up -d --remove-orphans

READY=0
for _ in $(seq 1 20); do
  if curl -sf http://127.0.0.1:4000/health/liveliness >/dev/null 2>&1; then READY=1; break; fi
  sleep 3
done
[[ $READY -eq 1 ]] || { docker compose -p triroute logs --tail 50 litellm; fail "litellm failed to become healthy"; }
pass "LiteLLM healthy on 127.0.0.1:4000 (single worker)"

for port in 4000 3737; do
  listeners="$(lsof -iTCP:"$port" -sTCP:LISTEN -P -n -F n 2>/dev/null || true)"
  names="$(printf '%s\n' "$listeners" | sed -n 's/^n//p')"
  [[ -n "$names" ]] || fail "no listener found for 127.0.0.1:${port}"
  found=0
  while IFS= read -r listener; do
    [[ -n "$listener" ]] || continue
    case "$listener" in
      127.0.0.1:"$port"|\[::1\]:"$port") found=1 ;;
      *) fail "port ${port} escaped loopback binding (${listener})" ;;
    esac
  done <<< "$names"
  [[ "$found" -eq 1 ]] || fail "no loopback listener found for 127.0.0.1:${port}"
done
pass "loopback confinement verified (127.0.0.1:4000 and 3737)"

# Model discovery assertion
MODELS_JSON="$(curl -fsS -H "Authorization: Bearer $KEY" http://127.0.0.1:4000/v1/models)" || fail "model discovery request failed"
for alias in claude-opus claude-sonnet claude-haiku claude-gpt claude-gemini-pro claude-gemini-flash; do
  python3 -c 'import json,sys; data=json.load(sys.stdin); ids={item.get("id") for item in data.get("data", [])}; sys.exit(0 if sys.argv[1] in ids else 1)' "$alias" <<< "$MODELS_JSON" || fail "discovery missing $alias"
done
pass "model discovery returns all gateway aliases"

# ─── PHASE 4: AUTH STATE (manual by design — agent never opens browsers) ────
log "Phase 4: subscription auth state"

CREDS="${INSTALL_ROOT}/data/credentials.json"
for pair in "anthropic:Claude Pro/Max" "openai-codex:ChatGPT Plus/Pro" "google-antigravity:Google AI Pro"; do
  provider="${pair%%:*}"; label="${pair##*:}"
  if [[ -f "$CREDS" ]] && python3 - "$CREDS" "$provider" <<'PYEOF'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
    entry = data.get(sys.argv[2], {}) if isinstance(data, dict) else {}
    sys.exit(0 if isinstance(entry, dict) and entry.get("access") else 1)
except (OSError, ValueError):
    sys.exit(1)
PYEOF
  then
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
