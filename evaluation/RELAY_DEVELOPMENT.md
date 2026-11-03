# Relay development tests — 2 November 2026

This is the historical 2 November development stage. The working source was subsequently corrected; see [3 November validation](CONNECTION_FIX_VALIDATION.md) for current results. The source hashes and failures below remain preserved.

Two development cases were tested with fresh GPT-6 Luna Codex responders at low reasoning: P003 (minimum payout) and P021 (missed connection on a through ticket). P001 was checked again after the first revision. All original summaries, source snapshots, mailbox messages, and failed attempts are retained. These are supervised relay diagnostics, not raw course API runs.

## Results by revision

Scores below come from the supplied scorer. The penalty column retains the course penalty before its overall zero floor. A successful execution status alone does not mean a correct decision.

| Revision | Case | Points | Penalty | Questions | Model requests | Finding |
|---|---|---:|---:|---:|---:|---|
| Original | [P003](../runs/20261102T222001Z-codex-relay-P003/evaluation.md) | 0/1 | 0.50 | 3/2 | 9 | Ignored the retrieved minimum, filed a claim, and exceeded the question allowance. |
| Original | [P021](../runs/20261102T222001Z-codex-relay-P021/evaluation.md) | 0/1 | 0 | 4/5 | 13 | Wrong amount and payment choice; selected the wrong replacement arrival. |
| Explicit minimum and revised instructions | [P003](../runs/20261102T222513Z-codex-relay-P003/evaluation.md) | 0/1 | 0.50 | 1/2 | 10 | Calculated zero correctly but still filed a zero-value claim. |
| Explicit minimum and revised instructions | [P021](../runs/20261102T222513Z-codex-relay-P021/evaluation.md) | 0/1 | 0 | 3/5 | 12 | Used the missed train as the actual onward journey; missing traveler evidence. |
| Explicit minimum and revised instructions | [P001](../runs/20261102T222611Z-codex-relay-P001/evaluation.md) | 1/1 | 0 | 1/3 | 10 | Previously passing direct-trip case remained correct. |
| Zero-payout guard and explicit onward-journey instruction | [P003](../runs/20261102T223029Z-codex-relay-P003/evaluation.md) | 1/1 | 0 | 1/2 | 9 | Correct no-claim decision with train evidence, no filing, and no question penalty. |
| Zero-payout guard and explicit onward-journey instruction | [P021](../runs/20261102T223030Z-codex-relay-P021/evaluation.md) | 1/1 | 0 | 4/5 | 14 | Correct amount, recipient, train and payment, but manual review found an unsupported planned arrival. |
| Timestamp evidence guard (current source) | [P021](../runs/20261102T224116Z-codex-relay-P021/evaluation.md) | 0/1 | 0 | 4/5 | 15 | Invented timestamp inputs were rejected; the responder failed to recover and exhausted the model allowance without a valid claim. |

## Changes retained in the working agent

- The calculator requires an explicit minimum. Its schema explains percentage units and when zero is appropriate.
- Filing is blocked after the most recent calculation returns zero. A later calculation can establish a payable amount for another ticket.
- Delay calculations accept only full timestamps observed through the train tools, including nested stop records. Equivalent ISO formatting is accepted.
- Instructions emphasize existing facts, train-category applicability, final-destination delay, actual onward travel, and an explicit payment preference when a payable claim offers a choice.

There are no case IDs, expected payouts, or company-specific compensation rates in these changes. The working file has **358 lines, 13 top-level functions, and the same 42-line main loop**. The additional state is the most recent payout and observed timestamps.

## What the scorer missed

The third revision's P021 filing used planned arrival **16:00** and a delay of **213 minutes**. The train evidence and public reference give **15:50**, actual arrival **19:33**, and **223 minutes**. The official amount remained correct because both delays fall within the same compensation bracket. The course scorer awarded 1/1; that result is preserved, with the timestamp discrepancy recorded separately. It motivated the timestamp guard and its regression tests.

The guard verifies that a calculator timestamp was observed, not that the model selected the correct leg. It also does not independently validate every time field subsequently written to a form. P021 remains unresolved as a reliable end-to-end trajectory at this relay setting. The final failed run must not be replaced by the earlier passing score.

## Verification and limits

All **56 local tests passed** in the existing Python 3.10 Docker environment. New regressions cover omitted minimums, rejection of zero-value filings, a later payable ticket after a zero payout, invented timestamps, equivalent ISO formatting, and timestamps nested inside train stops.

Source hashes:

| Revision | SHA-256 |
|---|---|
| Original | `0fb33ef985b298fba4093c6750eb925df1670216c5a1b0140757ab3863f3ed07` |
| Explicit minimum and revised instructions | `d8528b8e419ec7b6130b2cfdf8be6ba4b71dd9a66823720dbb9fecea5c02299c` |
| Zero-payout guard | `676d612f76cfd087de4e44520ac296c4343a9cab350ae477ee089739a7313b73` |
| Timestamp evidence guard (current) | `11036619a0bd80bb1cfec36e207b69988a4c2bc5a2dbf785223ad56ca2367639` |

No course or personal API requests were made: containers ran with networking disabled. API charges were $0; Codex usage and token counts were not measured. Raw summary token zeros are placeholders for this relay. The responders received no labels or source inspection. One P003 responder was resumed after stopping before the runner finished; the parent supplied no task-decision guidance.

These runs used changing source versions and are not repetitions of one fixed configuration. Do not aggregate them into pass^k, a benchmark-wide accuracy, or an unbiased model-performance estimate. The reserved regression cases were not run. Course API access, actual API cost/latency, broader coverage, and fixed-source repeated-trial reliability remain pending.
