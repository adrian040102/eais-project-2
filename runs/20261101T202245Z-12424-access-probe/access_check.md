# Live model access check

On 1 November 2026, the supplied course probe reached the course service, but both model requests were rejected.

| Model | Result | Tokens processed | Cost |
|---|---|---:|---:|
| gpt-4o-mini | HTTP 403: Project 2 team not confirmed | 0 | $0 |
| gpt-6-luna | HTTP 403: Project 2 team not confirmed | 0 | $0 |

The service reports: “You aren't in a confirmed Project 2 team yet.” The supplied README states that all three members must confirm their team on the course app's Project 2 page before model calls are enabled.

The probe cap was $0.005; actual spend was $0. No agent cases were attempted after this access failure. The local Docker/Python 3.10 test suite still passed all 51 tests. Live agent accuracy remains untested.

The token was read without terminal echo, held in process memory, and forwarded by environment-variable name. It was not written to project files. The saved manifests and logs were checked for a persisted token pattern and none was found.

Next action: confirm the course team, including all three members' confirmations, then retry the access probe before running the selected development cases.

[Official summary](summary.json) · [Tool/model trace](log.jsonl)
