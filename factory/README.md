# factory/

The factory's tooling. How the factory works, why, and how to stand it up: `../FACTORY.md`.

| Piece | Who runs it | What it does |
|---|---|---|
| `bin/factory-selftest` | owner, once per machine | Proves the guard rails below work here |
| `bin/factory-init <dir>` | owner, once per run | A fresh result repository carrying mandates, tooling and seat settings |
| `bin/factory-seats create\|status\|remove` | owner, once per run | Registers the three headless seats in Jam, opens the room, writes `<repo>.state/run.env` |
| `bin/factory-stage <dir> --check <cmd>` | planner | Declares the current increment and its check command; records the increment base |
| `bin/factory-post --to <seats> --title <t> <file>` | planner (any seat) | Posts a long handoff as numbered parts, each mentioning every recipient |
| `bin/factory-handoff` | builder | Refuses uncommitted work, out-of-scope commits or a failing self-check; prints the delivery block |
| `bin/factory-verify <commit>` | verifier | Fresh clone at the commit, scope check, the increment's check command, a report under `reviews/` |
| `bin/factory-lint-mandates` | owner, CI | Flags anything in a mandate shaped like problem detail |
| `hooks/guard.py` | every seat (PreToolUse hook, via `.claude/settings.json`) | Per-seat write scope; no push, history rewrite, branch switch, skipped hooks, identity changes or secret stores |
| `git-hooks/pre-commit`, `commit-msg` | every commit | No credentials, no AI attribution, each seat's commits inside its own directory |
| `templates/dispatch.md` | owner | The one message of a run |
| `../docker/cleanroom.sh` | any seat | Builds and boots a service directory with no network, for problems that ship no harness |
