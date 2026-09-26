# Increment status

| Increment | Final commit | Verdict | Check | Known gaps |
|---|---|---|---|---|
| stage-1 | 71e748308f9d89ccc274cdaf1616d8ac2f4ae50c | ACCEPT (reviews/stage-1/verdict-71e748308f9d.md) | PASS twice (highest contiguous 1, claimed 1); verifier probes 127/127; ledger 76/76 | none |

Stage-1 history: 84a7bbb rejected (TRACE -> 501; export with empty display_name not importable), fixed in 71e7483.
D14 revised at 07395a3: seeded references follow the §8 reference format (builder evidence).

| stage-2 | 48a39df74bc1db80bfbea277b11ef6d473bf8afe | ACCEPT first delivery (reviews/stage-2/verdict-48a39df74bc1.md) | PASS twice (highest contiguous 2, claimed 2); verifier probes 126 stage-1 + 39 API + 25 browser, 0 failed; ledger 46/46 | none (seeded booking on an undeclared pair accepted as trusted data, per E12/D14) |
| stage-3 | 1937f01881823a9e9bee53ab2983e5763cab93c8 | ACCEPT first delivery (reviews/stage-3/verdict-1937f0188182.md) | PASS twice (highest contiguous 3, claimed 3); verifier probes 126 + 37 + 25 + 37, 0 failed; ledger 42/42 | none (restaurant revision kept internally, exposed in stage 4) |
| stage-4 | c150b0fe77082886ba32865d84f7de0a1b4f9c29 | ACCEPT first delivery (reviews/stage-4/verdict-c150b0fe7708.md) | PASS twice (highest contiguous 4, claimed 4); verifier probes 126 + 37 + 25 + 37 + 80, 0 failed; ledger 26/26 | series-amend `cutoff_passed` verified by code read only (a probe needs wall-clock time to pass after adoption) |
