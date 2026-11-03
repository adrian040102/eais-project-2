# Agent-loop validation

Validated on 1 November 2026 in the existing Python 3.10.22 Docker image.

## Implemented scope

`team/system.py` now contains the required rule-index builder, tool definitions, agent loop, and stopping rule. It remains one file: 331 lines, including instructions and tool schemas. `run_agent` is 42 lines. The model uses the documented course gateway; no separate agent framework is involved.

The dispatcher permits only advertised tools. It tracks previous answers, read forms, returned train IDs, retrieved rule evidence, and accepted decisions. Local arithmetic uses decimal rounding and whole-minute timestamp differences. The loop returns an incomplete result when model access fails or a call/context limit is reached; it never substitutes a no-claim declaration.

## Tests

All **51 tests passed** through `run.ps1 -Action Test` in the prepared Linux/Python 3.10 container. This includes 22 loop/arithmetic tests and the existing 29 retrieval, scoring, reporting, and split tests.

Scripted model tests cover:

- Unchanged assistant-message replay, including encrypted reasoning, with matching tool-call IDs.
- Rule search, dynamic forms, and filing prerequisites.
- Malformed JSON, wrong value types, unsupported tool names, and invalid response structures.
- Rejected filing correction, duplicate prevention, and multiple tickets before completion.
- Repeated-question caching and explicit claim/no-claim completion.
- Budget exhaustion, processing of the last allowed response, propagated kit limits, and bounded loops.
- Cent rounding, minimum payout arithmetic, minute flooring, and crossing midnight.

Model responses in these tests are predetermined. Passing them validates controller behavior, not a real model's policy interpretation or accuracy.

## Official-runner integration

The final source ran on all 26 development cases with model calls explicitly disabled. Setup succeeded, the cache loaded in each fresh request process, all request execution statuses were `ok`, and report coverage was complete.

Each request attempted one model call and received the expected disabled-access error. No external model request was sent, no counted business tools were called, and cost was $0. The resulting 0/26 is an error-handling check, not a performance measurement.

- [Report](../runs/20261101T170346Z-1-dev-system/evaluation.md)
- [Official summary](../runs/20261101T170346Z-1-dev-system/summary.json)
- [Run manifest](../runs/20261101T170346Z-1-dev-system/run_manifest.json)

The source SHA-256 matches the final run manifest: `0fb33ef985b298fba4093c6750eb925df1670216c5a1b0140757ab3863f3ed07`. All 94 manifest-listed course files remain unchanged.

## Next verification

Real-model access, actual task success, unseen-company generalization, and repeated-trial reliability remain untested. The next step is a small course-model run on development cases, followed by inspection of the tool traces and official score.

## Live access attempt on 1 November 2026

A user-authorized temporary token was tested with the kit's two-model access probe, capped at $0.005. Both models returned HTTP 403 because the course account was not in a confirmed Project 2 team. No tokens were processed and actual spend was $0. The agent cases were not attempted while access remained blocked. All 51 local tests passed again.

The [access-check report](../runs/20261101T202245Z-12424-access-probe/access_check.md) records the result. Course team confirmation is an external prerequisite; no agent changes were made to address this server-side restriction.

## Working revision on 2 November 2026

The earlier 331-line source and its run snapshots remain preserved. The current working agent is 358 lines, with 13 top-level functions and the same 42-line main loop. It requires an explicit payout minimum, blocks filing after a zero calculation, and validates delay timestamps against train evidence. Instructions clarify train-category policies, actual onward journeys, known facts, and payment choices.

All 56 project tests passed in the existing Python 3.10 Docker environment. The current source SHA-256 is `11036619a0bd80bb1cfec36e207b69988a4c2bc5a2dbf785223ad56ca2367639`. The [relay development record](RELAY_DEVELOPMENT.md) links every tested source snapshot and outcome. P003 reached a correct no-claim decision, and P001 passed a regression check after the first revision. P021 remains unreliable: an official 1/1 result had a timestamp discrepancy, and the final guarded run exhausted the model allowance without a valid claim. The supplied scorer's results are preserved alongside that independent trace audit. Course API validation remains pending.

## Correction on 3 November 2026

The current source is `b0cf4c5d985d42fecf7f22d2e80bdecae7466ccb3611f977cfa631711ce1298f`: 486 lines, 15 top-level functions and the same 42-line main loop. A deterministic connection resolver and rate-band selection replace the previously unreliable journey arithmetic. Cached clarifications establish onward travel and payment preferences. Observed train names resolve to unique IDs, and a positive calculation blocks an inconsistent no-claim declaration.

All 72 local tests passed. Fresh current-source relay checks on P001, P003 and P021 each scored 1/1; exact arrival, delay, payout and payment fields were audited independently. All 94 supplied course files still match their manifest. The [correction validation](CONNECTION_FIX_VALIDATION.md) records every tested revision, failed attempt, invalid relay and usage-limit interruption. These selected checks do not establish full benchmark reliability or raw course API compatibility.
