# Correction validation — 3 November 2026

The current working source passes the three selected development checks with GPT-6 Luna and low reasoning. The supplied scorer gave **3/3**, and a separate trace audit checked the actual arrival times, delay, amount and payment fields. These remain supervised Codex relay runs; course API access has not been tested.

## Final checks on the current source

All three runs used source SHA-256 `b0cf4c5d985d42fecf7f22d2e80bdecae7466ccb3611f977cfa631711ce1298f`.

| Case | Official score | Questions | Tools | Model requests | Audited outcome |
|---|---:|---:|---:|---:|---|
| [P001](../runs/20261103T091116Z-final-correction-P001/evaluation.md) | 1/1 | 1/3 | 5 | 8 | Travel date 20 October; planned arrival 22:13, recorded arrival 00:23 next day; 130 minutes; EUR 12.50 voucher claim. |
| [P003](../runs/20261103T091116Z-final-correction-P003/evaluation.md) | 1/1 | 1/2 | 5 | 8 | Correct DB no-claim decision, with train evidence and no filing or question penalty. |
| [P021](../runs/20261103T091116Z-final-correction-P021/evaluation.md) | 1/1 | 4/5 | 15 | 12 | Incoming TGV INOUI 9866; replacement 9879; final arrival 19:33 against planned 15:50; 223 minutes; EUR 57.75 bank-transfer claim. |

All 72 local tests passed in the existing Python 3.10 container. All 94 supplied course files still match their manifest sizes and SHA-256 hashes. The original archives, earlier source snapshots and raw run summaries remain unchanged.

## What changed

1. `resolve_connection` looks up the scheduled incoming and missed departures, applies the fixed course transfer time, and selects the first feasible noncancelled replacement by recorded departure. It computes the final delay itself. This removes guesses about which train and arrival belong to the journey.
2. Before using an unknown-onward fallback, the dispatcher obtains an onward-travel answer if none is recorded. A recognized payment choice similarly obtains a cached preference before filing. `ask_once` keeps each topic from being charged twice.
3. For a resolved connection, the model supplies the complete applicable `rate_bands`. Python selects the highest threshold not exceeding the final delay and performs the decimal calculation. No company-specific rates are hardcoded.
4. Filing checks retain the resolved arrival values, final delay and calculated amount. Unambiguous observed train names and numbers are normalized to their IDs, so valid names no longer waste a correction call or bypass duplicate checks.
5. A positive calculation blocks a contradictory no-claim declaration until the model files or explicitly recalculates. The instructions also distinguish the writing date from a relative travel date.

The assistant is still one file: **486 lines, 15 top-level functions, and the unchanged 42-line main loop**. The substantive new helpers are the connection resolver and the shared question cache. The walkthrough explains both.

## Development evidence, including failures

| Source revision | Evidence | Outcome and implication |
|---|---|---|
| Connection resolver, `0f23980a…` | [Trial A](../runs/20261102T231107Z-connection-fix-P021-a/evaluation.md), [trial B](../runs/20261102T231108Z-connection-fix-P021-b/evaluation.md) | Both 0/1. Journey times and payout were correct, but the model skipped the required onward-travel question. This motivated the clarification gate. |
| Clarification gates, `ea87b531…` | [P021](../runs/20261102T231654Z-connection-final-P021-b/evaluation.md) | 1/1 with exact fields. |
| Same revision | [P021 repeat](../runs/20261102T232009Z-connection-verified-P021-repeat/evaluation.md) | 0/1: correct 223-minute delay but an incorrect percentage selected from the table. This motivated deterministic band selection. |
| Same revision | [P003](../runs/20261102T231654Z-connection-final-P003-regression/evaluation.md), [P001](../runs/20261102T232009Z-connection-verified-P001-regression/evaluation.md) | Both 1/1. |
| Rate bands, `b11e9b63…` | [P021 trial A](../runs/20261102T232742Z-policy-bands-P021-a/evaluation.md), [fresh repeat](../runs/20261102T233043Z-policy-bands-P021-controlled-repeat/evaluation.md) | Both valid trials 1/1, with exact times, delay, amount, payment and traveler evidence. |
| Same revision | [P003](../runs/20261102T232743Z-policy-bands-P003-regression/evaluation.md) | 1/1. |
| Same revision | [P001](../runs/20261102T233045Z-policy-bands-P001-final/evaluation.md) | 0/1, penalty 0.50. The model used the writing date instead of the relative travel date, then contradicted its positive calculation with no-claim. The final instruction and consistency guard address these errors. |
| Current source, `b0cf4c5d…` | [Interrupted P001](../runs/20261102T233722Z-date-check-P001/evaluation.md) | Correct travel date and EUR 12.50 were established, but the Codex responder reached its usage limit. The mailbox timed out before filing; the raw 0/1 remains recorded. Fresh current-source checks subsequently passed as shown above. |

Two additional trials were invalid relay executions: [null placeholder calls](../runs/20261102T231654Z-connection-final-P021-a/evaluation.md) and [an empty response object](../runs/20261102T232743Z-policy-bands-P021-b/evaluation.md). They are preserved, including their raw zero scores. The first responder confirmed that it had generated placeholders programmatically instead of selecting actions. The driver now rejects invalid function names immediately while still passing malformed argument strings to the assistant's normal correction loop. Both behaviors were checked without model calls.

The fresh repeated P021 responder chose one action per turn; the parent only published its JSON and corrected one repeated call ID before publication. No business decisions or expected answers were supplied by the parent. Other responders were occasionally resumed with lifecycle instructions when they stopped before the runner finished. No responder received practice labels or source inspection.

## Scope and reproducibility

`CONNECTION_FIX_RESULTS.json` contains all 16 development runs from this correction, every source hash, official score, penalty and infrastructure note, plus the independent field audits. Each run preserves its agent source, relay driver, manifest, messages, event log and summary. Folder timestamps are UTC; this record uses Europe/Berlin dates.

The resolver handles a single change on a through ticket when the onward train is unknown. It uses counted train tools, normally eight calls, and searches at most two days while reserving four calls for remaining work. Truncated searches and unavailable required arrivals produce errors. Candidates need recorded departure times. Known onward trains, separate tickets and more complex journeys still use the ordinary train tools.

The model must still identify the right policy, extract all relevant rate thresholds, provide the correct price basis, and map facts to unfamiliar forms. The time-field guard compares the two final time values for common forms; it does not independently interpret every form label. Broader development coverage and reserved regression cases remain necessary.

API charges were $0; Codex usage and token counts were not measured. The relay's surrounding instructions, serialization and timing differ from raw API calls. The interrupted and invalid relay trials are not silently counted as successful model decisions. Runs across changing revisions are not combined into a pass^k estimate or benchmark-wide accuracy. The reserved cases have not had model runs, and course API validation still awaits team confirmation.
