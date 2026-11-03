"""Check rule evidence and cache behavior without practice answer labels."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


PROJECT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT / "team" / "system.py"
spec = importlib.util.spec_from_file_location("rule_search_system", SOURCE)
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)


def document_tools(documents, cache):
    return SimpleNamespace(
        cache_dir=str(cache),
        list_rule_documents=lambda: {"documents": [{"doc_id": d["doc_id"]} for d in documents]},
        read_rule_document=lambda doc_id: next(d for d in documents if d["doc_id"] == doc_id),
    )


class RuleSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="retrieval-test-", dir=Path(__file__).parent)
        cls.addClassCleanup(cls.temp.cleanup)
        rules = PROJECT / "eais-p2-kit" / "rules"
        documents = json.loads((rules / "index.json").read_text(encoding="utf-8"))
        for document in documents:
            document["text"] = (rules / (document["doc_id"] + ".md")).read_text(encoding="utf-8")
        cls.tools = document_tools(documents, cls.temp.name)
        cls.summary = system.build_rule_index(cls.tools)
        cls.index = system.load_rule_index(cls.tools)

    def test_real_searches_return_body_evidence(self):
        fixtures = [
            ("Entschädigungsbeträge unter 4 ausgezahlt", "DB",
             [("de-db-bb-de", "unter 4 € werden nicht ausgezahlt"),
              ("de-db-rechtliche-regelungen-de", "Entschädigungsbeträge unter 4 Euro werden nicht ausgezahlt")]),
            ("OUIGO retard arrivée 2h 50 montant billet", "OUIGO",
             [("fr-ouigo-faq-retard-fr", "À partir de 2h")]),
            ("single tickets compensation under CHF 5", "SBB",
             [("ch-sbb-compensation-en", "Compensation amounts of under CHF 5")]),
            ("through tickets single commercial transaction missed connections Article 12", None,
             [("eu-reg-2021-782-en", "purchased in a single commercial transaction from a railway undertaking")]),
            ("AJC separate tickets hotels taxis compensation restrictions", None,
             [("xb-cer-ajc-en", "does not reimburse any costs for hotels, taxis")]),
        ]
        self.assertEqual(self.summary["documents"], 45)
        for query, company, alternatives in fixtures:
            with self.subTest(query=query):
                results = system.search_rules(self.index, query, company)["passages"]
                self.assertTrue(any(p["doc_id"] == doc_id and evidence in p["text"]
                                    for p in results for doc_id, evidence in alternatives),
                                [(p["doc_id"], p["section"]) for p in results])

    def test_unknown_company_is_discovered_and_accents_are_normalized(self):
        documents = [{"doc_id": "new-rail-policy", "company": "Nouvelle Rail", "language": "fr",
                      "title": "Indemnités", "text": "# Indemnités\n\nIndemnités après 37 minutes."}]
        with tempfile.TemporaryDirectory(prefix="new-company-test-", dir=Path(__file__).parent) as cache:
            tools = document_tools(documents, cache)
            system.build_rule_index(tools)
            # A fresh request needs only its cache path, not the original documents.
            index = system.load_rule_index(SimpleNamespace(cache_dir=cache))
            results = system.search_rules(index, "INDEMNITES 37", company="NOUVELLE RAIL")["passages"]
            self.assertEqual(results[0]["doc_id"], "new-rail-policy")
            self.assertIn("après 37 minutes", results[0]["text"])

    def test_overlap_preserves_a_rule_crossing_a_passage_boundary(self):
        text = "# Passenger rights\n\n## Compensation\n\n" + "background " * 298
        text += "minimum four euros are not paid " + "background " * 100
        passages = list(system.split_passages(text))
        self.assertTrue(any("minimum four euros are not paid" in p["text"] for p in passages))
        self.assertTrue(all(p["section"] == "Passenger rights > Compensation" for p in passages))
        self.assertTrue(all(len(p["text"].split()) <= 300 for p in passages))

    def test_empty_and_unmatched_queries_do_not_return_arbitrary_rules(self):
        for query in ("", "?!", "zzznomatchingrailword"):
            with self.subTest(query=query):
                self.assertEqual(system.search_rules(self.index, query)["passages"], [])

    def test_company_filter_is_optional_and_search_does_not_change_cache(self):
        path = Path(self.tools.cache_dir) / system.INDEX_FILE
        before = path.read_bytes()
        results = system.search_rules(self.index, "compensation delay", company="sbb")["passages"]
        self.assertTrue(results)
        self.assertTrue(all(p["company"] == "SBB" for p in results))
        self.assertEqual(path.read_bytes(), before)

    def test_read_failure_does_not_replace_a_working_cache(self):
        path = Path(self.tools.cache_dir) / system.INDEX_FILE
        before = path.read_bytes()
        broken = SimpleNamespace(cache_dir=self.tools.cache_dir,
                                 list_rule_documents=lambda: {"documents": [{"doc_id": "unavailable"}]},
                                 read_rule_document=lambda doc_id: {"error": "document unavailable"})
        with self.assertRaisesRegex(ValueError, "document unavailable"):
            system.build_rule_index(broken)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
