"""Scoring: the filed claims of one request against the answer key, and the score lines that both runners print.

The practice runner (kit/run_practice.py) and the staff grader (grader/run.py) use this same code.

A claim scores one point if the amount, the train and the provenance are right:

- amount: within one cent of the key, and in the key's currency when the form asks for one;
- train: the train run that the form stored for the train field (its train_id, p2kit/forms.py) is one of the key's runs.
  The form takes only a run that a train tool returned in the same request, so the run comes from the request's own
  train data;
- provenance: the ticket price on the form, if the form asks for one, is the key's price, and before the filing the
  system asked at least one topic of each group that the claim depends on.

When nothing is owed, the request is worth one point if the system declared it with no_claim for the key's company,
filed no claim, looked up the train before the declaration (train_stops, or a find_trains search with at most five
trains, returned one of the key's runs) and asked the topics the decision depends on before the declaration. We grade
the last declaration for the key's company; declarations for other companies cost 0.5 points from the set total.

Each extra or duplicate filing costs 0.5 points, and when a claim is owed, so does each no_claim declaration. Each
question after the limit costs 0.25 points. Wrong no-claim calls and claims where nothing is owed have a separate
penalty column. The set total is the sum of request points minus those penalties, floored at zero.
"""
from decimal import Decimal

from .forms import to_decimal

DATA_TOOLS = {"departures", "find_trains", "train_stops"}
NO_ANSWER = ("I don't know.", "The traveler does not answer any more questions.")
PENALTY_EXTRA, PENALTY_OVER = 0.5, 0.25
NO_RUNS = "the answer key has no train runs (an answer key from before train_id?): "


def train_ids(rows):
    return {r["train_id"] for r in rows if isinstance(r, dict) and r.get("train_id")}


def no_claim_train_evidence(name, result):
    """The runs that a train's stops or a small, complete direct-train result identifies; departures do not."""
    if not isinstance(result, dict) or result.get("error"):
        return set()
    rows = result.get("trains")
    if name == "train_stops" and isinstance(rows, list):
        return train_ids(rows)
    if name == "find_trains" and isinstance(rows, list) and 1 <= len(rows) <= 5 and not result.get("truncated"):
        return train_ids(rows)
    return set()


def carried_penalty(row):
    return row.get("penalty", row.get("set_penalty", row.get("no_claim_penalty", 0)))


def set_row_points(rows):
    """Net contributions before the set floor. Penalties stay on the request that caused them."""
    return [row["points"] - carried_penalty(row) for row in rows]


def key_problems(key):
    """What an answer key entry lacks for grading: the runs of a claim, or the required_runs of a no-claim answer. An
    answer key from before train runs has neither."""
    probs = [f"claim {i} ({c.get('company')}) has no runs" for i, c in enumerate(key.get("claims", []), 1) if not c.get("runs")]
    nc = key.get("no_claim")
    if nc is not None and not nc.get("required_runs"):
        probs.append(f"the no-claim answer ({nc.get('company')}) has no required_runs")
    probs += [f"the ticket with nothing owed ({t.get('company')}) has no runs" for t in key.get("not_owed", []) if not t.get("runs")]
    return probs


def check_keys(key, ids):
    """Raises ValueError, with every problem, if the answer key entry of a request in ids lacks its runs (key_problems)."""
    probs = [f"{rid}: {p}" for rid in ids for p in key_problems(key[rid])]
    if probs:
        raise ValueError(NO_RUNS + "; ".join(probs))


def ticket_filings(expected, filings, not_owed=()):
    """({index of an expected claim: [(event index, claim)]}, extra filings). A company with one expected claim and no
    other ticket gets every filing to it, and we grade the last. A company with several tickets (ruling R11), each with
    an expected claim or, if nothing is owed for it, an entry in the key's not_owed, gets each filing for the ticket whose
    runs hold its train. A filing for a ticket with nothing owed counts as extra, and never replaces the claim for the
    owed ticket. A filing with another train goes to a claim of that company without a filing, in the key's order, or
    else counts as extra. Each filing after the first of a claim is a duplicate."""
    by_company = {}
    for i, comp, claim in filings:
        by_company.setdefault(comp, []).append((i, claim))
    by_claim, extra = {}, 0
    for comp, lst in by_company.items():
        idx = [k for k, c in enumerate(expected) if c["company"] == comp]
        unowed = {r for t in not_owed if t["company"] == comp for r in t["runs"]}
        if len(idx) == 1 and not unowed:
            by_claim[idx[0]] = lst
        elif idx:
            rest = []
            for f in lst:
                k = next((k for k in idx if f[1].get("train_id") in expected[k]["runs"]), None)
                if k is not None:
                    by_claim.setdefault(k, []).append(f)
                elif f[1].get("train_id") not in unowed:
                    rest.append(f)
            for k in idx:
                if k not in by_claim and rest:
                    by_claim[k] = [rest.pop(0)]
        extra += len(lst) - sum(k in by_claim for k in idx)
    return by_claim, extra


def grade_request(req_text, key, events, forms, rules):
    """A dict with the points, the maximum, the checks for each expected claim and the extra filings."""
    if key_problems(key):
        raise ValueError(NO_RUNS + "; ".join(key_problems(key)))
    filings = []          # (event index, company, accepted claim)
    declared = []         # no_claim: (event index, company)
    answered = set()
    data_trains = []
    seen = []             # per event: (answered topics, train data results) before that event
    for i, ev in enumerate(events):
        seen.append((set(answered), len(data_trains)))
        if ev.get("kind") != "tool":
            continue
        name, res = ev["name"], ev.get("result")
        if name == "ask_traveler" and isinstance(res, str):
            t = str(ev["args"].get("topic", "")).strip().lower().replace(" ", "_").replace("-", "_")
            if res not in NO_ANSWER:
                answered.add(t)
        elif name in DATA_TOOLS:
            data_trains.append(no_claim_train_evidence(name, res))
        if name == "file_claim" and ev.get("accepted"):
            filings.append((i, ev["accepted"]["company"], ev["accepted"]))
        if name == "no_claim" and ev.get("declared"):
            declared.append((i, ev["declared"]["company"]))
    expected = key.get("claims", [])
    checks, correct = [], 0
    by_claim, extra = ticket_filings(expected, filings, key.get("not_owed", []))
    if expected:
        extra += len(declared)  # a no-claim answer where a claim is owed counts like an extra filing
    for k, c in enumerate(expected):
        lst = by_claim.get(k)
        chk = {"company": c["company"], "filed": bool(lst)}
        if not lst:
            chk.update(amount=False, train=False, provenance=False, ok=False)
            checks.append(chk)
            continue
        i, claim = lst[-1]
        fields = claim["fields"]
        snap_topics, _ = seen[i]
        slot = lambda s: forms.slot(c["company"], fields, s)
        try:
            amt_ok = abs(to_decimal(slot("amount")) - Decimal(c["amount"])) <= Decimal("0.01")
        except Exception:
            amt_ok = False
        cur = slot("currency")
        if cur is not None and str(cur).strip().upper() != c.get("currency", "EUR"):
            amt_ok = False
        train_ok = claim.get("train_id") in c["runs"]
        prov = []
        for group in c.get("required_topics", []):
            if not set(group) & snap_topics:
                prov.append("missing topic " + "|".join(group))
        price = slot("price")
        if price not in (None, ""):
            try:
                if to_decimal(price) != Decimal(c["price"]):
                    prov.append("price is not the ticket price")
            except Exception:
                prov.append("price unreadable")
        pay = slot("payment")
        chk.update(amount=amt_ok, train=train_ok, provenance=not prov, provenance_issues=prov,
                   payment_ok=(None if not c.get("payment") else str(pay or "").strip() == c["payment"]))
        chk["ok"] = amt_ok and train_ok and not prov
        correct += chk["ok"]
        checks.append(chk)
    nc = key.get("no_claim") or {}
    # On separate tickets, a no-claim answer for the company of either ticket is right (the key's "companies").
    accepted = nc.get("companies") or [nc.get("company")]
    if not expected:
        mine = [x for x in declared if x[1] in accepted]
        if mine:
            i, _ = mine[-1]
            topics, n_data = seen[i]
            provenance_ok = bool(set(nc["required_runs"]) & set().union(*data_trains[:n_data]))
        else:
            topics, provenance_ok = answered, False
        topics_ok = all(set(g) & topics for g in key.get("required_topics", []))
        ok = bool(mine) and provenance_ok and not filings and topics_ok
        checks.append({"company": nc.get("company"), "no_claim": True, "ok": ok, "declared": bool(mine),
                       "filed_anything": bool(filings), "topics_ok": topics_ok,
                       **({"provenance": provenance_ok} if mine else {})})
        correct += ok
        max_pts = 1
    else:
        max_pts = len(expected)
    wrong = len(declared) if expected else sum(comp not in accepted for _, comp in declared)
    return {**({"wrong_no_claims": wrong} if wrong else {}),
            **({"wrong_filings": len(filings)} if filings and not expected else {}),
            "correct": correct, "max": max_pts, "extra_filings": extra if expected else 0, "checks": checks,
            "filings": len(filings), "no_claims": len(declared)}


def issue_codes(g):
    """The issue codes of a graded request: A, T, P, missing-claim, no-claim-when-owed (for a claim owed), and
    filed-when-nothing-owed, missing-no-claim, no-claim-P, topics (for nothing owed)."""
    issues = []
    for c in g["checks"]:
        if c.get("no_claim"):
            if c["filed_anything"]:
                issues.append("filed-when-nothing-owed")
            if not c["declared"]:
                issues.append("missing-no-claim")
            if c.get("declared") and not c.get("provenance", True):
                issues.append("no-claim-P")
            if not c["topics_ok"]:
                issues.append("topics")
        elif not c["ok"]:
            if not c["filed"]:
                issues.append("missing-claim")
            else:
                issues.append("".join(x for x, k in (("A", "amount"), ("T", "train"), ("P", "provenance"))
                                      if not c[k]))
    if g.get("max") and not any(c.get("no_claim") for c in g["checks"]) and g.get("no_claims"):
        issues.append("no-claim-when-owed")
    if any(c.get("no_claim") for c in g["checks"]) and g.get("wrong_no_claims"):
        issues.append("no-claim-wrong-company")
    return issues


def points(g, over_limit):
    # Wrong declarations are displayed separately, and charged at the set level.
    wrong_local = g.get("no_claims", 0) if not any(c.get("no_claim") for c in g["checks"]) else 0
    return max(0.0, g["correct"] - PENALTY_EXTRA * (g["extra_filings"] - wrong_local) - PENALTY_OVER * over_limit)


def no_claim_carry(g, over_limit):
    """The separate set penalty, attributed to the request that caused it."""
    return PENALTY_EXTRA * (g.get("wrong_no_claims", 0) + g.get("wrong_filings", 0))


HEADER = (f"{'id':5} {'points':>7} {'penalty':>7} {'claims':>7} {'extra':>5} {'quest':>6} {'over':>4} {'tools':>5} {'model':>5} "
          f"{'usd':>8}  status  issues")


def row_line(row):
    return (f"{row['id']:5} {row['points']:>4.2f}/{row['max']:<2} {carried_penalty(row):>7.2f} {row['correct']:>3}/{row['max']:<3} {row['extra']:>5} "
            f"{row['questions']:>3}/{row['limit']:<2} {row['over']:>4} {row['tools']:>5} {row['model']:>5} "
            f"{row['usd']:>8.4f}  {row['status']:7} {' '.join(row['issues'])}")


def failed_calls(model_calls):
    """The model calls that returned an error instead of an answer, e.g. a refused call or an HTTP error."""
    return [c for c in model_calls if c.get("error")]


def totals(rows, model_calls, spent):
    """(totals, fully right, input tokens, output tokens, the two TOTAL lines). When model calls failed, the second
    line has their number, e.g. "model 46 (0 input and 0 output tokens), 46 of them failed", so a run whose model
    calls all failed doesn't look like a normal run."""
    tot = {k: sum(x[k] for x in rows) for k in ("points", "max", "correct", "extra", "questions", "over", "tools",
                                                 "model", "usd")}
    tot["penalty"] = sum(carried_penalty(x) for x in rows)
    tot["points"] = max(0.0, tot["points"] - tot["penalty"])
    full = sum(1 for x in rows if x["correct"] == x["max"] and x["extra"] == 0 and x["over"] == 0
               and not carried_penalty(x))
    tin = sum(c.get("input_tokens", 0) for c in model_calls)
    tout = sum(c.get("output_tokens", 0) for c in model_calls)
    failed = len(failed_calls(model_calls))
    lines = [f"TOTAL points {tot['points']:.2f} of {tot['max']}; fully right {full} of {len(rows)} requests; "
             f"claims right {tot['correct']} of {tot['max']}; extra filings {tot['extra']}; questions over limit {tot['over']}",
             f"TOTAL calls: tools {tot['tools']}, model {tot['model']} ({tin} input and {tout} output tokens)"
             f"{f', {failed} of them failed' if failed else ''}; cost ${spent:.4f}"]
    return tot, full, tin, tout, lines
