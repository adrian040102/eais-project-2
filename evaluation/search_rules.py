"""Inspect the working agent's rule search locally without any model calls."""

import argparse
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "team"), str(PROJECT / "eais-p2-kit")]
import system
from p2kit.rules import RuleLibrary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--company", help="Optional exact company name, e.g. DB, SBB, EU")
    args = parser.parse_args()
    library = RuleLibrary([PROJECT / "eais-p2-kit" / "rules"])
    with tempfile.TemporaryDirectory(prefix="p2-rule-demo-") as cache:
        tools = SimpleNamespace(cache_dir=cache, list_rule_documents=library.list_rule_documents,
                                read_rule_document=library.read_rule_document)
        summary = system.build_rule_index(tools)
        print(f"Indexed {summary['documents']} documents as {summary['passages']} passages.\n")
        index = system.load_rule_index(tools)
        results = system.search_rules(index, args.query, args.company)["passages"]
        for number, result in enumerate(results, 1):
            print(f"{number}. {result['doc_id']} | {result['company']} | score {result['score']}")
            print(result["section"])
            print(result["text"] + "\n")
        if not results:
            print("No matches. Try keywords in the document's language, or omit --company.")


if __name__ == "__main__":
    main()
