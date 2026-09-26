# <entry name>

<one paragraph: what this repository is, the team, the track>

## How to read this repository

| Path | What it is |
|---|---|
| `FACTORY.md` | The factory: seats, design choices, costs, how it catches bad work |
| `mandates/` | Each seat's standing instruction (one per seat, generic) |
| `factory/` | The factory's tooling: seat launcher, guards, handoff and verification scripts |
| `plans/` | What the planner decided and handed off, per increment |
| `reviews/` | The verifier's reports and probes, per increment |
| `stage-N/` | What the factory shipped for each stage: a complete service with `Dockerfile` and `RUN.md` |
| `room.json` | The full room log, downloaded from Band unchanged |
