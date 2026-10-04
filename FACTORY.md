# The factory

Three coding-agent seats in one Band room: a **planner** that coordinates and decides, a
**builder** that implements, a **verifier** that checks independently. The owner posts one
message, the job, and the band runs unattended until the planner's final report. Nothing in the
factory knows what is being built: the problem arrives in that one message, the mandates are
generic, and the guard rails are mechanical.

```
owner ──job (once)──▶ planner ──brief + work orders──▶ builder ──commit──▶ verifier
                         ▲   └──────── same brief ─────────────────────────────▲ │
                         └────────────── verdict with evidence ◀───────────────┘ │
                         └─── final report ──▶ owner                             │
                                              builder ◀── REJECT with repro ─────┘
```

## Seats

| Seat | Owns | Writes and commits | Never |
|---|---|---|---|
| `planner` | Splitting the job into increments and work orders, every open decision, the current-increment setting, the final report | `plans/` | writes product code, ask the owner anything |
| `builder` | Implementing work orders, its own tests, build and run files | the current increment directory only | touches another increment, special-cases a check |
| `verifier` | ACCEPT/REJECT with evidence, a requirements ledger, its own probes for what the shipped checks do not cover | `reviews/` | fixes code, trusts the builder's working tree |

Each seat is a headless Claude Code runtime owned by Jam (Band Desktop's daemon), named exactly
after its role, with its mandate (`mandates/<seat>.md`) live-linked as its standing instructions.
The model is the one named on the mandate's `Model:` line.

## Design choices, and what they cost

| Choice | Why | Cost |
|---|---|---|
| **Three seats with sharp boundaries** (decides / does / checks) | The minimum that separates judgement from implementation from verification; readable in thirty seconds | No parallel builders; one increment is built at a time |
| **Headless, Jam-owned seats** | The run is unattended: no terminal to keep alive, no window a human could type into, restarts handled by the daemon | Debugging happens through the room and the files, not by watching a terminal |
| **No path to the owner** | `AskUserQuestion` is disabled and permission prompts are bypassed; open points are decided by the planner and recorded | A wrong decision is only caught by the verifier or by a later increment |
| **Guard hook instead of permission prompts** (`factory/hooks/guard.py`) | Each seat writes only its own directory; nobody pushes, rewrites history, switches branches in the shared checkout, or opens a secret store | Legitimate but unusual commands are refused and the seat has to find another way |
| **One shared checkout, three git identities** | The history shows who did what: plans by the planner, code by the builder, reports by the verifier. The pre-commit hook keeps each seat's commits inside its directory | Seats must commit with explicit paths |
| **Verification from a fresh clone** (`factory-verify`) | Proves the commit, not the working tree; a file that was never committed fails here, as it would for anyone else | A clone and a full build per verdict |
| **Whole brief in every handoff** (`factory-post`) | The builder and the verifier both get the complete specification, split into numbered parts, so the verifier checks against the requirements and not the builder's reading of them | Long handoffs are several messages |
| **Requirements ledger and own probes** | Shipped checks are a sample; the verifier lists every testable requirement and writes probes for the uncovered ones | Verifier time, the most expensive part of an increment |
| **Scope from an increment base** | `factory-stage` records the commit an increment starts from; anything outside the increment directory, or an answer to the next increment, is a rejection | The planner must set the increment before work starts |

## Stand it up

Requirements: git, Python 3, bash, Docker, Claude Code logged in with a subscription, Band
Desktop (Jam) running and signed in (`jam preflight` all green).

```sh
factory/bin/factory-selftest                         # the guard rails work on this machine
factory/bin/factory-init /abs/path/to/result         # a fresh result repository carrying the factory
cd /abs/path/to/result
factory/bin/factory-seats create --repo "$PWD" --room-name <name>
```

`factory-seats create` registers the three seats, opens one room with them and the owner, and
writes the run state to `<result>.state/run.env` (outside the repository). Then fill in
`factory/templates/dispatch.md` and post it as the one message of the run, mentioning the
planner. Read the final report when it arrives.

`factory-seats remove --repo <result>` removes the seats and retires the room.

## Point it at a different problem

Write a new dispatch message. Nothing else changes: not the mandates, not the scripts. The
check command in the dispatch is whatever the new problem uses to test a delivery; the factory
only needs it to exit 0 on success.

## How it catches bad work

- **Wrong behaviour:** the verifier rejects with a command that reproduces the failure; the
  builder fixes it and adds a test that would have caught it.
- **Work written to the checks:** the verifier reads the code for special cases and hard-coded
  expectations with no sentence of the specification behind them, and rejects them.
- **Uncommitted or unbuildable work:** `factory-verify` builds from a fresh clone; `factory-handoff`
  refuses to hand off a dirty tree or a failing self-check.
- **Scope creep:** the guard and the pre-commit hook keep each seat in its directory; the
  verifier rejects any change outside the increment and any delivery that also passes the next
  increment's checks.
- **Loops:** three failed verifications on the same criterion send the decision back to the
  planner, who changes the order, splits it, or records a known gap.

## What we tried that failed

| Tried | What went wrong | Replaced by |
|---|---|---|
| Interactive Claude Code seats in terminal panes, joined with the plugin's `/jam as <role>` | The plugin always adds a random suffix to the seat name (`planner-6mcx`), so mandates could not be named after their seats; and a terminal is a place a human can type into mid-run | Headless Jam-owned seats with exact names (`factory-seats`) |
| One room per stage, the owner accepting each stage | Four room logs instead of one, and an owner approval between stages is human input | One room; the verifier accepts, the planner advances |
| One git worktree per seat, the owner merging the builder's branch | The merge is the owner touching the delivered code | One shared checkout; each seat commits only its own directory |
| The planner asking the owner about ambiguities (11 questions in the pre-kickoff rehearsal) | Not allowed in an unattended run | The planner decides, records the decision, and the builder or verifier can challenge it with evidence |
| Removing seats after a run | Band shows a removed seat's messages as sent by "Unknown", breaking the seat-to-mandate link in the room log | `factory-seats remove` refuses until the room has been downloaded |

## Measured cost

Reference run: the four-stage reservations problem of the event this factory was built for, one
dispatch, no human input until the final report. All three seats `claude-opus-5-5`, effort high,
on a Claude subscription (no per-token billing; token figures are Jam's catalog estimate).

| Increment | Wall time | Deliveries | Rejections | Verifier's own probes |
|---|---|---|---|---|
| 1 (JSON API) | ~30 min | 2 | 1 (wrong status for unsupported methods; an export that could not be re-imported) | 127 |
| 2 (browser UI) | ~23 min | 1 | 0 | 39 API + 25 browser |
| 3 (time-dependent rules) | ~18 min | 1 | 0 | 37 |
| 4 (bulk changes) | ~18 min | 1 | 0 | 80, incl. 60 random scenarios against an exhaustive reference |
| **Total** | **89 min** | 5 | 1 | 308 |

Tokens: 110 M in total (~$41 at catalog prices): verifier 54 M, builder 47 M, planner 8 M. The
verifier is the most expensive seat by design: it rebuilds from a fresh clone and runs the full
check twice per verdict, plus its own probes. Commits: verifier 14, planner 10, builder 9.

A smaller rehearsal problem (a shared counter, four increments) took 20 minutes and 22 M tokens,
with one rejection in increment 4 that no shipped check covered (integers over 4,300 digits
killed the service).
