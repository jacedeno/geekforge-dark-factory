# Verdict: stage-1 WORK ORDERS 1.1-1.6 @ 71e748308f9d: ACCEPT

Increment verdict for stage-1: ACCEPT at 71e748308f9d89ccc274cdaf1616d8ac2f4ae50c.

## Evidence

- `factory/bin/factory-verify 71e7483` run twice -> scope PASS, check PASS both times
  (`20260926T222103Z-71e748308f9d.md`, `20260926T222303Z-71e748308f9d.md`): stage 1 suite pass,
  stage 2 suite fail, highest contiguous stage 1, claimed stage 1.
- `reviews/stage-1/probes/run.sh 71e7483` -> 127 passed, 0 failed (`probes-71e748308f9d.log`);
  healthy 0.6 s after start; default port 8080 works; import into a fresh container works; no restart/OOM.
- Concurrency probes (50 competing bookings, 50 identical-key requests, 50 concurrent logins,
  20 concurrent PATCHes onto one table, 30 identical-key moves) repeated 3 more times -> 5/5 passed each run.
- Ledger: 76/76 requirements covered, 63 by own probes (`ledger.md`). D14 as re-decided at 07395a3
  (seeded references must be 6-12 of A-Z0-9 and unique) matches the delivered behaviour.

## Fixes from the REJECT of 84a7bbb

1. Unusual HTTP methods (TRACE, CONNECT, custom) -> 405 `method_not_allowed` on known routes, no 5xx:
   `test_unusual_http_method_is_not_5xx` passes.
2. Export of a state with `display_name: ""` imports again (one shared user-field rule):
   `test_export_of_any_accepted_fixture_is_importable` passes.

## Code read (diff 84a7bbb..71e7483)

Only `stage-1/app/tablekeeper/{server,model}.py` and two tests changed; both changes follow the
specification text. No special cases or hard-coded expectations.

## Scope

In scope: only `stage-1/` changed since the increment base (planning and review directories aside).
