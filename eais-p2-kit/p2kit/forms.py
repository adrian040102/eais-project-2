"""Claim forms: list_forms, get_form and file_claim, with plain-text errors in the form's language, and no_claim, the
answer that nothing is owed to a company.

Every train field of a form names one train run that the train tools returned in the same request (train_names()):
its name as the tools write it, its number, its line with its name or number, or its train_id. That holds for the
claim's train (slot "train") and for any other train, e.g. a missed connecting train. The form stores the train_id of
the claim's train, and file_claim's answer gives that run back. A value that fits no run, or several, gets an error
with the runs.

The code knows nothing about any one company. Each form is a JSON file with the company, its language, its labels,
help texts and error messages, and its fields with their types. In grading, the runner adds the test-day companies'
folder of forms, and the same code serves forms that the team's system has never seen.
"""
import json
import re
from datetime import date as Date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .rules import fold


def date_ok(value, fmt):
    """True if value is a real date written in the form's format, e.g. dd.mm.yyyy, dd/mm/yyyy or yyyy-mm-dd."""
    parts = re.split(r"(dd|mm|yyyy)", fmt)
    if sorted(p for p in parts if p in ("dd", "mm", "yyyy")) != ["dd", "mm", "yyyy"]:
        raise KeyError(fmt)  # a form with a date format we can't read
    pattern = "".join({"dd": r"(?P<d>\d{2})", "mm": r"(?P<m>\d{2})", "yyyy": r"(?P<y>\d{4})"}.get(p, re.escape(p))
                      for p in parts)
    m = re.fullmatch(pattern, str(value).strip())
    if not m:
        return False
    try:
        Date(int(m["y"]), int(m["m"]), int(m["d"]))
    except ValueError:
        return False
    return True


def to_decimal(v):
    if isinstance(v, bool):
        raise InvalidOperation
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip().replace("€", "").replace("EUR", "").replace("CHF", "").strip()
    if re.fullmatch(r"-?\d+,\d{1,2}", s):
        s = s.replace(",", ".")
    return Decimal(s)


def train_names(run):
    """What the train field may hold for a run (TrainData.run), without case and with one space between words: its name as
    the tools write it ("ICE 1101"), its number ("1101"), its line with its name or number ("RB54 12469") and its train_id."""
    names = {run["train"], run["number"], run["train_id"]}
    for line in run.get("lines") or []:
        names |= {f"{line} {run['train']}", f"{line} {run['number']}"}
    return {" ".join(str(x).split()).lower() for x in names}


def run_list(runs, m):
    """Every run, for a train field error."""
    return "; ".join(m["train_item"].format(train=r["train"], day=r["service_day"], first=r["from"], last=r["to"], id=r["train_id"])
                     for r in runs)


class FormDesk:
    def __init__(self, folders):
        self.forms = {}
        for folder in folders:
            for p in sorted(Path(folder).glob("*.json")):
                spec = json.loads(p.read_text())
                self.forms[spec["company"]] = spec
        self.claims = []
        self.no_claims = []

    def _find(self, company):
        return self.forms.get(self.names().get(fold(company)))

    def names(self):
        """Every name a form accepts, folded with fold(), mapped to the form's company: the company name, the company's own
        names in other languages (company_names, e.g. CFF and FFS for SBB) and its operators."""
        return {fold(n): s["company"] for s in self.forms.values()
                for n in [s["company"], *s.get("company_names", []), *s.get("operators", [])]}

    def known(self):
        return ", ".join(sorted(self.forms))

    def list_forms(self):
        """The companies that have a claim form, with the form's title and language."""
        return {"forms": [{"company": s["company"], "title": s["title"], "language": s["language"]}
                          for s in sorted(self.forms.values(), key=lambda s: s["company"])]}

    def get_form(self, company):
        """The form's fields with their labels, help texts, formats and allowed values, in the form's language."""
        spec = self._find(company)
        if not spec:
            return {"error": f"no claim form for '{company}'; forms exist for: {self.known()}"}
        fields = []
        for f in spec["fields"]:
            g = {k: f[k] for k in ("name", "label", "type", "required") if k in f}
            if f.get("description"):
                g["help"] = f["description"]
            for k in ("format", "values", "required_if", "min_length"):
                if k in f:
                    g[k] = f[k]
            fields.append(g)
        return {"company": spec["company"], "title": spec["title"], "language": spec["language"],
                "instructions": spec.get("instructions", ""), "fields": fields}

    def _check(self, spec, fields, runs=()):
        """(errors, the run of the claim's train field or None). runs: the runs that the train tools returned in this request."""
        m = spec["messages"]
        errs, train = [], None
        if not isinstance(fields, dict):
            return [m["not_object"]], None
        names = {f["name"] for f in spec["fields"]}
        for k in fields:
            if k not in names:
                errs.append(m["unknown"].format(name=k))
        vals = {}
        for f in spec["fields"]:
            v = fields.get(f["name"])
            empty = v is None or (isinstance(v, str) and not v.strip())
            req = f.get("required", False)
            ri = f.get("required_if")
            if ri and str(fields.get(ri["field"], "")).strip().lower() == str(ri["value"]).lower():
                req = True
            if empty:
                if req:
                    errs.append(f.get("missing_message") or m["missing"].format(label=f["label"]))
                continue
            t = f["type"]
            try:
                if t == "date":
                    if not date_ok(v, f["format"]):
                        raise ValueError
                elif t == "time":
                    mm = re.fullmatch(r"(\d{2}):(\d{2})", str(v).strip())
                    if not mm or int(mm.group(1)) > 23 or int(mm.group(2)) > 59:
                        raise ValueError
                elif t == "int":
                    if isinstance(v, bool) or not re.fullmatch(r"-?\d+", str(v).strip()):
                        raise ValueError
                elif t == "amount":
                    d = to_decimal(v)
                    if d < 0 or d.as_tuple().exponent < -2:
                        raise ValueError
                    vals[f["name"]] = d
                elif t == "train":
                    key = " ".join(str(v).split()).lower()
                    hits = [r for r in runs if key in train_names(r)]
                    if len(hits) != 1:
                        why = "train_several" if hits else "train_unknown" if runs else "train_none"
                        errs.append(m[why].format(label=f["label"], value=v, n=len(hits), trains=run_list(hits or list(runs), m)))
                        continue
                    if f.get("slot") == "train":
                        train = hits[0]
                elif t == "enum":
                    if str(v).strip() not in f["values"]:
                        errs.append(m["enum"].format(label=f["label"], values=", ".join(f["values"])))
                        continue
                elif t == "text":
                    if len(str(v).strip()) < f.get("min_length", 1):
                        raise ValueError
            except (ValueError, InvalidOperation, KeyError, TypeError):  # TypeError: e.g. "Infinity" as an amount
                errs.append(f.get("format_message") or m["format"].format(label=f["label"], hint=f.get("format", f.get("hint", ""))))
        if not errs:
            for c in spec.get("checks", []):
                d = vals.get(c["field"])
                if d is None:
                    continue
                if c["type"] == "multiple_of" and (d / Decimal(str(c["step"]))) % 1 != 0:
                    errs.append(c["message"])
                if c["type"] == "min" and d < Decimal(str(c["value"])):
                    errs.append(c["message"])
        return errs, train

    def file_claim(self, company, fields, runs=()):
        """runs: the runs (TrainData.run) that the train tools returned in this request, which the train field must name."""
        spec = self._find(company)
        if not spec:
            return {"status": "rejected", "errors": [f"no claim form for '{company}'; forms exist for: {self.known()}"]}
        errs, train = self._check(spec, fields, list(runs))
        if errs:
            return {"status": "rejected", "errors": errs}
        cid = f"{spec['company'][:3].upper().replace(' ', '')}-{len(self.claims) + 1:04d}"
        self.claims.append({"claim_id": cid, "company": spec["company"], "fields": dict(fields),
                            **({"train_id": train["train_id"]} if train else {})})
        out = {"status": "accepted", "claim_id": cid, "message": spec["messages"]["accepted"]}
        if train:
            out["train"] = {k: train[k] for k in ("train", "train_id", "service_day", "from", "to")}
        return out

    def no_claim(self, company):
        """Record that nothing is owed to a company for this request."""
        spec = self._find(company)
        if not spec:
            return {"status": "rejected", "errors": [f"no claim form for '{company}'; forms exist for: {self.known()}"]}
        self.no_claims.append({"company": spec["company"]})
        return {"status": "recorded", "company": spec["company"],
                "message": f"recorded: no claim to {spec['company']} for this request"}

    def slot(self, company, fields, slot):
        spec = self.forms[company]
        for f in spec["fields"]:
            if f.get("slot") == slot:
                return fields.get(f["name"])
        return None
