> Supervised Codex relay. API charges only: $0; Codex usage and tokens were not measured. See relay_manifest.json for source and limitations.

> Invalid relay response {}. The driver rejected the missing assistant role and recorded a protocol error.

# Evaluation: supervised-relay-development

Score: **0.00 / 1 (0.00%)**. Fully right: **0 / 1 requests**.

Coverage: 1 attempted, 0 not run, 0 missing. Statuses: ok=1.

Cost: **$0.00000**; tools: 1; model calls: 2; model errors: 1.

Request runtime sum: 71.410s; mean: 71.41s; p95: 71.41s. Runtime excludes setup; p95 uses the nearest-rank method over attempted requests.

Diagnostic report. The course runner is the scoring authority. Amount/train/provenance counts describe failed claim checks; missing claims are counted separately. Company groups may overlap. Cost includes setup when recorded in the summary.

## Failure diagnostics

| Category | Count |
|---|---:|
| amount | 0 |
| train | 0 |
| provenance | 0 |
| missing claim | 1 |
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
| missed_through | 1 / 1 | 0.00 | 1 | 0 |

## Company

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| SNCF | 1 / 1 | 0.00 | 1 | 0 |

## Decision

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| claim | 1 / 1 | 0.00 | 1 | 0 |

## Requests

| ID | Type | Net / max | Status | Issues | Questions / limit | Tools | Models | USD | Seconds |
|---|---|---:|---|---|---:|---:|---:|---:|---:|
| P021 | missed_through | 0.00 / 1 | ok | missing-claim | 1 / 5 | 1 | 2 | 0.00000 | 71.41 |
