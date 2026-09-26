#!/usr/bin/env bash
# Verifier probes for stage-2, against a fresh clone of <commit>.
#
#   reviews/stage-2/probes/run.sh <commit> [pytest args...]
#
# Builds stage-2/ and stage-1/ from the clone and starts, under the spec limits (2 vCPU / 2 GiB):
#   A  stage-2 with -e PORT=9123        (probe target)
#   B  stage-2 with no PORT (8080)      (untouched import target, default-port check)
#   P  stage-1                          (the preceding stage, for the upgrade probe)
# Then runs: the stage-1 probes against A (regression), test_api2.py, test_ui2.py (screenshots to
# reviews/stage-2/shots/<sha>/). Removes everything it created.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
commit="${1:?usage: run.sh <commit> [pytest args]}"; shift
py=/home/geekendzone/hackathon/dark-factory-wearedevs/.venv/bin/python
clean_env=(env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH)

sha="$(git -C "$repo" rev-parse --verify "$commit^{commit}")" || exit 2
short="${sha:0:12}"
clone="$(mktemp -d /tmp/verifier-probe2-XXXX)"
a="vp2-a-${sha:0:8}-$$"; b="vp2-b-${sha:0:8}-$$"; p="vp2-p-${sha:0:8}-$$"
cleanup() { docker rm -f "$a" "$b" "$p" >/dev/null 2>&1; rm -rf "$clone"; }
trap cleanup EXIT

git clone -q --no-local "$repo" "$clone" && git -C "$clone" checkout -q --detach "$sha" || exit 2
echo "== build stage-2 and stage-1 @ $short"
docker build -q -t "verifier-probe2:$short" "$clone/stage-2" || { echo "BUILD FAILED (stage-2)"; exit 3; }
docker build -q -t "verifier-probe1:$short" "$clone/stage-1" || { echo "BUILD FAILED (stage-1)"; exit 3; }

free_port() { "${clean_env[@]}" "$py" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
pa="$(free_port)"; pb="$(free_port)"; pp="$(free_port)"
t0=$(date +%s.%N)
docker run -d --name "$a" --cpus 2 --memory 2g -e PORT=9123 -p "127.0.0.1:$pa:9123" "verifier-probe2:$short" >/dev/null || exit 4
docker run -d --name "$b" --cpus 2 --memory 2g -p "127.0.0.1:$pb:8080" "verifier-probe2:$short" >/dev/null || exit 4
docker run -d --name "$p" --cpus 2 --memory 2g -e PORT=8080 -p "127.0.0.1:$pp:8080" "verifier-probe1:$short" >/dev/null || exit 4

wait_healthy() {
  local url="$1" deadline=$(( $(date +%s) + 60 ))
  while (( $(date +%s) < deadline )); do
    if [[ "$(curl -s -m 2 "$url/health")" =~ \"status\"[[:space:]]*:[[:space:]]*\"ok\" ]]; then return 0; fi
    sleep 0.2
  done
  return 1
}
wait_healthy "http://127.0.0.1:$pa" || { echo "== A NOT HEALTHY within 60 s"; docker logs --tail 40 "$a"; exit 5; }
printf '== A (stage-2, PORT=9123) healthy after %.1f s\n' "$(echo "$(date +%s.%N) - $t0" | bc)"
wait_healthy "http://127.0.0.1:$pb" || { echo "== B NOT HEALTHY (default port 8080?)"; exit 5; }
echo "== B (stage-2, default 8080) healthy"
wait_healthy "http://127.0.0.1:$pp" || { echo "== P (stage-1) NOT HEALTHY"; exit 5; }
echo "== P (stage-1) healthy"

export PROBE_BASE_URL="http://127.0.0.1:$pa" PROBE_BASE_URL_FRESH="http://127.0.0.1:$pb" \
       PROBE_BASE_URL_PREV="http://127.0.0.1:$pp" PROBE_SHOTS="$repo/reviews/stage-2/shots/$short"
rc=0
echo "== stage-1 probes against stage-2 (regression)"
"${clean_env[@]}" "$py" -m pytest -q -p no:cacheprovider "$repo/reviews/stage-1/probes/test_probes.py" \
  --deselect "$repo/reviews/stage-1/probes/test_probes.py::test_create_shape_strict" "$@" || rc=1
echo "== stage-2 API probes"
"${clean_env[@]}" "$py" -m pytest -q -p no:cacheprovider "$here/test_api2.py" "$@" || rc=1
echo "== stage-2 browser probes"
"${clean_env[@]}" "$py" -m pytest -q -p no:cacheprovider "$here/test_ui2.py" "$@" || rc=1
echo "== container A: $(docker inspect -f '{{.RestartCount}} restarts oom={{.State.OOMKilled}} running={{.State.Running}}' "$a")"
docker logs "$a" 2>&1 | grep -i -E "traceback|exception|panic" | head -20
exit $rc
