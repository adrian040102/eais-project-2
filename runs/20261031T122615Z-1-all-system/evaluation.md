# Evaluation: all

Score: **0.00 / 36 (0.00%)**. Fully right: **0 / 36 requests**.

Coverage: 36 attempted, 0 not run, 0 missing. Statuses: ok=36.

Cost: **$0.00000**; tools: 0; model calls: 0; model errors: 0.

Request runtime sum: 5.270s; mean: 0.146s; p95: 0.3s. Runtime excludes setup; p95 uses the nearest-rank method over attempted requests.

Diagnostic report. The course runner is the scoring authority. Amount/train/provenance counts describe failed claim checks; missing claims are counted separately. Company groups may overlap. Cost includes setup when recorded in the summary.

## Failure diagnostics

| Category | Count |
|---|---:|
| amount | 0 |
| train | 0 |
| provenance | 0 |
| missing claim | 26 |
| missing no claim | 10 |
| wrong no claim | 0 |
| filed when nothing owed | 0 |
| extra or duplicate claims | 0 |
| no claim train evidence | 0 |
| missing facts | 2 |
| wrong ticket price | 0 |
| questions over | 0 |
| runtime error | 0 |
| call budget | 0 |
| timeout | 0 |
| model access | 0 |
| cost cap | 0 |
| not run | 0 |
| missing output | 0 |

Counts can overlap. A combined ATP issue increments amount, train, and provenance. Missing-fact counts use detailed checks where available; missing claims do not also count as ATP failures.

## Case type

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| mismatch_over | 3 / 3 | 0.00 | 3 | 0 |
| mismatch_under | 2 / 2 | 0.00 | 2 | 0 |
| missed_separate | 1 / 1 | 0.00 | 1 | 0 |
| missed_through | 5 / 5 | 0.00 | 5 | 0 |
| nothing_minimum | 3 / 3 | 0.00 | 3 | 0 |
| nothing_threshold | 2 / 2 | 0.00 | 2 | 0 |
| ouigo | 2 / 2 | 0.00 | 2 | 0 |
| return_ticket | 2 / 2 | 0.00 | 2 | 0 |
| single_ask | 10 / 10 | 0.00 | 10 | 0 |
| single_full | 4 / 4 | 0.00 | 4 | 0 |
| trenord | 2 / 2 | 0.00 | 2 | 0 |

## Company

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| DB | 16 / 16 | 0.00 | 16 | 0 |
| OUIGO | 2 / 2 | 0.00 | 2 | 0 |
| SBB | 3 / 3 | 0.00 | 3 | 0 |
| SNCF | 10 / 10 | 0.00 | 10 | 0 |
| Trenitalia | 3 / 3 | 0.00 | 3 | 0 |
| Trenord | 2 / 2 | 0.00 | 2 | 0 |

## Decision

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| claim | 26 / 26 | 0.00 | 26 | 0 |
| no_claim | 10 / 10 | 0.00 | 10 | 0 |

## Requests

| ID | Type | Net / max | Status | Issues | Questions / limit | Tools | Models | USD | Seconds |
|---|---|---:|---|---|---:|---:|---:|---:|---:|
| P001 | ouigo | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.09 |
| P002 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.30 |
| P003 | nothing_minimum | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.19 |
| P004 | mismatch_over | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.16 |
| P005 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.10 |
| P006 | mismatch_over | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.11 |
| P007 | return_ticket | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.09 |
| P008 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.11 |
| P010 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 5 | 0 | 0 | 0.00000 | 0.10 |
| P011 | nothing_minimum | 0.00 / 1 | ok | missing-no-claim, topics | 0 / 3 | 0 | 0 | 0.00000 | 0.24 |
| P012 | ouigo | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.16 |
| P014 | missed_through | 0.00 / 1 | ok | missing-claim | 0 / 5 | 0 | 0 | 0.00000 | 0.18 |
| P015 | return_ticket | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.17 |
| P016 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.15 |
| P017 | single_full | 0.00 / 1 | ok | missing-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.11 |
| P020 | single_full | 0.00 / 1 | ok | missing-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.12 |
| P021 | missed_through | 0.00 / 1 | ok | missing-claim | 0 / 5 | 0 | 0 | 0.00000 | 0.11 |
| P022 | nothing_threshold | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.10 |
| P023 | nothing_threshold | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.10 |
| P024 | missed_separate | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.09 |
| P025 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.15 |
| P028 | mismatch_under | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.11 |
| P030 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.14 |
| P031 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.22 |
| P032 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 5 | 0 | 0 | 0.00000 | 0.32 |
| P033 | single_full | 0.00 / 1 | ok | missing-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.18 |
| P034 | missed_through | 0.00 / 1 | ok | missing-claim | 0 / 6 | 0 | 0 | 0.00000 | 0.14 |
| P036 | trenord | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.09 |
| P037 | mismatch_under | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.09 |
| P038 | missed_through | 0.00 / 1 | ok | missing-claim | 0 / 5 | 0 | 0 | 0.00000 | 0.10 |
| P039 | mismatch_over | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.10 |
| P040 | trenord | 0.00 / 1 | ok | missing-no-claim, topics | 0 / 3 | 0 | 0 | 0.00000 | 0.11 |
| P041 | single_full | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.11 |
| P043 | nothing_minimum | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.22 |
| P044 | missed_through | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.27 |
| P045 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.14 |
