# EAIS Project 2: train-delay compensation assistant

This repository contains the supplied course framework, a separate evaluation harness, and our working assistant in [`team/system.py`](team/system.py). The assistant retrieves rules, resolves train journeys, calculates compensation, and files through the supplied tools. The original template in `eais-p2-kit/team/system.py` remains unchanged; the root `team/` folder is the working solution.

EvalGen and τ-bench inform the agent and evaluation design. See [DESIGN.md](DESIGN.md) for the papers and design decisions, and [WALKTHROUGH.md](WALKTHROUGH.md) for the code explanation.

## Start here

- The current source passes 72 local tests. Selected P001, P003 and P021 model-relay checks scored 3/3, with arrival times and payout fields audited separately. These selected checks do not establish full benchmark performance or course API compatibility. See [the validation record](evaluation/CONNECTION_FIX_VALIDATION.md).
- [`p2-upload-2026-11-03-v1.zip`](p2-upload-2026-11-03-v1.zip) is the prepared Gradescope upload. It contains only `team/system.py` and passed the supplied upload checker with model calls disabled. The frozen source and packaging manifest are in [`submissions/`](submissions/).
- Compressed source train files are included. The generated database is not tracked; the setup command below builds it locally, using approximately 0.7 GB.
- Existing experiment evidence is included in `runs/`. That folder is ignored for new Git additions so newly generated experiments are not accidentally published. Historical source copies there describe earlier revisions; edit `team/system.py` for current work.
- API credentials are not included. Live course calls need a confirmed three-person team and its course token. The Codex relay helper requires an active responder in a Codex session and does not run by itself.

## Run from Windows with Docker

Install and start Docker Desktop with Linux containers, open PowerShell at the repository root, and run:

```powershell
.\run.ps1 -Action Setup
.\run.ps1 -Action Test
.\run.ps1 -Split dev
```

Setup builds the Python 3.10 runtime and the train store once. Evaluation also prepares either prerequisite if it is missing. The default evaluation uses our working `team/system.py` with model calls disabled. The loop reports incomplete processing when a model call is disabled; it does not substitute a no-claim decision. A zero score is therefore expected in this mode.

Read [WALKTHROUGH.md](WALKTHROUGH.md) for the assistant's explanation. To try rule search directly:

```powershell
.\run.ps1 -Action Search -Query "Entschadigungsbetrage unter 4 ausgezahlt" -Company DB
```

Other commands:

```powershell
.\run.ps1 -Action Status
.\run.ps1 -Split all
.\run.ps1 -Split dev -System eais-p2-kit/examples/example_system.py
```

For paid model calls, set `P2_BASE_URL` and `P2_TOKEN` in the current PowerShell session using the course app's instructions, then explicitly enable them:

```powershell
.\run.ps1 -Split dev -Model -MaxCostUsd 0.25
```

The token is forwarded to the container by environment-variable name and is not saved in project files or command arguments. Containers are removed after each command. The project folder is mounted so that the train store, agent edits, and reports persist on Windows. No separate Ubuntu distribution or Linux terminal is needed. Docker's existing WSL backend must be working.

For a small live access check, `python -X utf8 evaluation/live_test.py --probe --cap 0.005` reads a temporary token through a hidden prompt. Explicit development IDs can be tested with `--cases P001,P003,P021 --cap 0.065`; that mode preserves a source snapshot and creates a separate smoke-test report. It never runs the whole benchmark implicitly. The course requires all three team members to confirm their Project 2 team before either model can be used.

This runs the official evaluator without bubblewrap. It verifies tool use, scoring, and request processes, but does not reproduce the grading sandbox's filesystem and network isolation. The image runs as an unprivileged user and contains only the dependency manifests; the course data and source are read from the project folder. For an exact sandbox check, use the course's Linux/bubblewrap workflow separately.

The base image is pinned by digest in `runtime/Dockerfile`; course package versions come directly from the unchanged `packages.txt`. `-Action Setup` can rebuild the image after an intentional dependency or Dockerfile change, and preserves an existing train store. See [runtime/VALIDATION.md](runtime/VALIDATION.md) for live validation results.

| Path | Purpose |
|---|---|
| `eais-p2-kit/` | Supplied course code, rules, forms, practice labels, and train files |
| `team/system.py` | Our working submission source: rule search, model/tool loop, schemas, and arithmetic |
| `evaluation/evaluate.py` | Fixed-split launcher and report command |
| `evaluation/reporting.py` | Diagnostics over the official runner's outputs |
| `evaluation/splits.json` | Versioned development and reserved regression IDs |
| `evaluation/inventory.json` | Dataset and split composition |
| `evaluation/tests/` | Scoring, reporting, and split validation |
| `runs/` | Local run outputs, created when a benchmark is launched |
| `run.ps1` | PowerShell launcher for the Docker runtime |
| `runtime/` | Container definition and live validation record |

The supplied kit's 94 manifest-listed files remain unchanged. The original download ZIP had SHA-256 `a6865391d4554faf4e68c2a51ca2908e62a073241429426de477b169bfa4be0a`. This repository includes the kit contents and compressed source train files; it omits the generated SQLite database and Python caches. `.gitattributes` preserves file bytes because the course manifest and evaluation split use SHA-256 checks.

## Dataset and fixed split

The kit has 36 labelled requests, worth 36 points in total: 26 claim cases and 10 cases where nothing is owed.

| Split | Requests | Claim / no claim | Companies | Case types |
|---|---:|---:|---:|---:|
| `dev` | 26 | 20 / 6 | 6 | 11 |
| `holdout` | 10 | 6 / 4 | 6 | 10 |
| `all` | 36 | 26 / 10 | 6 | 11 |

Use `dev` while changing prompts, retrieval, and control flow. Use `holdout` after a meaningful development milestone, and `all` for a final regression run. The split is fixed by ID, with no random seed. The sole separate-ticket missed-connection example, P024, remains in development because that category has only one example.

The public labels were inspected before fixing this split. The holdout is a reserved regression set, not an unbiased estimate of hidden-test performance. All labelled travel dates are October 19-22, so the split does not test unseen dates. Hidden companies and new forms require additional robustness evaluation later.

The harness checks the practice JSON's SHA-256 and rejects dataset drift. A changed practice dataset requires a reviewed new split version. Labels, split metadata, and reports stay outside `team/`; they are development assets and are not submission files.

## Inventory and tests on Windows

Run these commands from this `Projekt 2` directory. Inventory and report commands need only the Python standard library. The full test suite now includes rule retrieval and uses the prepared Docker environment and its course packages.

```powershell
python -I evaluation/evaluate.py inventory
.\run.ps1 -Action Test
```

The supplied practice runner uses Unix facilities including `resource`, `os.statvfs`, and process groups. Native Windows can inspect labels, run these scoring/reporting tests, and summarize existing results; it cannot launch the official runner unchanged. Initial validation used the available Windows Python 3.13.5. Python 3.10 syntax checks are included in the validation record, but do not establish Python 3.10 runtime or Linux sandbox compatibility.

Platform clarification, 31 October 2026: the ordinary runner is Unix-based, not Linux-only. The kit explicitly discusses Mac setup; source inspection indicates normal runs without `--sandbox` are intended to work on macOS, although this has not been tested here. The bubblewrap sandbox specifically requires Linux. Windows users can run the unchanged kit inside an Ubuntu WSL environment on their Windows machine; a Linux laptop is not required. See [Microsoft's WSL explanation](https://learn.microsoft.com/en-us/windows/wsl/about) and the supplied kit README's setup and sandbox sections.

## Alternative: run directly under Linux or WSL

Use Python 3.10, matching grading. From this project directory, create the environment and install the kit's pinned packages:

```bash
uv venv --seed --python 3.10 .venv
source .venv/bin/activate
python -m pip install --no-deps -r eais-p2-kit/packages.txt -r eais-p2-kit/requirements.txt
python -I eais-p2-kit/make_store.py
```

The train store is built once and needs about 0.7 GB, according to the kit. The harness does not install software, build the store, or start containers automatically.

Run the current system with model calls disabled:

```bash
python -I evaluation/evaluate.py run --system team/system.py --split dev
```

To enable paid course model calls, set `P2_BASE_URL` and `P2_TOKEN` in the shell as described in the kit README, and explicitly add `--model`:

```bash
python -I evaluation/evaluate.py run --system team/system.py --split dev --model --max-cost-usd 0.25
```

`--max-cost-usd` applies to the whole run. Both course environment variables are required with `--model`; the harness never saves their values. Without `--model`, it always passes `--no-model` to the official runner. Use `--split holdout` or `--split all` to select the other sets. Add `--sandbox` when Linux has bubblewrap configured; this exercises the course sandbox as well.

Each invocation creates a fresh run directory. `--out PATH` selects a custom directory, which must not exist. Each run records the selected IDs, dataset and split hashes, system entry-point hash, Python version, limits, and runner command in `run_manifest.json`. The system hash covers `system.py`; changes to helper modules or external configuration need a separate source snapshot for full reproducibility.

## Reports

The official runner creates `summary.json` and `log.jsonl`. The harness preserves those files and adds:

- `evaluation.md`: readable score, coverage, failure counts, grouped results, and case details.
- `evaluation.json`: the same diagnostics as structured data.
- `cases.csv`: per-case points, penalties, status, questions, calls, cost, and runtime.

Reports include amount, train, and provenance errors; missing facts; incorrect ticket prices; missing claims; no-claim evidence; wrong declarations; extra filings; excess questions; runtime and call-limit failures; model errors; and cost. Combined issue codes such as `ATP` count in each applicable category. Missing claims are counted separately from those three checks.

The course scorer remains authoritative. The report uses its row scores and penalties, rather than implementing compensation rules. Overall points follow `max(0, sum(row.points) - sum(row.penalty))`. Negative per-case net contributions are retained until the final floor. Wrong no-claim declarations are not charged twice as extra filings. Missing and not-run cases retain their full maximum in the selected denominator.

Runtime statistics cover attempted requests and exclude setup. Whole-run cost uses `summary.usd`, which includes setup when applicable. Company groups describe requests involving that company; future multi-company requests can appear in multiple groups. Cost, latency, and correctness should be reviewed together.

To summarize a run made directly with the course runner, supply the intended full split explicitly:

```powershell
python -I evaluation/evaluate.py report --run "runs/my-run" --split dev
```

For a run launched by this harness, the split is read from `run_manifest.json`, so `--split` can be omitted. A missing event log still allows a score report, but detailed missing-topic explanations are unavailable. Incomplete or unexpected coverage gives exit code 2 after writing the report; zero score alone does not cause a command failure. A completed run can still have failing request statuses, which are shown in the report.

## Initial validation

See `evaluation/VALIDATION.md` for the initial Windows checks and `runtime/VALIDATION.md` for subsequent Docker runs. The unit-test scoring fixtures use synthetic events and are distinct from actual practice runs. No paid model calls have been made during setup.

## Supervised model relay in Codex

`evaluation/relay_test.py` supports a small local diagnostic during a Codex session. It snapshots the unchanged `team/` source, runs one development case through the supplied runner, and exports each `tools.llm` request into a mailbox. A GPT-6 Luna subagent with low reasoning reads those messages and schemas and supplies assistant messages as JSON; the actual assistant executes the tools. The model receives no practice labels. The supplied scorer assesses the resulting decisions.

This helper stays outside the submission. It requires an active Codex responder and consumes Codex usage; it is not a standalone API service. It makes no course or personal API requests. `relay_manifest.json` records the source, settings, and limitations. A zero in the raw summary's token counters means unmeasured tokens for this relay; the zero dollar amount covers API charges only. The Codex agent's surrounding instructions, JSON serialization, latency, and token limits differ from raw API calls, so relay results do not establish course API access, cost, or model performance. See [the relay validation record](evaluation/RELAY_VALIDATION.md) for the first smoke test.

The [3 November correction record](evaluation/CONNECTION_FIX_VALIDATION.md) describes the connection and calculation fixes, 72 passing local tests, and current-source P001/P003/P021 checks. It preserves unsuccessful revisions and infrastructure failures separately. `WALKTHROUGH.md` explains the connection helper and rate-band calculation for code review.
