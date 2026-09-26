# Verdict: stage-2 WORK ORDERS 2.1-2.3 @ 48a39df74bc1: ACCEPT

Increment verdict for stage-2: ACCEPT at 48a39df74bc1db80bfbea277b11ef6d473bf8afe.

## Evidence

- `factory/bin/factory-verify 48a39df` -> scope PASS, check PASS (`20260926T224058Z-48a39df74bc1.md`):
  stage 1 pass, stage 2 pass, stage 3 fail; highest contiguous stage 2, claimed stage 2.
- `reviews/stage-2/probes/run.sh 48a39df` (`probes-48a39df74bc1.log`):
  - stage-1 probes against stage-2: 126 passed; the one failure is `test_create_shape_strict`, which
    asserts the stage-1 key set and is superseded by stage-2 (`table_ids` added, E10). The runner now
    excludes it (`-k`); the regression is otherwise clean.
  - stage-2 API probes: 39 passed (E10 precedence matrix, E12 seeding, E14 options, pair occupancy,
    PATCH/moves with `table_ids`, 30-way concurrent pair vs single race, stage-2 round trip, upgrade
    from a real stage-1 container with original-response replays and tokens).
  - stage-2 browser probes (headless Chromium): 25 passed (out-of-order search, 409 refresh with kept
    form, lost-after-commit -> uncertain -> same-key recovery, 5xx and unreadable 200 -> uncertain,
    4xx after uncertain, retry and session across export/import, lookup matrix incl. cutoff refusal,
    HTML injection, no external requests, no horizontal scroll at 375/1280, labels, visible focus).
  - Healthy 0.9 s; no restart/OOM.
- WO 2.1: tree of `stage-2/` at 8176222 equals tree of `stage-1/` at 71e7483 (d6947eb).
  `stage-1/` unchanged since the base.
- Visual review (E15), screenshots in `shots/48a39df74bc1/`: warm, consistent design system (serif
  headings, terracotta primary, cream surfaces); distinct open / taken / too small / selected /
  confirmed / refused / uncertain states; human labels for pairs ("Window + Garden, Joined 6 seats");
  labelled inputs; consistent header with navigation and current user; usable single-column
  layout at 375 px.
- Second run: factory-verify again PASS (`20260926T224415Z-48a39df74bc1.md`); run.sh again -> 126 + 39 + 25 passed, 0 failed (`probes-48a39df74bc1-run2.log`). Not flaky.
- Ledger: 46/46 stage-2 requirements covered, 41 by own probes (`ledger.md`); stage-1 ledger rerun.

## Code read

Server diff 8176222..48a39df (model, service, server, web) follows E10-E14; one global lock unchanged;
UI (`static/app.js`) inserts data as text (only a static SVG via innerHTML), sequence numbers guard
searches and lookups, local assets only. No check-specific special cases. Noted, not a defect: reset
accepts a seeded reservation on an undeclared pair as trusted fixture data (E12/D14 permit it).

## Scope

In scope: only `stage-2/` changed (plus planning and review directories); nothing from stage 3.
