# Shared helpers for the factory scripts. Sourced, never executed.
# shellcheck shell=bash

FACTORY_BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FACTORY_DIR="$(dirname "$FACTORY_BIN")"
FACTORY_ROLES=(planner builder verifier)
PLAN_DIR=plans      # the planner's directory in the result repository
REVIEW_DIR=reviews  # the verifier's directory in the result repository

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*" >&2; }

# The result repository: the shared checkout every seat works in.
factory_repo() {
  if [[ -n "${FACTORY_REPO:-}" ]]; then printf '%s\n' "$FACTORY_REPO"; return; fi
  git -C "${1:-$PWD}" rev-parse --show-toplevel 2>/dev/null || die "not inside a git repository"
}

# Run state that must never enter the repository: run.env, seat launchers, check output.
factory_state() {
  if [[ -n "${FACTORY_STATE:-}" ]]; then printf '%s\n' "$FACTORY_STATE"; return; fi
  printf '%s.state\n' "$(factory_repo "${1:-$PWD}")"
}

# Load run.env: ROOM_ID, STAGE_DIR, STAGE_N, STAGE_BASE, CHECK_CMD, ...
factory_load_run_env() {
  local env_file; env_file="$(factory_state)/run.env"
  [[ -f "$env_file" ]] || die "no run config at $env_file; run factory-seats create first"
  # shellcheck disable=SC1090
  source "$env_file"
}

# Set KEY=VALUE in run.env, keeping every other line.
factory_set_run_env() {
  local env_file; env_file="$(factory_state)/run.env"
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  { grep -v -E "^${key}=" "$env_file" 2>/dev/null || true; printf '%s=%q\n' "$key" "$value"; } > "$tmp"
  mv "$tmp" "$env_file"
}

# docker if its daemon answers, else podman.
factory_container_cli() {
  if [[ -n "${CONTAINER_CLI:-}" ]]; then printf '%s\n' "$CONTAINER_CLI"; return; fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then echo docker; return; fi
  if command -v podman >/dev/null 2>&1; then echo podman; return; fi
  die "no container runtime (docker or podman) available"
}
