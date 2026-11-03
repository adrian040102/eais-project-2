> Supervised Codex relay. API charges only: $0; Codex usage and tokens are unmeasured. See relay_manifest.json for model, source, and limitations.

# Evaluation: supervised-relay-development

Score: **1.00 / 1 (100.00%)**. Fully right: **1 / 1 requests**.

Coverage: 1 attempted, 0 not run, 0 missing. Statuses: ok=1.

Cost: **$0.00000**; tools: 4; model calls: 9; model errors: 0.

Request runtime sum: 165.830s; mean: 165.83s; p95: 165.83s. Runtime excludes setup; p95 uses the nearest-rank method over attempted requests.

Diagnostic report. The course runner is the scoring authority. Amount/train/provenance counts describe failed claim checks; missing claims are counted separately. Company groups may overlap. Cost includes setup when recorded in the summary.

## Failure diagnostics

| Category | Count |
|---|---:|
| amount | 0 |
| train | 0 |
| provenance | 0 |
| missing claim | 0 |
| missing no claim | 0 |
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
| nothing_minimum | 1 / 1 | 1.00 | 1 | 1 |

## Company

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| DB | 1 / 1 | 1.00 | 1 | 1 |

## Decision

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| no_claim | 1 / 1 | 1.00 | 1 | 1 |

## Requests

| ID | Type | Net / max | Status | Issues | Questions / limit | Tools | Models | USD | Seconds |
|---|---|---:|---|---|---:|---:|---:|---:|---:|
| P003 | nothing_minimum | 1.00 / 1 | ok |  | 1 / 2 | 4 | 9 | 0.00000 | 165.83 |
