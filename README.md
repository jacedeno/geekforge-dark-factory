# GeekForge — a dark software factory

Entry of team **GeekForge** (Jose Cedeno) in the WeAreDevelopers x BAND "Dark Factory" hackathon,
track **tablekeeper**: a restaurant reservation system built in four stages by a band of three
coding-agent seats in one Band room, from one dispatched task, with no human input until the
band's final report.

- **The factory:** [`FACTORY.md`](FACTORY.md) — seats, design choices and their cost, what failed,
  measured time and tokens, how it catches bad work. Stand it up from there and `mandates/`.
- **The run:** [`room.json`](room.json) is the full room log. [`plans/final-report.md`](plans/final-report.md)
  is the planner's final report; `plans/` and `reviews/` hold every brief, decision, verdict and
  probe, committed by the seat that wrote it.
- **What it shipped:** `stage-1/` to `stage-4/`, each a complete service with its own `Dockerfile`
  and `RUN.md`. Each claims its stage on the shipped checks in isolated mode.

## How to read this repository

| Path | What it is | Written by |
|---|---|---|
| `FACTORY.md`, `README.md` | The factory description and this page | owner |
| `mandates/` | Each seat's standing instruction, generic, with its harness and model | owner |
| `factory/`, `.claude/settings.json`, `docker/` | Seat launcher, guards, git hooks, handoff, post and verification scripts | owner |
| `plans/` | Increment briefs, decisions on open points, the final report | `planner` |
| `stage-N/` | The service for stage N | `builder` |
| `reviews/` | Requirements ledgers, verification reports, probes, verdicts | `verifier` |
| `room.json` | The room, downloaded unchanged from Band | Band |

`git log --format='%an %s'` shows who did what. The owner's commits are the setup before the
dispatch and the documentation after the final report; nothing under `stage-N/` is the owner's.

## Owner commits after the run

The seats found two bugs in the factory's own tooling during the run and worked around them
(visible in the room log): the Jam daemon leaked its bundled Python paths into the seats' shells,
and `factory-post` sent seat names without their owner prefix. Both are fixed in `factory/` in a
separate owner commit after the final report; the run itself used the version in the first commit.
