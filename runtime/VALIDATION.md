# Docker runtime validation

Validated on 31 October 2026 from the Windows project folder.

## Runtime

- Docker Desktop is running with Linux engine 27.1.1.
- Docker's existing WSL backend was updated to 3.0.3.0 during startup. The only installed WSL distribution after setup is `docker-desktop`; no Ubuntu distribution was installed.
- Local image: `eais-p2:py310`, Python 3.10.22, unprivileged user `eais` (uid 1000).
- The base image is pinned by digest in `Dockerfile`. Rebuilding the finalized Dockerfile used cached layers and produced the same image ID.
- All 17 course package pins match `eais-p2-kit/packages.txt`; all allowed packages and certifi imported successfully. `pip check` reported no broken requirements.
- Exact installed versions, base digest, platform, and image ID are recorded in [environment.json](environment.json).
- The train store contains all eight supplied days. It occupies 666,804,224 bytes and took 295 seconds to build. The launcher preserves it on subsequent runs.

## Checks performed

| Check | Result |
|---|---|
| PowerShell launcher parsing | Passed in PowerShell 5.1 and 7.6 |
| Spaced project path and Docker argument forwarding | Passed |
| Project tests inside Python 3.10 container | 23 passed |
| Launcher `Status` | Linux engine, runtime image, and train store ready |
| Official tool example, P001, model disabled | Setup and request status `ok`; six successful counted tool calls; one question; zero model calls |
| Official starter, all 36 cases, model disabled | Setup `ok`; all 36 requests status `ok`; complete report coverage |
| Starter score | 0/36, expected for the unimplemented starter |
| Model cost | $0 across both live checks |
| Supplied source integrity | All 94 manifest-listed files unchanged; original ZIP hash unchanged |

The tool example exercised traveler questions, departures, direct-train search, train stops, form listing, form retrieval, and the setup cache through the actual runner/process boundary. None of the counted tool calls returned an error.

The full starter baseline correctly reported 26 missing claims and ten missing no-claim declarations. Its derived report agrees with the official totals and includes all 36 cases in its CSV. Total request runtime was 5.27 seconds, excluding setup. This confirms the runtime and evaluation plumbing; the starter does not implement a claims assistant.

## Saved evidence

- [Full baseline report](../runs/20261031T122615Z-1-all-system/evaluation.md)
- [Official baseline summary](../runs/20261031T122615Z-1-all-system/summary.json)
- [Baseline run manifest](../runs/20261031T122615Z-1-all-system/run_manifest.json)
- [Official tool-example summary](../runs/20261031-132609-tool-smoke-P001/summary.json)

The outputs are local historical evidence and remain separate from the submitted `team/` folder.

## Scope

These were normal Docker runs with the official bubblewrap sandbox disabled. Grading-equivalent filesystem/network isolation has not been tested. Paid model access has not been configured or exercised, and model calls remain disabled unless the launcher receives `-Model` explicitly.

To use the prepared setup, start Docker Desktop and run from `Projekt 2`:

```powershell
.\run.ps1 -Action Status
.\run.ps1 -Action Test
.\run.ps1 -Split dev
```

## Startup repair on 1 November 2026

Docker startup initially failed because its backend could not remove a stale zero-byte OTel socket, `userAnalyticsOtlpHttp.sock`, in its Windows runtime directory. This caused the backend cancellation and secondary WSL bootstrap errors. WSL itself was already working; no additional update, installation, distribution, or Docker-data reset was needed.

After stopping the failed Docker processes, the single socket could not be renamed. The plain runtime directory was verified at `C:\Users\adria\AppData\Local\Docker\run` and preserved as its sibling `run-stale-20261101-180022`. Docker recreated its runtime endpoints and started successfully. Images, train data, settings, and historical project runs were not moved. The launcher now treats an empty engine-info response as unavailable, rather than incorrectly identifying it as Windows-container mode.

The existing Python 3.10 image subsequently passed all 51 project tests. See [agent-loop validation](../evaluation/AGENT_LOOP_VALIDATION.md) for the current code verification.
