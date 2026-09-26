#!/usr/bin/env bash
# Verifier probes for stage-1, against a fresh clone of <commit>.
#
#   reviews/stage-1/probes/run.sh <commit> [pytest args...]
#
# Builds stage-1/Dockerfile from the clone, starts two containers under the spec limits
# (2 vCPU / 2 GiB): A with -e PORT=9123 mapped to a free host port, B with no PORT (default 8080)
# used as the untouched import target. Measures time to first healthy response, runs
# test_probes.py, prints a summary and removes everything it created.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
commit="${1:?usage: run.sh <commit> [pytest args]}"; shift
py=/home/geekendzone/hackathon/dark-factory-wearedevs/.venv/bin/python
clean_env=(env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH)

sha="$(git -C "$repo" rev-parse --verify "$commit^{commit}")" || exit 2
tag="verifier-probe:${sha:0:12}"
clone="$(mktemp -d /tmp/verifier-probe-XXXX)"
a="vp-a-${sha:0:8}-$$"; b="vp-b-${sha:0:8}-$$"
cleanup() { docker rm -f "$a" "$b" >/dev/null 2>&1; rm -rf "$clone"; }
trap cleanup EXIT

git clone -q --no-local "$repo" "$clone" && git -C "$clone" checkout -q --detach "$sha" || exit 2
echo "== build $tag"
docker build -q -t "$tag" "$clone/stage-1" || { echo "BUILD FAILED"; exit 3; }

free_port() { "${clean_env[@]}" "$py" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
pa="$(free_port)"; pb="$(free_port)"
t0=$(date +%s.%N)
docker run -d --name "$a" --cpus 2 --memory 2g -e PORT=9123 -p "127.0.0.1:$pa:9123" "$tag" >/dev/null || exit 4
docker run -d --name "$b" --cpus 2 --memory 2g -p "127.0.0.1:$pb:8080" "$tag" >/dev/null || exit 4

wait_healthy() {
  local url="$1" deadline=$(( $(date +%s) + 60 ))
  while (( $(date +%s) < deadline )); do
    if [[ "$(curl -s -m 2 "$url/health")" =~ \"status\"[[:space:]]*:[[:space:]]*\"ok\" ]]; then return 0; fi
    sleep 0.2
  done
  return 1
}
if wait_healthy "http://127.0.0.1:$pa"; then
  printf '== A (PORT=9123) healthy after %.1f s\n' "$(echo "$(date +%s.%N) - $t0" | bc)"
else
  echo "== A NOT HEALTHY within 60 s"; docker logs --tail 40 "$a"; exit 5
fi
if wait_healthy "http://127.0.0.1:$pb"; then
  echo "== B (no PORT, default 8080) healthy"
else
  echo "== B NOT HEALTHY within 60 s (default port 8080?)"; docker logs --tail 40 "$b"; exit 5
fi

echo "== probes"
PROBE_BASE_URL="http://127.0.0.1:$pa" PROBE_BASE_URL_FRESH="http://127.0.0.1:$pb" \
  "${clean_env[@]}" "$py" -m pytest -q -p no:cacheprovider "$here/test_probes.py" "$@"
rc=$?
echo "== container A restarts: $(docker inspect -f '{{.RestartCount}} oom={{.State.OOMKilled}} running={{.State.Running}}' "$a")"
docker logs "$a" 2>&1 | grep -i -E "traceback|exception|panic|error" | head -20
exit $rc
