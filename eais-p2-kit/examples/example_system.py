"""A small example system. It calls each tool except file_claim and no_claim once and prints what each tool returns, so
that you can see how to call the tools and what you get back. It makes no model call and files no claim, and it is not
a solution.

We call setup(tools) once per run, before the first request, in a process of its own. Then we call
solve(request_text, tools) once per request, each time in a new process. So a module variable that setup() fills is
gone in solve(). What setup() writes into the folder tools.cache_dir is there, read-only, in every request.

Run it on the practice requests, from the kit folder:
    python3 -I run_practice.py --system examples/example_system.py

We write what it prints to the run log (runs/<time>-<process id>-example_system/log.jsonl), as a "stdout" line per request.
"""
import json
import os


def print_result(label, result):
    print(label + ":", json.dumps(result, ensure_ascii=False)[:300])


def setup(tools):
    # Rule documents. Reading them is free: it doesn't count against your 20 tool calls. In setup(), you can use only
    # the rule documents and budget(). Here we save the list of documents in tools.cache_dir.
    documents = tools.list_rule_documents()["documents"]
    with open(os.path.join(tools.cache_dir, "documents.json"), "w") as f:
        json.dump(documents, f)


def solve(request_text, tools):
    # 1. Rule documents: the list that setup() saved in the cache, and the full text of one document.
    with open(os.path.join(tools.cache_dir, "documents.json")) as f:
        documents = json.load(f)
    print(len(documents), "rule documents; the first is", documents[0]["doc_id"], "of", documents[0]["company"])
    doc = tools.read_rule_document(documents[0]["doc_id"])
    print(doc["title"], "has", len(doc["text"]), "characters")

    # 2. The traveler, asked about one topic from topics.json.
    print("traveler:", tools.ask_traveler("travel_date"))

    # 3. Train data. These are fixed examples on the practice days, not values from the request.
    print_result("departures", tools.departures("Basel SBB", "2026-10-20", "21:00", "21:30"))
    print_result("direct trains", tools.find_trains("Basel SBB", "Zürich HB", "2026-10-19", "21:00", "23:00"))
    print_result("stops", tools.train_stops("ICE 541", "2026-10-20"))

    # 4. Claim forms: the list, and the fields of the first company's form.
    forms = tools.list_forms()["forms"]
    print_result("forms", forms)
    print_result("form", tools.get_form(forms[0]["company"]))

    print("calls left:", tools.budget())
    return "example done"
