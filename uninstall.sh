#!/usr/bin/env bash
# Remove the TriRoute runtime: containers, images (optional) and ~/ai-gateway.
# Never touches anything outside ~/ai-gateway.
set -euo pipefail

INSTALL_ROOT="${HOME}/ai-gateway"

if [[ -d "$INSTALL_ROOT" ]]; then
  cd "$INSTALL_ROOT"
  docker compose -p triroute down --remove-orphans 2>/dev/null || true
  rm -rf "$INSTALL_ROOT"
  echo "removed ${INSTALL_ROOT} and stopped containers"
else
  docker rm -f triroute_litellm triroute_dashboard 2>/dev/null || true
  echo "no ${INSTALL_ROOT} found; stale containers (if any) removed"
fi

if [[ "${1:-}" == "--purge-images" ]]; then
  docker rmi ghcr.io/berriai/litellm:main-latest triroute-dashboard 2>/dev/null || true
  echo "gateway images purged"
fi
