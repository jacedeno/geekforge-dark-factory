#!/usr/bin/env python3
"""PreToolUse guard for factory seats.

Reads the Claude Code hook payload on stdin. Exit 0 allows the call; exit 2 blocks it and the
stderr text is shown to the seat as the reason. Outside a factory seat (no FACTORY_ROLE) it
allows everything, so the project settings file is harmless in an ordinary session.

Rules:
  * File writes (Write/Edit/MultiEdit/NotebookEdit) inside the result repository must fall
    inside the seat's write scope:
      planner  -> plans/
      builder  -> the current increment directory (STAGE_DIR in run.env)
      verifier -> reviews/
    Paths outside the repository (temp dirs, the seat's own scratch space) are allowed.
  * Shell commands: no push, no history rewrite, no branch switching or tree resets in the
    shared checkout, no skipped hooks, no identity or account changes, no secret stores.
    Which paths a seat may commit is enforced by the pre-commit hook.
"""
import json
import os
import re
import shlex
import sys

SCOPES = {"planner": "plans", "verifier": "reviews"}


def block(reason: str) -> None:
    print(f"factory guard: {reason}", file=sys.stderr)
    sys.exit(2)


def run_env(state: str) -> dict:
    env = {}
    try:
        with open(os.path.join(state, "run.env")) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                try:
                    parts = shlex.split(value)
                    env[key] = parts[0] if parts else ""
                except ValueError:
                    env[key] = value
    except OSError:
        pass
    return env


def within(path: str, root: str) -> bool:
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def write_scope(role: str, repo: str, env: dict) -> list:
    if role in SCOPES:
        return [os.path.join(repo, SCOPES[role])]
    if role == "builder":
        stage = env.get("STAGE_DIR", "")
        return [os.path.join(repo, stage)] if stage else []
    return []


def check_write(path: str, role: str, repo: str, env: dict) -> None:
    if not path:
        return
    path = os.path.realpath(os.path.join(os.getcwd(), os.path.expanduser(path)))
    if not within(path, os.path.realpath(repo)):
        return
    if within(path, os.path.join(os.path.realpath(repo), ".git")):
        block("the repository's git directory is not a place to write files")
    scope = [os.path.realpath(p) for p in write_scope(role, repo, env)]
    if any(within(path, root) for root in scope):
        return
    allowed = ", ".join(os.path.relpath(p, repo) + "/" for p in scope) or "nothing yet (no increment set)"
    block(f"the {role} may only write inside: {allowed}. Refused: {os.path.relpath(path, repo)}")


SECRET_PATHS = re.compile(r"(homelab-secrets|\.ssh/|\.credentials\.json|\.jam/|\.gnupg|\.netrc|\.aws/)")
SHELL_RULES = [
    (re.compile(r"\bgit\s+push\b"), "seats never push; the owner publishes"),
    (re.compile(r"\bgit\s+(reset\s+--hard|rebase|filter-branch|filter-repo|replace|commit\s+[^|;&]*--amend)\b"),
     "no history rewrites"),
    (re.compile(r"\bgit\s+(switch|checkout\s+(-[bB]\s|[^-\s][^\s]*\s*($|[;&|])))"),
     "the shared checkout stays on its branch; use a separate clone to look at other commits"),
    (re.compile(r"\bgit\s+(stash|clean\s+-[a-zA-Z]*f)"), "stash and clean would touch other seats' work"),
    (re.compile(r"\bgit\s+(worktree\s+(remove|prune|move)|branch\s+-[dD]\b|update-ref\s+-d)"),
     "branches and worktrees belong to the factory, not the seat"),
    (re.compile(r"\bgit\s+config\b(?!\s+(--get|-l|--list))"), "git config is fixed by the factory"),
    (re.compile(r"\bjam\s+(rm|reset|logout|archive|account|agent|detach|init)\b"),
     "identities and accounts are fixed for the whole run"),
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|~|\$HOME|\.\.)(\s|/?$)"),
     "recursive delete outside the repository"),
    (re.compile(r"--no-verify\b"), "git hooks may not be skipped"),
]


def check_shell(cmd: str) -> None:
    if SECRET_PATHS.search(cmd):
        block("secret stores are out of bounds for every seat")
    for pattern, reason in SHELL_RULES:
        if pattern.search(cmd):
            block(reason)


def main() -> None:
    role = os.environ.get("FACTORY_ROLE")
    if not role:
        sys.exit(0)
    repo = os.environ.get("FACTORY_REPO") or os.getcwd()
    state = os.environ.get("FACTORY_STATE") or repo.rstrip("/") + ".state"
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input") or {}
    env = run_env(state)
    if tool in ("Write", "Edit", "MultiEdit"):
        check_write(tin.get("file_path", ""), role, repo, env)
    elif tool == "NotebookEdit":
        check_write(tin.get("notebook_path", ""), role, repo, env)
    elif tool == "Bash":
        check_shell(tin.get("command", ""))
    sys.exit(0)


if __name__ == "__main__":
    main()
