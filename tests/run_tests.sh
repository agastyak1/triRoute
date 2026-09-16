#!/usr/bin/env bash
# TriRoute test runner — offline by design: no network, no Docker, no host writes.
# Pure stdlib Python; tests temp dirs live under the system temp only.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
shim="tests/__pycache__"
export PYTHONDONTWRITEBYTECODE=1

status=0
for t in tests/test_*.py; do
  echo "── $t"
  if ! python3 "$t"; then
    status=1
  fi
done
echo
if [[ $status -eq 0 ]]; then
  echo "ALL TESTS PASS"
else
  echo "TEST FAILURES" >&2
fi
exit $status
