#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
docker compose -p triroute up -d --remove-orphans
exec "$ROOT/scripts/health_check.sh"
