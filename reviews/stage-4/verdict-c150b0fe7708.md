# Verdict: stage-4 WORK ORDERS 4.1-4.5 @ c150b0fe7708: ACCEPT

Increment verdict for stage-4: ACCEPT at c150b0fe77082886ba32865d84f7de0a1b4f9c29.

## Evidence

- `factory/bin/factory-verify c150b0f` run twice -> scope PASS, check PASS both
  (`20260926T231552Z-c150b0fe7708.md`, `20260926T232216Z-c150b0fe7708.md`): stages 1-4 pass;
  highest contiguous stage 4, claimed stage 4.
- `reviews/stage-4/probes/run.sh c150b0f` (`probes-c150b0fe7708.log`), 0 failures:
  stage-1 probes 126 passed; stage-2 API 37; stage-2 browser 25; stage-3 37; stage-4 80
  (restaurant revision counting, replan/apply precedence, planning_limit, 60 reproducible random
  scenarios identical to an exhaustive reference implementation of G3/G4 incl. no_feasible_plan and
  apply read-back, speed at 6 tables/4 pairs/6 bookings < 5 s, preview side effects, apply effects and
  reassigned history, closures everywhere incl. half-open boundaries and later plans, concurrent
  applies, lookup screen after apply, series amend precedence/eligibility/per-date policy/DST/error
  order/concurrency, stage-4 round trip, upgrades from real stage-1 and stage-3 services).
  The first run (`probes-c150b0fe7708-run1.log`) had one failure that was a probe bug (the probe's
  own anchor already held the table; 409 was correct); fixed in the probe, rerun clean. Healthy 1.7 s.
- WO 4.1: tree of `stage-4/` at 97ab750 equals tree of `stage-3/` at 1937f01 (49682eb).
  `stage-1/`..`stage-3/` unchanged.
- Ledger: 26/26 stage-4 requirements covered, 23 by own probes; stages 1-3 rerun as regression.
  Requirement 25 (series-amend `cutoff_passed`) needs wall-clock time to pass after adoption and is
  verified by code read only: `_amend_series` checks the accepted cutoff for each really changing
  occurrence, in index order, before validation, as G9 states.

## Code read

`planner.py`: depth-first branch and bound over candidates sorted by (changed, unused, rank), with
admissible suffix lower bounds on moves and unused seats and a prefix comparison on ranks; exact.
`service.py`: replan/apply/series-amend orders per G2/G6/G9; closures enforced in `_overlaps` (all
writes) and in availability; application under the single global lock. No check-specific code.

## Scope

In scope: only `stage-4/` changed (plus planning and review directories).
