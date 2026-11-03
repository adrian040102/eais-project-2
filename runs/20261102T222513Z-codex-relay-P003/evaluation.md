> Supervised Codex relay. API charges only: $0; Codex usage and tokens are unmeasured. See relay_manifest.json for model, source, and limitations.

# Evaluation: supervised-relay-development

Score: **0.00 / 1 (0.00%)**. Fully right: **0 / 1 requests**.

Coverage: 1 attempted, 0 not run, 0 missing. Statuses: ok=1.

Cost: **$0.00000**; tools: 6; model calls: 10; model errors: 0.

Request runtime sum: 151.340s; mean: 151.34s; p95: 151.34s. Runtime excludes setup; p95 uses the nearest-rank method over attempted requests.

Diagnostic report. The course runner is the scoring authority. Amount/train/provenance counts describe failed claim checks; missing claims are counted separately. Company groups may overlap. Cost includes setup when recorded in the summary.

## Failure diagnostics

| Category | Count |
|---|---:|
| amount | 0 |
| train | 0 |
| provenance | 0 |
| missing claim | 0 |
| missing no claim | 1 |
| wrong no claim | 0 |
| filed when nothing owed | 1 |
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
| nothing_minimum | 1 / 1 | -0.50 | 1 | 0 |

## Company

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| DB | 1 / 1 | -0.50 | 1 | 0 |

## Decision

| Group | Attempted / expected | Net points before floor | Maximum | Fully right |
|---|---:|---:|---:|---:|
| no_claim | 1 / 1 | -0.50 | 1 | 0 |

## Requests

| ID | Type | Net / max | Status | Issues | Questions / limit | Tools | Models | USD | Seconds |
|---|---|---:|---|---|---:|---:|---:|---:|---:|
| P003 | nothing_minimum | -0.50 / 1 | ok | filed-when-nothing-owed, missing-no-claim | 1 / 2 | 6 | 10 | 0.00000 | 151.34 |
