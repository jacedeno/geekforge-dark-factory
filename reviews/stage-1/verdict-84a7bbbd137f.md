# Verdict: stage-1 WORK ORDERS 1.1-1.6 @ 84a7bbbd137f: REJECT

## Evidence

- `factory/bin/factory-verify 84a7bbb` -> scope PASS, check PASS (report `20260926T221257Z-84a7bbbd137f.md`):
  stage 1 suite pass, stage 2 suite fail, highest contiguous stage 1, claimed stage 1.
- `reviews/stage-1/probes/run.sh 84a7bbb` -> 125 passed, 2 failed of 127 (log `probes-84a7bbbd137f.log`).
  Healthy 0.6 s after start; default port 8080 works; no container restart or OOM.
- Ledger: 76/76 requirements covered, 63 by own probes (`ledger.md`).
- Code read: single global lock around every state change including idempotency lookup and record;
  scrypt N=2^14 outside the lock; no special cases or hard-coded expectations found. Nothing from
  stage 2 (`combinable`, `table_ids`, HTML) present.

## Failures

1. §5 "Requests must not produce 5xx responses" and D15 (wrong method on a known route -> 405
   `method_not_allowed`): any method without a `do_<METHOD>` handler (TRACE, CONNECT, custom
   methods) falls through to `BaseHTTPRequestHandler.send_error(501)` and answers
   `501 {"error":{"code":"internal_error"}}`.
   Repro: `curl -s -i -X TRACE http://127.0.0.1:8080/restaurants` -> `501`; expected `405 method_not_allowed`.
   Probe: `test_unusual_http_method_is_not_5xx`.
2. §10 "It must accept an unchanged export produced by this service": reset accepts a user with
   `"display_name": ""` (model.py `from_fixture`, `_str(..., allow_empty=True)`), but import
   requires a non-empty `display_name` (`from_export`, `_str(u, "display_name")`), so the export of
   that state is refused with 422 `validation_failed` ("display_name must be a string").
   Repro: reset with users `[{"id":"u_e","email":"e@x.io","password":"correct horse","display_name":""}]`,
   `GET /_test/export`, `POST /_test/import` with that body -> `422`; expected `204`.
   Probe: `test_export_of_any_accepted_fixture_is_importable`. Any other field that reset accepts
   but import validates more strictly has the same defect; reset and import should share one rule.

## Scope

In scope: only `stage-1/` changed since the increment base.
