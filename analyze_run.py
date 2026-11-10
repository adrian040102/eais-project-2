import json
import pathlib
from collections import Counter

def load_run_analysis(run_path):
    """
    Loads data from a run directory for pandas analysis.
    Returns:
        summary: The raw summary.json as a dict.
        rows: A list of dictionaries (one per request) ready for pd.DataFrame.
        grades: A mapping of request_id -> grade object from log.jsonl.
    """
    run_path = pathlib.Path(run_path).resolve()
    
    if not (run_path / "summary.json").is_file():
        raise FileNotFoundError(f"No summary.json found in {run_path}")
        
    summary = json.loads((run_path / "summary.json").read_text(encoding="utf-8-sig"))
    
    # Extract detailed grades from the log.jsonl
    grades = {}
    log_path = run_path / "log.jsonl"
    if log_path.is_file():
        with log_path.open(encoding="utf-8-sig") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    ev = json.loads(line)
                    if ev.get("kind") == "grade":
                        req = ev.get("req")
                        if req and req != "setup":
                            # Store the last grade event for the request
                            grades[req] = ev.get("grade")
                except json.JSONDecodeError:
                    continue
    
    # Flatten rows for Pandas
    rows = []
    for row in summary.get("rows", []):
        rid = row.get("id")
        grade = grades.get(rid, {}) if rid else {}
        
        # Enhance row with data from the grade object
        enhanced_row = row.copy()
        if isinstance(grade, dict):
            enhanced_row["grade_correct"] = grade.get("correct", 0)
            enhanced_row["grade_max"] = grade.get("max", 1)
            # Collect all specific check failures (A, T, P)
            check_failures = []
            for check in grade.get("checks", []):
                if isinstance(check, dict) and not check.get("ok", True):
                    # Find what failed (amount, train, provenance)
                    for key in ("amount", "train", "provenance"):
                        if check.get(key) is False:
                            check_failures.append(key)
            enhanced_row["detailed_failures"] = check_failures
            
        rows.append(enhanced_row)
        
    return summary, rows, grades
