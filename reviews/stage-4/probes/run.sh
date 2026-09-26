#!/usr/bin/env bash
# Verifier probes for stage-4, against a fresh clone of <commit>.
#
#   reviews/stage-4/probes/run.sh <commit> [pytest args...]
#
# Builds stage-4/ .. stage-1/ from the clone and starts, under the spec limits (2 vCPU / 2 GiB):
# A stage-4 (-e PORT=9123, probe target), B stage-4 (no PORT, 8080), P1/P2/P3 stage-1/2/3
# (upgrade sources). Runs against A: stage-1 probes, stage-2 API and browser probes, stage-3
# probes (regression; the few probes whose exact shapes a later stage supersedes are excluded as
# in earlier runners), then test_api4.py. Removes everything it created.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
commit="${1:?usage: run.sh <commit> [pytest args]}"; shift
py=/home/geekendzone/hackathon/dark-factory-wearedevs/.venv/bin/python
clean_env=(env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH)

sha="$(git -C "$repo" rev-parse --verify "$commit^{commit}")" || exit 2
short="${sha:0:12}"
clone="$(mktemp -d /tmp/verifier-probe4-XXXX)"
tag="${sha:0:8}-$$"
names=("vp4-a-$tag" "vp4-b-$tag" "vp4-p1-$tag" "vp4-p2-$tag" "vp4-p3-$tag")
cleanup() { docker rm -f "${names[@]}" >/dev/null 2>&1; rm -rf "$clone"; }
trap cleanup EXIT

git clone -q --no-local "$repo" "$clone" && git -C "$clone" checkout -q --detach "$sha" || exit 2
echo "== build stage-4..1 @ $short"
for s in 4 3 2 1; do
  docker build -q -t "verifier-probe4-s$s:$short" "$clone/stage-$s" >/dev/null || { echo "BUILD FAILED (stage-$s)"; exit 3; }
done

free_port() { "${clean_env[@]}" "$py" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
pa="$(free_port)"; pb="$(free_port)"; pp1="$(free_port)"; pp2="$(free_port)"; pp3="$(free_port)"
lim=(--cpus 2 --memory 2g)
t0=$(date +%s.%N)
docker run -d --name "${names[0]}" "${lim[@]}" -e PORT=9123 -p "127.0.0.1:$pa:9123" "verifier-probe4-s4:$short" >/dev/null || exit 4
docker run -d --name "${names[1]}" "${lim[@]}" -p "127.0.0.1:$pb:8080" "verifier-probe4-s4:$short" >/dev/null || exit 4
docker run -d --name "${names[2]}" "${lim[@]}" -p "127.0.0.1:$pp1:8080" "verifier-probe4-s1:$short" >/dev/null || exit 4
docker run -d --name "${names[3]}" "${lim[@]}" -p "127.0.0.1:$pp2:8080" "verifier-probe4-s2:$short" >/dev/null || exit 4
docker run -d --name "${names[4]}" "${lim[@]}" -p "127.0.0.1:$pp3:8080" "verifier-probe4-s3:$short" >/dev/null || exit 4

wait_healthy() {
  local url="$1" deadline=$(( $(date +%s) + 60 ))
  while (( $(date +%s) < deadline )); do
    if [[ "$(curl -s -m 2 "$url/health")" =~ \"status\"[[:space:]]*:[[:space:]]*\"ok\" ]]; then return 0; fi
    sleep 0.2
  done
  return 1
}
wait_healthy "http://127.0.0.1:$pa" || { echo "== A NOT HEALTHY within 60 s"; docker logs --tail 40 "${names[0]}"; exit 5; }
printf '== A (stage-4, PORT=9123) healthy after %.1f s\n' "$(echo "$(date +%s.%N) - $t0" | bc)"
wait_healthy "http://127.0.0.1:$pb" || { echo "== B NOT HEALTHY (default port 8080?)"; exit 5; }
echo "== B (stage-4, default 8080) healthy"
for u in "$pp1" "$pp2" "$pp3"; do wait_healthy "http://127.0.0.1:$u" || { echo "== previous stage NOT HEALTHY"; exit 5; }; done
echo "== P1, P2, P3 healthy"

export PROBE_BASE_URL="http://127.0.0.1:$pa" PROBE_BASE_URL_FRESH="http://127.0.0.1:$pb" \
       PROBE_BASE_URL_PREV="http://127.0.0.1:$pp1" PROBE_BASE_URL_PREV1="http://127.0.0.1:$pp1" \
       PROBE_BASE_URL_PREV2="http://127.0.0.1:$pp2" PROBE_BASE_URL_PREV3="http://127.0.0.1:$pp3" \
       PROBE_SHOTS="$repo/reviews/stage-4/shots/$short"
rc=0
pt=("${clean_env[@]}" "$py" -m pytest -q -p no:cacheprovider)
echo "== stage-1 probes against stage-4 (regression)"
"${pt[@]}" "$repo/reviews/stage-1/probes/test_probes.py" -k "not test_create_shape_strict" "$@" || rc=1
echo "== stage-2 API probes against stage-4 (regression)"
"${pt[@]}" "$repo/reviews/stage-2/probes/test_api2.py" \
  -k "not test_create_pair_shape and not test_upgrade_from_stage1" "$@" || rc=1
echo "== stage-2 browser probes against stage-4 (regression)"
"${pt[@]}" "$repo/reviews/stage-2/probes/test_ui2.py" "$@" || rc=1
echo "== stage-3 probes against stage-4 (regression)"
"${pt[@]}" "$repo/reviews/stage-3/probes/test_api3.py" "$@" || rc=1
echo "== stage-4 probes"
"${pt[@]}" "$here/test_api4.py" "$@" || rc=1
echo "== container A: $(docker inspect -f '{{.RestartCount}} restarts oom={{.State.OOMKilled}} running={{.State.Running}}' "${names[0]}")"
docker logs "${names[0]}" 2>&1 | grep -i -E "traceback|exception|panic" | head -20
exit $rc
