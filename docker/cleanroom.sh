#!/usr/bin/env bash
# Prove a service builds and boots from a clean container with no outbound network, then
# optionally serve it on a local port for acceptance tests.
#
#   docker/cleanroom.sh <service-dir> [--port N] [--health PATH] [--timeout S] [--serve]
#
# 1. Pulls only the base images named in <service-dir>/Dockerfile (graders have those).
# 2. Builds with --network=none: any download during the build fails here.
# 3. Boots the image with --network=none and probes the health path from a sidecar that
#    shares the container's network namespace (loopback only).
# 4. With --serve, starts it again publishing the port on 127.0.0.1 and prints, on stdout:
#      CLEANROOM_CONTAINER=<name>   CLEANROOM_BASE_URL=<url>
#    The caller stops it with: <cli> rm -f <name>
#
# Exit 0 only if every step passed. Uses docker when its daemon answers, else podman
# (override with CONTAINER_CLI).
set -euo pipefail

dir="" port="${SERVICE_PORT:-8080}" health="${HEALTH_PATH:-/}" timeout="${BOOT_TIMEOUT:-60}" serve=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --health) health="$2"; shift 2 ;;
    --timeout) timeout="$2"; shift 2 ;;
    --serve) serve=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) [[ -z "$dir" ]] && { dir="$1"; shift; } || { echo "unknown argument: $1" >&2; exit 2; } ;;
  esac
done
[[ -n "$dir" && -f "$dir/Dockerfile" ]] || { echo "cleanroom: no Dockerfile in '${dir:-<missing>}'" >&2; exit 2; }

if [[ -n "${CONTAINER_CLI:-}" ]]; then cli="$CONTAINER_CLI"
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then cli=docker
elif command -v podman >/dev/null 2>&1; then cli=podman
else echo "cleanroom: no container runtime" >&2; exit 2; fi

probe_image="docker.io/library/busybox:stable"
tag="factory-cleanroom-$(basename "$(cd "$dir" && pwd)" | tr -c 'a-z0-9-\n' '-')-$$"
name="${tag}-boot"
log() { printf '[cleanroom] %s\n' "$*" >&2; }
cleanup() { "$cli" kill "$name" >/dev/null 2>&1 || true; "$cli" rm -f "$name" >/dev/null 2>&1 || true; }
trap 'rc=$?; cleanup; exit $rc' EXIT

log "runtime: $cli"
# Base images: every FROM that is not an earlier build stage or scratch.
mapfile -t bases < <(awk 'toupper($1)=="FROM" {
    img=""; for (i=2;i<=NF;i++) if ($i !~ /^--/) { img=$i; break }
    for (j=i+1;j<=NF;j++) if (toupper($j)=="AS") stages[tolower($(j+1))]=1
    if (img!="" && !(tolower(img) in stages) && img!="scratch") print img }' "$dir/Dockerfile" | sort -u)
for img in "${bases[@]}" "$probe_image"; do
  log "pull $img"
  "$cli" pull -q "$img" >/dev/null
done

log "build --network=none"
if ! "$cli" build --network=none -q -t "$tag" "$dir" >/dev/null; then
  log "FAIL: the build needs something it cannot find offline"
  exit 1
fi

log "boot --network=none, probe http://127.0.0.1:$port$health"
"$cli" run -d --name "$name" --network=none -e PORT="$port" "$tag" >/dev/null
ok=0
for ((i = 0; i < timeout; i++)); do
  if [[ "$("$cli" inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]]; then
    log "FAIL: the container exited during boot"; "$cli" logs --tail 40 "$name" >&2 || true; exit 1
  fi
  if "$cli" run --rm --network "container:$name" "$probe_image" \
       wget -q -O /dev/null -T 2 "http://127.0.0.1:$port$health" >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 1
done
if [[ $ok -ne 1 ]]; then
  log "FAIL: no answer on $health within ${timeout}s"; "$cli" logs --tail 40 "$name" >&2 || true; exit 1
fi
log "PASS: built offline and answered offline after ${i}s"
cleanup

if [[ $serve -eq 1 ]]; then
  host_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
  served="${tag}-serve"
  "$cli" run -d --name "$served" -e PORT="$port" -p "127.0.0.1:$host_port:$port" "$tag" >/dev/null
  for ((i = 0; i < timeout; i++)); do
    python3 - "$host_port" "$health" <<'PY' && break
import sys, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}{sys.argv[2]}", timeout=2)
except urllib.error.HTTPError:
    pass
except Exception:
    sys.exit(1)
PY
    sleep 1
  done
  echo "CLEANROOM_CONTAINER=$served"
  echo "CLEANROOM_BASE_URL=http://127.0.0.1:$host_port"
  echo "CLEANROOM_CLI=$cli"
fi
