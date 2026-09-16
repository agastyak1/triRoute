#!/usr/bin/env bash
# Remove the TriRoute runtime: containers, images (optional) and ~/ai-gateway.
# Never touches anything outside ~/ai-gateway.
set -euo pipefail

INSTALL_ROOT="${HOME}/ai-gateway"

if [[ -d "$INSTALL_ROOT" ]]; then
  cd "$INSTALL_ROOT"
  command -v docker >/dev/null 2>&1 || { echo "error: docker is required to stop the gateway" >&2; exit 1; }
  if ! docker compose -p triroute down --remove-orphans; then
    echo "error: could not stop the TriRoute containers; runtime was left intact" >&2
    exit 1
  fi
  rm -rf "$INSTALL_ROOT"
  echo "removed ${INSTALL_ROOT} and stopped containers"
else
  echo "no ${INSTALL_ROOT} found; no containers were removed"
fi

if [[ "${1:-}" == "--purge-images" ]]; then
  command -v docker >/dev/null 2>&1 || { echo "error: docker is required to purge images" >&2; exit 1; }
  if docker rmi ghcr.io/berriai/litellm:main-latest triroute-dashboard 2>/dev/null; then
    echo "gateway images purged"
  else
    echo "warning: one or more gateway images were already absent" >&2
  fi
fi
