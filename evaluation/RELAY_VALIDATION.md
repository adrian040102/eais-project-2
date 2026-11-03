# Supervised Codex relay validation — 2 November 2026

The unchanged assistant was run in the existing Docker image with networking disabled. Its `tools.llm` messages and tool schemas were exported by `relay_test.py`; a GPT-6 Luna Codex subagent with low reasoning supplied assistant messages. The supplied runner executed the actual tools and scored the decisions. The responder received only mailbox messages and schemas, with no practice labels or source inspection.

Both runs used agent source SHA-256 `0fb33ef985b298fba4093c6750eb925df1670216c5a1b0140757ab3863f3ed07`. The current submission source remains unchanged.

| Run | Result | Interpretation |
|---|---|---|
| `../runs/20261102T214905Z-codex-relay-P001` | P001: 0/1; 10 relay requests; one relay error | The relay rejected malformed nested JSON arguments before the assistant could return a tool error and let the model correct them. This was a relay defect. The evidence is preserved. |
| `../runs/20261102T215204Z-codex-relay-P001` | P001: 1/1; fully right; 10 relay requests; 6 counted tool calls; 1 of 3 allowed questions | With the handoff corrected, the supplied scorer confirmed the claim's amount, train, provenance, and payment fields. No extra filings or excess questions. |

The fix leaves argument validation to the actual assistant, matching its normal correction loop. A focused regression replayed the first run's exact malformed argument message through the corrected gateway and verified that it reached the assistant unchanged. Python 3.10 syntax validation also passed. No submitted agent code or supplied framework files were edited.

The second run's [summary](../runs/20261102T215204Z-codex-relay-P001/summary.json), [manifest](../runs/20261102T215204Z-codex-relay-P001/relay_manifest.json), and [event log](../runs/20261102T215204Z-codex-relay-P001/log.jsonl) preserve the result and configuration.

## Meaning and limits

- No course or personal API requests were made, so API charges were $0. The test consumed Codex usage; that usage and token counts were not measured. Zero token counters in the raw runner summary mean unmeasured, not zero model work.
- This was one development case, not the full benchmark or a repeated-trial reliability estimate. The initial relay failure remains recorded separately.
- Codex supplies surrounding instructions and serializes JSON differently from the raw API. Output-token enforcement and latency also differ. The result verifies this supervised trajectory and the tool integration, not course API access, raw API cost, or equivalent model performance.
- The normal submission still calls only `tools.llm`. The mailbox helper is outside `team/` and requires a Codex responder during the local test.

## Subsequent development

The source-preservation statements above describe the first P001 experiment. Later on 2 November, the working agent was revised after P003 and P021 tests. Those revisions and every outcome are recorded separately in [RELAY_DEVELOPMENT.md](RELAY_DEVELOPMENT.md); the original run snapshots and summaries above remain unchanged.
