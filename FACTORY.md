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

## Measured cost

<filled in from the submitted run: wall time per increment, subscription usage per increment,
number of work orders, rejections and what they caught>
