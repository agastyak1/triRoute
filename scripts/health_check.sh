#!/usr/bin/env bash
# Gateway validation: loopback confinement, health, auth state, model discovery.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/config/.env"
CREDS_FILE="${ROOT}/data/credentials.json"

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

[[ -f "$ENV_FILE" ]] || fail "missing ${ENV_FILE}"
KEY="$(grep '^LITELLM_MASTER_KEY=' "$ENV_FILE" | cut -d '=' -f2-)"
[[ -n "$KEY" ]] || fail "empty master key"
[[ ${#KEY} -ge 32 ]] || fail "master key is too short"

assert_loopback() {
  local port="$1"
  local listeners names found
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
}

assert_loopback 4000
pass "litellm bound to loopback only"

curl -fsS http://127.0.0.1:4000/health/liveliness >/dev/null || fail "litellm health check failed"
pass "litellm healthy on 127.0.0.1:4000"

assert_loopback 3737
pass "dashboard bound to loopback only"

MODELS="$(curl -fsS -H "Authorization: Bearer $KEY" http://127.0.0.1:4000/v1/models)" || fail "model discovery request failed"
for alias in claude-opus claude-sonnet claude-haiku claude-gpt claude-gemini-pro claude-gemini-flash; do
  python3 -c 'import json,sys; data=json.load(sys.stdin); ids={item.get("id") for item in data.get("data", [])}; sys.exit(0 if sys.argv[1] in ids else 1)' "$alias" <<< "$MODELS" || fail "discovery missing alias: $alias"
done
pass "model discovery exposes all claude-* aliases"

for p in anthropic openai-codex google-antigravity; do
  if python3 - "$CREDS_FILE" "$p" <<'PYEOF'
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
    pass "credential present: $p"
  else
    echo "[MANUAL] credential missing: $p — run scripts/auth_helper.py (see docs/MANUAL_TESTS.md)"
  fi
done
pass "health_check complete"
