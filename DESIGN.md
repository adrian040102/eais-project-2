# Research-guided design

The user selected these two papers as design references on 30 October 2026. The course contract, allowed tools, and official scorer define the assignment's requirements. The architectural choices below are our engineering interpretation of the papers for this task.

## Evaluator validity

Shankar et al., [Who Validates the Validators? Aligning LLM-Assisted Evaluation of LLM Outputs with Human Preferences](https://arxiv.org/pdf/2404.12272), introduce EvalGen: evaluation assertions are checked against human grades. They observe criteria drift as people refine their standards while examining outputs. Relevant sections: 4, 7.3, and 8.

For this project:

- Keep the official score fixed. Additional diagnostics can evolve independently, with versioned definitions and regression fixtures.
- Check each new diagnostic on successful and failed trajectories. Review false alarms as well as missed failures.
- If an LLM-based diagnostic is introduced, compare it with manually reviewed examples before relying on it. Report disagreements; retain deterministic checks for numerical and structural conditions.
- Use reviewed failures to refine diagnostics and the agent, while preserving the meaning of earlier results. Holdout results remain separate from development tuning.

## Interaction reliability

Yao et al., [τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains](https://arxiv.org/pdf/2406.12045), evaluate policy-guided agents through user/tool interaction and resulting state. Their pass^k metric measures success in every one of k independent trials; pass@k measures at least one success. Final-state reward can miss violations during a trajectory. Relevant sections: 3 and 5.2.

For repeated EAIS runs, retain the course score and add per-case success frequencies. A trial's strict success follows the official fully-right condition: all expected decisions correct, no extra filings or declarations, no excess questions, and no carried penalty. Keep execution status visible separately.

For n trials with c successes on a task, estimate pass^k with `comb(c, k) / comb(n, k)`, then average across tasks. Require n >= k. Do not raise aggregate accuracy to k or select the best trial. Flag incomplete repetitions explicitly.

EAIS has fixed topic-based traveler answers; repeats vary agent behavior, without reproducing τ-bench's stochastic user simulation. Paraphrases belong in a separate, verified robustness dataset.

## Agent structure for EAIS

The user must explain this code in a professor-led code review. Readability and code length are explicit requirements. The first implementation should use one clear model/tool loop and small helper functions, initially in `team/system.py`. Separate retrieval into a small module only when that makes the code easier to follow. Aim for a few hundred lines initially, counting tool descriptions and prompts, without compressing code to meet an arbitrary limit.

Use ordinary dictionaries, lists, and explicit branches. BM25 keyword retrieval, the provided model interface, and a short tool dispatcher are sufficient starting components. Defer separate planning/critic agents, reranking, policy compilation, and a generic workflow engine. The table below describes information and checks the solution needs; it does not require separate classes or a formal state machine. Add sophistication only for an observed failure, with a plain explanation and focused validation.

These decisions follow the local scoring and interface constraints:

| Component | Intended behavior |
|---|---|
| Request state | Track each ticket, planned/actual legs, resolved trains, claim recipient, and completion independently. |
| Evidence | Record facts from the request or traveler, tool-returned train IDs, and retrieved document sections with event order. |
| Rule interpretation | Retrieve policy evidence before choosing compensation or no compensation. Support documents supplied during setup and forms discovered during solving. |
| Calculations | Use deterministic date/time and decimal arithmetic after resolving the applicable policy and ticket price basis. |
| Filing | Validate fields, needed facts, and train identity before filing. Preserve accepted filings to prevent duplicates. |
| Stopping | Check every relevant ticket; budget exhaustion is incomplete processing. A no-claim decision requires its own supporting evidence. |

Questions use the kit's allowed topics. Any required missing facts must be obtained before the corresponding decision. The agent cannot inspect practice labels. Evaluate valid alternative tool sequences by outcomes and required evidence, without demanding one exact reference sequence.

## Implementation status

The current harness uses the official scorer and provides failure categories, evidence details, costs, runtime, fixed splits, and scoring regression tests. The working agent in `team/system.py` builds a BM25 index and runs one explicit model/tool loop. Simulated responses test control flow. Supervised Codex relay runs exercise selected model decisions; they differ from raw course API calls. The [2 November record](evaluation/RELAY_DEVELOPMENT.md) preserves failures and a timestamp error the official score missed. The [3 November correction](evaluation/CONNECTION_FIX_VALIDATION.md) adds one timetable-based connection helper, cached factual clarifications, deterministic selection from model-extracted rate bands, and consistent filing/no-claim checks. Current-source P001, P003 and P021 runs passed, including separate field audits, and all 72 local tests passed. The model still interprets policy applicability and extracts the table; this is not a general policy compiler. Course API validation and broader task performance remain pending. Repeated-trial aggregation remains planned. No τ-bench runtime or LLM user simulator has been installed.
