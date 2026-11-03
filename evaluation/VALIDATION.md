# Evaluation validation

Validated on 30 October 2026 with native Windows Python 3.13.5.

## Results

- All 23 tests passed: 6 split/CLI tests, 9 reporting tests, and 8 integrations with the supplied scoring, form, and traveler modules.
- The scoring integrations use controlled synthetic tool events. They verify correct claims, facts asked before versus after filing, no-claim train evidence, question limits, and penalties across requests. They do not execute an assistant or query the live train store.
- Every authored Python file parsed using Python 3.10 syntax rules. This is a syntax check, not Python 3.10 runtime validation.
- `inventory` validated a complete, disjoint 26/10 split, both covering all six companies. Development contains all 11 case types; the reserved set contains 10.
- The CLI generated Markdown, JSON, and CSV reports from temporary scoring fixtures. Missing cases retained their maximum in the selected denominator.
- Native Windows launch was rejected with the documented Linux/WSL requirement, before model calls or a run directory were created.
- All 94 files listed in the supplied manifest retained their original SHA-256 hashes. The starter ZIP hash remained `a6865391d4554faf4e68c2a51ca2908e62a073241429426de477b169bfa4be0a`.
- Authored code and documentation were checked for leftover template placeholders. The supplied course files were preserved.

## Reproduce

From the `Projekt 2` directory:

```powershell
python -B -X utf8 -I -m unittest discover -s evaluation/tests -p "test_*.py" -v
python -I evaluation/evaluate.py inventory
```

## Remaining runtime verification

No full agent benchmark, train-store build, paid model call, Python 3.10 runtime check, or Linux sandbox run was performed. The available WSL installation listed only Docker Desktop's distribution, and its Docker engine was stopped. The official runner depends on Unix facilities unavailable in native Windows. The README documents running it unchanged under Linux/WSL with Python 3.10.

No synthetic score report is saved as an agent baseline. Actual run reports will be created when the official practice runner is launched.
