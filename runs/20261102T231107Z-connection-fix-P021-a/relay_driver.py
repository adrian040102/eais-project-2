"""Supervised Codex relay smoke test; separate from course/API evaluation."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
KIT = PROJECT / "eais-p2-kit"
sys.path.insert(0, str(KIT))

from p2kit.data import TrainData
from p2kit.llm import ModelGateway, check_request
from p2kit.runner import load_practice, pick_requests, run_set


def save_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class RelayGateway(ModelGateway):
    """Exchange the unchanged agent's model messages through a supervised mailbox."""

    def __init__(self, mailbox):
        super().__init__(0, "off")
        self.mailbox = mailbox

    def describe(self):
        return "supervised Codex relay: gpt-6-luna, low reasoning; API cost only is $0"

    def call(self, messages, model="gpt-6-luna", **options):
        started = time.monotonic()
        number = len(self.calls) + 1
        record = {"model": model, "backend": "codex-subagent-relay", "reasoning_effort": "low",
                  "usd": 0, "tokens_measured": False, "subscription_usage_measured": False}
        try:
            _, problem = check_request(model, options)
            if problem or model != "gpt-6-luna":
                raise ValueError(problem or "This relay is configured only for gpt-6-luna.")
            save_json(self.mailbox / f"request-{number:03}.json",
                      {"model": model, "reasoning_effort": "low", "messages": messages, "options": options})
            print(f"Relay request {number:03} ready", flush=True)
            response_path = self.mailbox / f"response-{number:03}.json"
            deadline = started + 300
            while not response_path.is_file():
                if time.monotonic() >= deadline:
                    raise TimeoutError("No supervised model response within 300 seconds.")
                time.sleep(0.2)
            message = json.loads(response_path.read_text(encoding="utf-8-sig"))
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Relay response must be an assistant message object.")
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list) or (options.get("parallel_tool_calls") is False and len(calls) > 1):
                raise ValueError("Invalid tool-call list or parallel calls disabled.")
            # The assistant validates and dispatches calls itself, including bad JSON
            # arguments. Passing those through preserves its normal correction loop.
            result = {"choices": [{"message": message}]}
        except (KeyError, TypeError, ValueError, TimeoutError) as error:
            record["error"] = str(error)
            result = {"error": str(error)}
        record["seconds"] = round(time.monotonic() - started, 2)
        self.calls.append(record)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="One development case ID")
    parser.add_argument("--out", required=True, type=Path, help="Fresh run directory")
    args = parser.parse_args()
    splits = json.loads((HERE / "splits.json").read_text(encoding="utf-8"))
    if args.case not in splits["dev"]:
        parser.error("Choose one development case; holdout cases are excluded.")
    out = args.out.resolve()
    if not out.is_relative_to((PROJECT / "runs").resolve()):
        parser.error("The output must be under this project's runs directory.")
    out.mkdir(parents=True, exist_ok=False)
    mailbox = out / "mailbox"
    mailbox.mkdir()
    source = out / "source"
    shutil.copytree(PROJECT / "team", source, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = {
        "kind": "supervised-codex-relay-smoke", "created_utc": datetime.now(timezone.utc).isoformat(),
        "ids": [args.case], "model": "gpt-6-luna", "reasoning_effort": "low",
        "source_sha256": hashlib.sha256((source / "system.py").read_bytes()).hexdigest(),
        "relay_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "api_cost_usd": 0, "subscription_usage": "not measured; this test consumes Codex usage",
        "token_counts": "not measured", "course_api_tested": False,
        "limitations": "A Codex agent answers exported messages as JSON. Its surrounding instructions, "
                       "tool-call serialization, latency, and token limits differ from raw course API calls. "
                       "This is a supervised diagnostic, not an official model-performance measurement."
    }
    save_json(out / "relay_manifest.json", manifest)
    requests, facts, key = load_practice(KIT / "practice/practice_requests.json")
    requests = pick_requests(requests, args.case)
    try:
        summary = run_set(source / "system.py", set_name="supervised-relay-smoke", requests=requests,
                          facts=facts, key=key, data=TrainData(KIT / "data/trains.sqlite"),
                          rule_dirs=[KIT / "rules"], form_dirs=[KIT / "forms"],
                          gateway=RelayGateway(mailbox), out=out, timeout_s=1800, show_checks=True)
        save_json(mailbox / "finished.json", {"status": "finished"})
    except BaseException:
        save_json(mailbox / "finished.json", {"status": "runner stopped"})
        raise
    return 2 if any(call.get("error") for call in summary["model_calls"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
