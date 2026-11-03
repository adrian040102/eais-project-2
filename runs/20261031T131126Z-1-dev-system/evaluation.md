# Evaluation: dev

Score: **0.00 / 26 (0.00%)**. Fully right: **0 / 26 requests**.

Coverage: 26 attempted, 0 not run, 0 missing. Statuses: ok=26.

Cost: **$0.00000**; tools: 0; model calls: 0; model errors: 0.

Request runtime sum: 11.060s; mean: 0.425s; p95: 0.52s. Runtime excludes setup; p95 uses the nearest-rank method over attempted requests.

Diagnostic report. The course runner is the scoring authority. Amount/train/provenance counts describe failed claim checks; missing claims are counted separately. Company groups may overlap. Cost includes setup when recorded in the summary.

## Failure diagnostics

| Category | Count |
|---|---:|
| amount | 0 |
| train | 0 |
| provenance | 0 |
| missing claim | 20 |
| missing no claim | 6 |
| wrong no claim | 0 |
| filed when nothing owed | 0 |
| extra or duplicate claims | 0 |
| no claim train evidence | 0 |
| missing facts | 0 |
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
| mismatch_over | 2 / 2 | 0.00 | 2 | 0 |
| mismatch_under | 1 / 1 | 0.00 | 1 | 0 |
| missed_separate | 1 / 1 | 0.00 | 1 | 0 |
| missed_through | 4 / 4 | 0.00 | 4 | 0 |
| nothing_minimum | 2 / 2 | 0.00 | 2 | 0 |
| nothing_threshold | 1 / 1 | 0.00 | 1 | 0 |
| ouigo | 1 / 1 | 0.00 | 1 | 0 |
| return_ticket | 1 / 1 | 0.00 | 1 | 0 |
| single_ask | 9 / 9 | 0.00 | 9 | 0 |
| single_full | 3 / 3 | 0.00 | 3 | 0 |
| trenord | 1 / 1 | 0.00 | 1 | 0 |

## Company

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| DB | 12 / 12 | 0.00 | 12 | 0 |
| OUIGO | 1 / 1 | 0.00 | 1 | 0 |
| SBB | 2 / 2 | 0.00 | 2 | 0 |
| SNCF | 8 / 8 | 0.00 | 8 | 0 |
| Trenitalia | 2 / 2 | 0.00 | 2 | 0 |
| Trenord | 1 / 1 | 0.00 | 1 | 0 |

## Decision

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| claim | 20 / 20 | 0.00 | 20 | 0 |
| no_claim | 6 / 6 | 0.00 | 6 | 0 |

## Requests

| ID | Type | Net / max | Status | Issues | Questions / limit | Tools | Models | USD | Seconds |
|---|---|---:|---|---|---:|---:|---:|---:|---:|
| P001 | ouigo | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.49 |
| P002 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.45 |
| P003 | nothing_minimum | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.50 |
| P004 | mismatch_over | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.56 |
| P005 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.43 |
| P007 | return_ticket | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.38 |
| P008 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.39 |
| P010 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 5 | 0 | 0 | 0.00000 | 0.34 |
| P016 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.41 |
| P017 | single_full | 0.00 / 1 | ok | missing-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.35 |
| P020 | single_full | 0.00 / 1 | ok | missing-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.42 |
| P021 | missed_through | 0.00 / 1 | ok | missing-claim | 0 / 5 | 0 | 0 | 0.00000 | 0.38 |
| P023 | nothing_threshold | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.39 |
| P024 | missed_separate | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.37 |
| P025 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.42 |
| P028 | mismatch_under | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.38 |
| P030 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.49 |
| P031 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.52 |
| P032 | single_ask | 0.00 / 1 | ok | missing-claim | 0 / 5 | 0 | 0 | 0.00000 | 0.41 |
| P034 | missed_through | 0.00 / 1 | ok | missing-claim | 0 / 6 | 0 | 0 | 0.00000 | 0.39 |
| P036 | trenord | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.51 |
| P038 | missed_through | 0.00 / 1 | ok | missing-claim | 0 / 5 | 0 | 0 | 0.00000 | 0.47 |
| P039 | mismatch_over | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.44 |
| P041 | single_full | 0.00 / 1 | ok | missing-claim | 0 / 3 | 0 | 0 | 0.00000 | 0.39 |
| P043 | nothing_minimum | 0.00 / 1 | ok | missing-no-claim | 0 / 2 | 0 | 0 | 0.00000 | 0.38 |
| P044 | missed_through | 0.00 / 1 | ok | missing-claim | 0 / 4 | 0 | 0 | 0.00000 | 0.40 |
