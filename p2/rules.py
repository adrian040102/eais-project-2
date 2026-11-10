"""Rule documents: the list of documents and the full text of each one. There is no rule search: building
retrieval over the documents is the team's job.

The library loads one or more folders, each with an index.json and one Markdown file per document. In grading, the
runner adds the test-day companies' folder to the published one, so the list holds documents that the team's
system has never seen.
"""
import json
import unicodedata
from pathlib import Path


def fold(name):
    """A company name compared without case, accents and extra spaces, e.g., "Genève" and "geneve"."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    return " ".join("".join(ch for ch in s if not unicodedata.combining(ch)).lower().split())


class RuleLibrary:
    def __init__(self, folders):
        self.docs = {}
        for folder in folders:
            folder = Path(folder)
            for meta in json.loads((folder / "index.json").read_text()):
                text = (folder / f"{meta['doc_id']}.md").read_text()
                self.docs[meta["doc_id"]] = dict(meta, text=text)

    def companies(self):
        return sorted({d["company"] for d in self.docs.values()})

    def _company_ok(self, d, company, names):
        c = fold(company)
        return fold(d["company"]) in (c, fold(names.get(c)))

    def list_rule_documents(self, company=None, names=None):
        """Every document, or the documents of one company: `company` is the documents' company or a name that a claim
        form accepts, compared with fold(). `names` maps the names that the forms accept, folded, to the form's company
        (FormDesk.names): the company name and the operator names of its trains."""
        out = [{"doc_id": d["doc_id"], "company": d["company"], "title": d["title"], "language": d["language"],
                "chars": len(d["text"])}
               for d in self.docs.values() if not company or self._company_ok(d, company, names or {})]
        if not out:
            return {"error": f"no rule documents for company '{company}'; companies: {', '.join(self.companies())}"}
        return {"documents": out}

    def read_rule_document(self, doc_id):
        """The full text of one document, in Markdown."""
        d = self.docs.get(str(doc_id).strip())
        if not d:
            return {"error": f"unknown doc_id '{doc_id}'; list_rule_documents gives the ids"}
        return {"doc_id": d["doc_id"], "company": d["company"], "title": d["title"], "language": d["language"],
                "text": d["text"]}
