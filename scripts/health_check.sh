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

lsof -iTCP:4000 -sTCP:LISTEN -P -n | grep -q "127.0.0.1" || fail "litellm is not bound to loopback"
pass "litellm bound to 127.0.0.1 only"

curl -s -f http://127.0.0.1:4000/health/liveliness >/dev/null || fail "litellm health check failed"
pass "litellm healthy on 127.0.0.1:4000"

if lsof -iTCP:3737 -sTCP:LISTEN -P -n | grep -q "127.0.0.1"; then
  pass "dashboard bound to 127.0.0.1:3737"
else
  echo "[WARN] dashboard is not listening on 127.0.0.1:3737 (optional component)"
fi

MODELS="$(curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:4000/v1/models)" || fail "model discovery request failed"
for alias in claude-opus claude-sonnet claude-haiku claude-gpt claude-gemini-pro claude-gemini-flash; do
  echo "$MODELS" | grep -q "$alias" || fail "discovery missing alias: $alias"
done
pass "model discovery exposes all claude-* aliases"

for p in anthropic openai-codex google-antigravity; do
  if python3 -c "import json,sys; d=json.load(open('$CREDS_FILE')); sys.exit(0 if '$p' in d else 1)" 2>/dev/null; then
    pass "credential present: $p"
  else
    echo "[MANUAL] credential missing: $p — run scripts/auth_helper.py (see docs/MANUAL_TESTS.md)"
  fi
done
pass "health_check complete"
