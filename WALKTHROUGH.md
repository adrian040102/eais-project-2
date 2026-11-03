# Our assistant: rule search and the model/tool loop

The working solution is in `team/system.py`. The supplied starter kit remains unchanged. The code has rule retrieval and the model/tool loop. Simulated responses test its mechanics, and supervised Codex relay tests exercise actual model decisions. Course API validation is still pending.

## The idea

The documents are too large to put into every model call. We prepare a searchable collection once, then select a few relevant passages for each query.

1. Get the available documents through the course tools.
2. Split them into passages of up to 300 words, preserving their heading context.
3. Normalize search words and build a BM25 keyword index.
4. Save the prepared index in the course cache.
5. In each fresh request process, load the cache and return the best matching passages.

BM25 gives more weight to useful, uncommon search words and adjusts for passage length. We use the supplied `rank_bm25` package for this calculation. Our code controls which text is indexed and which evidence is returned.

## Functions to explain

| Function | Responsibility |
|---|---|
| `tokenize` | Lowercase words, remove accent differences, and separate words/numbers. For example, `ENTSCHÄDIGUNG` becomes `entschadigung`. |
| `split_passages` | Preserve the section heading, then divide its text into overlapping passages. The last 50 words also appear in the next passage to help preserve conditions crossing a boundary. |
| `build_rule_index` | Read all documents, collect their passages, create BM25, and save it. Newly supplied companies are included automatically. |
| `load_rule_index` | Read the prepared index from disk. This is necessary because every request runs in a fresh process. |
| `search_rules` | Rank matching passages and return their document ID, company, title, language, section, text, and score. |
| `setup` | Build the index once per run. |
| `solve` | Entry point that calls `run_agent` for one traveler request. |

The cache uses Python's `pickle` format because it stores the prepared BM25 object together with the text. It is created by our own setup code. A temporary file is renamed after a successful write, so a failed rebuild does not replace an existing complete cache.

## Try it

With Docker Desktop running, execute this from `Projekt 2`:

```powershell
.\run.ps1 -Action Search -Query "Entschadigungsbetrage unter 4 ausgezahlt" -Company DB
```

The first result currently contains DB's compensation thresholds and minimum payout in its original rule text. These amounts are not hardcoded in our assistant.

Omit `-Company` when the query may require EU law or cross-border conditions. A company filter is strict; filtering to DB excludes documents assigned to EU or Cross-border.

## What was checked

The test suite checks body evidence for DB, OUIGO, SBB, EU through-tickets, and cross-border continuation. It also checks a synthetic new operator, accent/case normalization, empty queries, overlapping text, cache reload, read-only searching, and failed document reads. No practice answer labels are used in these search checks.

Relevance tests accept equivalent official sources: the DB minimum-payout condition appears both in its full conditions and its passenger-rights summary. Returning the summary is a valid match.

At the retrieval milestone on 31 October 2026, all 29 project tests passed in the Python 3.10 Docker environment. The official runner also completed all 26 development requests without execution errors: setup indexed 45 documents into 3,163 passages in about 1.2 seconds, using a 6.8 MB cache. Request processes loaded and searched that cache successfully. That version's [development report](runs/20261031T131126Z-1-dev-system/evaluation.md) was 0/26 because it made no claim decisions. Model cost was $0. That version had 105 lines and seven functions; all supplied kit files remained unchanged.

## Second component: using the tools

`run_agent` maintains one list of messages. It starts with instructions and the traveler request. On each round it sends this history to the model, appends the complete model response, executes requested tools, and appends their results. The original assistant message is replayed unchanged, including the course wrapper's encrypted reasoning field. Each tool result uses the matching `tool_call_id` so the model can associate requests with results.

| Function | Responsibility |
|---|---|
| `tool_definitions` | Describe function names, purposes, and JSON arguments. Form fields are discovered at runtime. |
| `execute_tool` | Run advertised functions and return readable errors that the model can correct. |
| `run_agent` | Own the conversation, make model calls, and return tool results to the model. |
| `should_stop` | Stop after explicit completion or when the course call budgets are exhausted. |
| `calculate_compensation` | Apply a supplied percentage with decimal arithmetic, round to cents, then check the supplied minimum. |
| `calculate_delay` | Subtract full timestamps and round down to whole minutes. |
| `resolve_connection` | Find the incoming and missed trains, choose the first feasible replacement, and compute the final journey delay. |
| `ask_once` | Cache traveler answers for both model-requested questions and questions requested by local helpers. |

The instruction string contains task procedure and the course's fixed connection rules. Compensation percentages, minimums, exclusions, and recipient choices must come from retrieved documents. An arithmetic helper does not decide whether the policy applies.

The calculator requires an explicit minimum payout; it never silently assumes zero. The schema explains that `25` means 25 percent. The request state remembers the latest calculated payout and blocks filing when that value is zero. A new calculation replaces it, so another payable ticket can still be filed. These checks prevent common execution mistakes; the model must still select the applicable rule and minimum.

After a resolved connection, the model supplies the complete applicable compensation table as `rate_bands`, each with a starting delay and percentage. Python chooses the highest threshold not exceeding the resolved final delay, then calls the decimal calculator. A percentage guessed by the model cannot override that selection. The rates still come from the retrieved policy; the code contains no company-specific compensation percentages. A positive calculation also blocks a contradictory no-claim declaration until the model files or revises its calculation.

The request state is one dictionary: previous answers, read forms, observed train rows and timestamps, evidence flags, the latest resolved connection and calculation, and accepted decisions. Repeated questions reuse their first answer. Filing requires retrieved rules, a read form, and observed trains. An unambiguous observed train name or number is converted to its ID before duplicate checks. Ambiguous and unseen identifiers are rejected. Rejected submissions can be retried; accepted duplicates are blocked. No-claim declarations require the train evidence accepted by the course scorer and are blocked after a claim has been accepted.

The dispatcher collects timestamps from departure results, direct-train results, and nested stop records. `calculate_delay` calls must use values in that set. Parsing them as `datetime` objects accepts equivalent ISO formats while rejecting invented or rounded times. This proves that a timestamp was observed; it does not prove that the model chose the right leg.

## Resolving an unknown onward train

For a through ticket with one missed connection, the model gives `resolve_connection` the origin, change station, destination, travel date, and both scheduled departure times from the request. If no onward-travel answer has been recorded, the dispatcher asks the traveler first and returns that answer. The model then uses the helper only when the onward train is unknown; a known onward train is identified with the ordinary train tools.

The helper looks up the two scheduled departures exactly. It applies the course's minimum change time from `CHANGE_TIMES`, or five minutes at other stations, with fifteen minutes at Paris stations. These are the supplied R6 constants, not compensation policy.

It searches complete days in four-hour windows and orders feasible trains by recorded departure, which can differ from timetable order. Cancelled trains are excluded. An ordinary same-day lookup uses eight counted train calls: two scheduled departures and six windows for replacement candidates. The helper reserves four calls for remaining work and searches at most two days. Missing, ambiguous, truncated, or insufficient results produce an error rather than a guessed journey or no-claim decision.

The final delay compares the missed connection's planned destination arrival with the replacement's recorded destination arrival. The incoming late train remains the train on the claim form. For the development example: the missed train's planned arrival is 15:50, the replacement arrives at 19:33, and the final delay is 223 minutes. The model retrieves the applicable rate table, and Python applies that table to the 223-minute delay. It does not use the incoming train's 130-minute delay to select a rate.

For a resolved journey, filing checks retain those exact arrival values and delay, and require the calculated amount. Recognized payment choices trigger one cached preference question before a claim is sent; currency and departure/arrival enum fields do not trigger that question. A form offering only one payment method needs no preference question.

`finish` is a local tool. It is accepted only after an actual claim or no-claim decision was recorded. The prompt requires every ticket to be handled first; code deliberately continues after the first accepted claim to allow multiple tickets. This guard does not independently prove that the model identified every ticket.

Malformed tool arguments produce a tool error so the model can repair them. A model error, exhausted budget, or oversized context returns an explicit incomplete result. These conditions never trigger an automatic no-claim declaration. Pending tool calls from the last allowed model response are still executed.

For code review, the central explanation is: **the model proposes the next action; ordinary Python controls which functions run, preserves the evidence and conversation, and performs exact arithmetic.**

The simulated tests script model actions in advance. They test dispatch, message replay, retries, budgets, multiple tickets, guards, and arithmetic. They do not demonstrate that a real model will choose the correct rule or amount. The supervised relay runs provide separate evidence about model decisions; their setup differs from a raw course API call.

On 1 November 2026, all 51 tests passed in Docker with Python 3.10.22. The complete working file is 331 lines; the main `run_agent` loop is 42 lines. A separate official-runner check completed all 26 development requests with model access disabled, at $0 cost. See [the validation record](evaluation/AGENT_LOOP_VALIDATION.md) for scope and saved evidence. The resulting zero score checks error handling and is not a measure of real-model performance.

On 2 November, the working version grew to 358 lines while retaining 13 top-level functions and the 42-line main loop. All 56 local tests passed. Relay tests exposed minimum-payout, onward-journey, and timestamp errors. P003 achieved a correct no-claim decision; P001 passed its regression check. P021 achieved one official 1/1 result, but its timestamp audit and subsequent failed run show that it remains unreliable in this relay setting. See [the complete development record](evaluation/RELAY_DEVELOPMENT.md), including all failed revisions. These observations do not establish performance through the course API.

On 3 November, the corrected version has 486 lines and 15 top-level functions, with the main loop still 42 lines. All 72 local tests pass. Fresh current-source relay runs on P001, P003 and P021 all scored 1/1, with exact arrival times, delays, payout and payment fields audited separately. Two earlier valid runs of the rate-band revision also passed P021. The [correction record](evaluation/CONNECTION_FIX_VALIDATION.md) preserves the intermediate failures, two invalid relay runs, and the usage-limit interruption. Full benchmark reliability and course API compatibility are still pending.

## Current limits

Keyword search does not translate between languages or establish whether a rule applies to a particular ticket. Long tables can require additional surrounding context. The model must choose suitable queries and interpret applicability. Guards catch some common mistakes but do not prove that the recipient, required facts, rate, or final decision is correct; the official scorer and live evaluation are still needed.
