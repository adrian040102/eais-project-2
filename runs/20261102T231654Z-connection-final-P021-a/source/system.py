"""A train-claims assistant with rule search and an explicit model/tool loop."""

import json
import pickle
import re
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from rank_bm25 import BM25Okapi


INDEX_FILE = "rule_index.pkl"
MODEL = "gpt-6-luna"
CHANGE_TIMES = {
    "basel sbb": 7, "zurich hb": 7, "geneve": 8, "frankfurt main hbf": 8,
    "strasbourg": 8, "stuttgart hbf": 8, "milano centrale": 10, "munchen hbf": 10,
    "lyon part dieu": 10, "torino porta susa": 10, "bologna centrale": 10,
    "firenze s m n": 10, "roma termini": 15,
}

INSTRUCTIONS = """Help the traveler complete every compensation decision through the tools.
Work through missing facts, train identification, rule retrieval, then the form.
Use ask_traveler for missing facts; plain questions in your answer do not reach the traveler.
Ask only necessary topics, once each. Never invent prices, dates, sellers, or delay causes.
Do not ask for facts already stated or unambiguously derived, including explicit dates.
Resolve relative dates and weekdays from the date at the start of the request. All times are local.
Use recorded train data, not approximate delays reported by the traveler. Search each leg
of a connection separately. Use canonical station names and windows of at most four hours;
from_time and to_time must differ. Pick the intended run by date, route, and planned time.
For missed connections, establish ticket_structure and the actual onward journey through
train_after_missed_connection or arrival_time, unless already stated. For a through ticket
whose onward train is unknown, use resolve_connection with the route and both scheduled
departure times from the request. It resolves the incoming, missed and replacement trains
and final delay. Copy its exact arrival times and minutes; do not guess or round them.
For a known onward train, identify it and calculate_delay from the original planned final
arrival to that train's recorded arrival. Separate tickets are assessed separately.
Search the rules before deciding. Use keywords in the documents' language; search_rules
does not translate. Omit its company filter for EU/cross-border rules. Confirm that the
passage applies to this ticket, train category, and journey. Operator and claim recipient
may differ: use the seller, ticket structure, rules, and list_forms to choose the recipient.
Include the observed train category in rule queries. A company's regional, domestic,
and international policies can differ. Use the table column for the actual train category
and the row for the final delay, rather than the first percentage in the search results.
Read get_form before filing, including help, required_if, allowed values, and price basis.
Forms may be unfamiliar. Use their exact field names and exact observed train_id values.
Calculate compensation with calculate_compensation using the rate and minimum from the
applicable rules. Always supply minimum explicitly; use 0 only after checking that no
minimum applies. If all payouts are zero, record no_claim without a form or payment question.
Use calculate_delay for planned/actual timestamps from different trains; otherwise
use the relevant train tool's delay. Return tickets may require the affected direction's
price. Check exclusions and ask the reported delay_reason if the rule depends on it.
When a payable claim offers a payment choice, ask payment_preference unless already known.
Do not choose a voucher or transfer for the traveler. Check whether the choice changes the rate.
The form's train is the first late/cancelled train causing the disruption, including the
incoming late train for a missed connection. File one claim per company and ticket;
accepted filings are final, while rejected ones can be corrected. Still file when the
rules describe automatic payments. Deadlines, waiting periods, and proof are satisfied.
If any claim is owed, file the owed claims and leave the unowed tickets without no_claim.
Use no_claim only when nothing is owed for the entire request, for the responsible company.
Before no_claim, identify the relevant run with train_stops or complete find_trains with
at most five results. Obtain all necessary missing facts before either decision.
Reserve calls for form retrieval, filing, and possible correction. Call finish only after
all tickets are handled. A tool error is information to correct, never a no-claim decision.
"""


def tokenize(text):
    """Match words regardless of case or accents, keeping numbers as well."""
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.findall(r"[^\W_]+", normalized)


def split_passages(text, size=300, overlap=50):
    """Keep section headings and overlap passages so boundary text appears twice."""
    if not 0 <= overlap < size:
        raise ValueError("Passage overlap must be smaller than its size.")
    headings = []
    blocks = re.split(r"(?m)^(#{1,6}\s+[^\n]+)$", text)
    for block in blocks:
        heading = re.fullmatch(r"(#{1,6})\s+(.+)", block)
        if heading:
            level = len(heading[1])
            headings = headings[:level - 1]
            headings.extend([""] * (level - 1 - len(headings)))
            headings.append(heading[2].strip())
            continue
        # Keep whitespace with each word to preserve paragraphs and table rows.
        words = re.findall(r"\S+\s*", block)
        for start in range(0, len(words), size - overlap):
            yield {"section": " > ".join(filter(None, headings)),
                   "text": "".join(words[start:start + size]).strip()}
            if start + size >= len(words):
                break


def build_rule_index(tools):
    """Read every available document and save the prepared index for solve()."""
    listing = tools.list_rule_documents()
    if "error" in listing:
        raise ValueError(listing["error"])
    passages = []
    for metadata in listing["documents"]:
        document = tools.read_rule_document(metadata["doc_id"])
        if "error" in document:
            raise ValueError(document["error"])
        for passage in split_passages(document["text"]):
            passages.append({"doc_id": document["doc_id"], "company": document["company"],
                             "title": document["title"], "language": document["language"], **passage})
    if not passages:
        raise ValueError("No rule text was available to index.")
    corpus = [tokenize(" ".join((p["company"], p["title"], p["section"], p["text"]))) for p in passages]
    index = {"passages": passages, "bm25": BM25Okapi(corpus)}
    cache_file = Path(tools.cache_dir) / INDEX_FILE
    temporary = cache_file.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        pickle.dump(index, handle, protocol=pickle.HIGHEST_PROTOCOL)
    temporary.replace(cache_file)
    return {"documents": len(listing["documents"]), "passages": len(passages)}


def load_rule_index(tools):
    """Load the cache created by our own setup; requests never write to it."""
    with (Path(tools.cache_dir) / INDEX_FILE).open("rb") as handle:
        return pickle.load(handle)


def search_rules(index, query, company=None, limit=5):
    """Return matching passages; omit company to include laws and other operators."""
    terms = tokenize(query)
    if not terms:
        return {"passages": [], "note": "Enter search words, not only punctuation."}
    limit = max(1, min(int(limit), 8))
    scores = index["bm25"].get_scores(terms)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    results = []
    for number in ranked:
        passage = index["passages"][number]
        if company and tokenize(company) != tokenize(passage["company"]):
            continue
        # BM25 scores can be negative for common words in very small collections.
        if not any(term in index["bm25"].doc_freqs[number] for term in terms):
            continue
        results.append({**passage, "score": round(float(scores[number]), 3)})
        if len(results) == limit:
            break
    return {"passages": results}


def setup(tools):
    print("Rule index:", build_rule_index(tools))


def calculate_compensation(price, percentage, minimum):
    """Apply an already-established rate, rounding to cents before the minimum."""
    price, percentage, minimum = (Decimal(str(v)) for v in (price, percentage, minimum))
    if not all(v.is_finite() for v in (price, percentage, minimum)):
        raise ValueError("Amounts must be finite numbers.")
    if price < 0 or minimum < 0 or not 0 <= percentage <= 100:
        raise ValueError("Use nonnegative amounts and a percentage from 0 to 100.")
    amount = (price * percentage / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"amount": format(amount if amount >= minimum else Decimal(0), ".2f")}


def calculate_delay(planned, recorded):
    """Follow the course's whole-minute, rounded-down delay convention."""
    seconds = (datetime.fromisoformat(recorded) - datetime.fromisoformat(planned)).total_seconds()
    return {"minutes": int(seconds // 60)}


def resolve_connection(tools, origin, via, destination, date, departure, connection):
    """Resolve a through-ticket connection when the onward train is unknown (R6/R7)."""
    def query(start, minutes, first, last):
        if tools.budget()["tool_calls_left"] <= 4:
            raise ValueError("Not enough calls to complete the connection lookup and file; retain an incomplete result.")
        end = start + timedelta(minutes=minutes)
        result = tools.find_trains(first, last, start.date().isoformat(),
                                   start.strftime("%H:%M"), end.strftime("%H:%M"))
        if "error" in result or result.get("truncated"):
            raise ValueError(result.get("error") or "Connection lookup was truncated; use narrower train searches.")
        return result

    start = datetime.fromisoformat(date + " " + departure)
    found = query(start, 1, origin, via)
    incoming = [row for row in found["trains"] if datetime.fromisoformat(row["dep_planned"]) == start]
    if len(incoming) != 1:
        raise ValueError("The incoming departure is missing or ambiguous; identify it with the train tools.")
    incoming, via = incoming[0], found["destination"]
    planned_start = datetime.fromisoformat(date + " " + connection)
    if planned_start < datetime.fromisoformat(incoming["arr_planned"]):
        planned_start += timedelta(days=1)
    found = query(planned_start, 1, via, destination)
    planned = [row for row in found["trains"] if datetime.fromisoformat(row["dep_planned"]) == planned_start]
    if len(planned) != 1 or not incoming.get("arr_recorded"):
        raise ValueError("The planned connection or incoming recorded arrival is missing or ambiguous.")
    planned = planned[0]
    station = " ".join(tokenize(via))
    change = 15 if station.startswith("paris ") else CHANGE_TIMES.get(station, 5)
    earliest = datetime.fromisoformat(incoming["arr_recorded"]) + timedelta(minutes=change)
    day = datetime.combine(planned_start.date(), datetime.min.time())
    candidates = {}
    for _ in range(2):
        # Search the full day: recorded departure order may differ from planned order.
        for hour in range(0, 24, 4):
            result = query(day + timedelta(hours=hour), 240, via, destination)
            candidates.update({row["train_id"]: row for row in result["trains"]})
        eligible = [row for row in candidates.values() if not row.get("dep_cancelled")
                    and not row.get("arr_cancelled") and row.get("dep_recorded")
                    and datetime.fromisoformat(row["dep_recorded"]) >= earliest]
        if eligible and day.date() >= earliest.date():
            replacement = min(eligible, key=lambda row: datetime.fromisoformat(row["dep_recorded"]))
            if not replacement.get("arr_recorded"):
                raise ValueError("The first feasible replacement has no recorded arrival; ask about the actual journey.")
            return {"incoming": incoming, "planned_connection": planned, "replacement": replacement,
                    "planned_arrival": planned["arr_planned"], "recorded_arrival": replacement["arr_recorded"],
                    "minutes": calculate_delay(planned["arr_planned"], replacement["arr_recorded"])["minutes"],
                    "change_minutes": change}
        day += timedelta(days=1)
    raise ValueError("No feasible replacement found within two days; ask about the actual journey.")


def ask_once(tools, state, topic):
    """Reuse a recorded answer, including questions requested by local helpers."""
    if topic in state["asked"]:
        return state["asked"][topic]
    answer = tools.ask_traveler(topic)
    if isinstance(answer, str):
        state["asked"][topic] = answer
    return answer


def tool_definitions():
    """Describe the allowed functions; fields inside company forms stay dynamic."""
    def tool(name, description, properties, required=None):
        return {"type": "function", "function": {"name": name, "description": description,
                "parameters": {"type": "object", "properties": properties,
                               "required": list(properties) if required is None else required,
                               "additionalProperties": False}}}

    text = {"type": "string"}
    company = {"type": ["string", "null"]}
    window = {name: text for name in ("date", "from_time", "to_time")}
    return [
        tool("ask_traveler", "Ask one necessary topic from the supplied topic list.", {"topic": text}),
        tool("departures", "Departures at one station; maximum four-hour window, 40 rows.",
             {"station": text, **window}),
        tool("find_trains", "Direct trains only; search each connecting leg separately. Use narrow windows.",
             {"origin": text, "destination": text, **window}),
        tool("train_stops", "All stops of a train. Date is its service day; train numbers can be ambiguous.",
             {"train": text, "date": text}),
        tool("list_forms", "Discover available claim recipients, including unfamiliar companies.", {}),
        tool("get_form", "Read a company's fields, formats, instructions, and allowed values.", {"company": text}),
        tool("file_claim", "File one claim per ticket using exact form fields and observed train_id values.",
             {"company": text, "fields": {"type": "object", "additionalProperties": True}}),
        tool("no_claim", "Declare nothing owed for the entire request, after rules and relevant train evidence.",
             {"company": text}),
        tool("search_rules", "Search cached passages with keywords in the document language. Company is optional.",
             {"query": text, "company": company, "limit": {"type": "integer", "minimum": 1, "maximum": 8}}, ["query"]),
        tool("list_rule_documents", "List rule metadata; omit company to discover all operators and laws.",
             {"company": company}, []),
        tool("read_rule_document", "Read a short document by ID; use search_rules for large documents.", {"doc_id": text}),
        tool("calculate_compensation", "Apply the retrieved rate and minimum payout. Percentage: 25 means 25%, "
             "not 0.25 or '25%'. Minimum: amount in ticket currency; 0 only if no minimum applies.",
             {"price": text, "percentage": text, "minimum": text}),
        tool("calculate_delay", "Difference between full ISO planned/recorded timestamps, floored to minutes.",
             {"planned": text, "recorded": text}),
        tool("resolve_connection", "Through ticket, unknown onward train: look up incoming and missed departures, "
             "choose the first feasible noncancelled replacement and return exact final delay. "
             "Uses counted train calls. Departure and connection are scheduled HH:MM times from the request.",
             {name: text for name in ("origin", "via", "destination", "date", "departure", "connection")}),
        tool("budget", "Remaining counted tool calls and model calls.", {}),
        tool("finish", "Finish only after all required claims or the no-claim decision are recorded.", {}),
    ]


def execute_tool(call, tools, index, state):
    """Dispatch only advertised tools and enforce a few scoring-related checks."""
    try:
        name = call["function"]["name"]
        if name not in {t["function"]["name"] for t in tool_definitions()}:
            raise ValueError("Unknown tool name.")
        args = json.loads(call["function"]["arguments"])
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be a JSON object.")
        if state["finished"]:
            raise ValueError("This request has already finished.")
        if name == "finish":
            if not state["filed"] and not state["declared"]:
                raise ValueError("Record a claim or a justified no_claim decision before finishing.")
            state["finished"] = True
            return {"status": "finished"}
        if name == "search_rules":
            result = search_rules(index, **args)
            state["rules"] = state["rules"] or bool(result.get("passages"))
            return result
        if name == "calculate_compensation":
            result = calculate_compensation(**args)
            state["compensation"] = result["amount"]
            return result
        if name == "calculate_delay":
            if any(datetime.fromisoformat(args[field]) not in state["timestamps"]
                   for field in ("planned", "recorded")):
                raise ValueError("Use exact full timestamps returned by the train tools; do not round or invent times.")
            return calculate_delay(**args)
        if name == "resolve_connection":
            if not {"train_after_missed_connection", "arrival_time"}.intersection(state["asked"]):
                return {"onward_answer": ask_once(tools, state, "train_after_missed_connection"),
                        "next": "If the onward train is unknown, call resolve_connection again. "
                                "Otherwise identify the actual train with the train tools."}
            result = resolve_connection(tools, **args)
            state["connection"] = result
            for row in (result["incoming"], result["planned_connection"], result["replacement"]):
                state["trains"][row["train_id"]] = row
            state["timestamps"].update(datetime.fromisoformat(result[key])
                                       for key in ("planned_arrival", "recorded_arrival"))
            state["no_claim_evidence"] = True
            return result
        if name == "ask_traveler":
            topic = args["topic"].strip().lower().replace(" ", "_").replace("-", "_")
            return ask_once(tools, state, topic)
        if name in ("file_claim", "no_claim") and not state["rules"]:
            raise ValueError("Retrieve applicable rule passages before deciding.")
        if name == "file_claim":
            if state.get("compensation") == "0.00":
                raise ValueError("Calculated payout is zero. Use no_claim if nothing is owed; do not file a zero claim.")
            form = state["forms"].get(" ".join(tokenize(args["company"])))
            if not form:
                raise ValueError("Read get_form for this company before filing.")
            args["company"] = form["company"]
            payment_words = {"uberweisung", "gutschein", "virement", "bonus", "rimborso", "bonifico",
                             "voucher", "transfer", "cash"}
            payment_choice = any(f["type"] == "enum" and len(f.get("values", [])) > 1
                                 and payment_words.intersection(tokenize(" ".join(f["values"])))
                                 for f in form["fields"])
            if payment_choice and "payment_preference" not in state["asked"]:
                return {"payment_preference": ask_once(tools, state, "payment_preference"),
                        "next": "Map the answer to an allowed payment value, then file the claim again."}
            train_fields = [f["name"] for f in form["fields"] if f["type"] == "train"]
            for field in train_fields:
                value = args["fields"].get(field)
                if value is not None and value not in state["trains"]:
                    matches = [tid for tid, row in state["trains"].items()
                               if str(value) in (row.get("train"), str(row.get("train", "")).split(" ")[-1])]
                    if len(matches) != 1:
                        raise ValueError("Use an observed train_id or an unambiguous observed train name.")
                    args["fields"][field] = matches[0]
            trains = [args["fields"].get(field) for field in train_fields]
            journey = state.get("connection")
            if journey and journey["incoming"]["train_id"] in trains:
                times = [args["fields"].get(f["name"]) for f in form["fields"] if f["type"] == "time"]
                expected = {datetime.fromisoformat(journey[key]).strftime("%H:%M")
                            for key in ("planned_arrival", "recorded_arrival")}
                if len(times) == 2 and set(times) != expected:
                    raise ValueError("Use the exact final arrival times returned by resolve_connection.")
                integers = [f["name"] for f in form["fields"] if f["type"] == "int"]
                if len(integers) == 1 and int(args["fields"][integers[0]]) != journey["minutes"]:
                    raise ValueError("Use the final delay in minutes returned by resolve_connection.")
            ticket = trains[0] if len(trains) == 1 else json.dumps(args["fields"], sort_keys=True)
            filing_key = (form["company"], ticket)
            if filing_key in state["filed"]:
                raise ValueError("This ticket already has an accepted claim; do not file it again.")
        if name == "no_claim":
            if state["filed"]:
                raise ValueError("Claims were filed; no_claim is only for an entirely unowed request.")
            if not state["no_claim_evidence"]:
                raise ValueError("First identify the run with train_stops or complete find_trains of at most five trains.")
            if " ".join(tokenize(args["company"])) in state["declared"]:
                raise ValueError("A no_claim decision for this company was already recorded.")
        free = {"list_rule_documents", "read_rule_document", "budget"}
        if name not in free and tools.budget()["tool_calls_left"] <= 0:
            raise ValueError("No counted tool calls remain.")
        result = getattr(tools, name)(**args)
        if name == "get_form" and "fields" in result:
            state["forms"][" ".join(tokenize(args["company"]))] = result
            state["forms"][" ".join(tokenize(result["company"]))] = result
        if name in ("departures", "find_trains", "train_stops"):
            rows = result.get("departures", []) if name == "departures" else result.get("trains", [])
            state["trains"].update({row["train_id"]: row for row in rows if row.get("train_id")})
            for row in rows:
                for stop in [row, *row.get("stops", [])]:
                    for field in ("dep_planned", "dep_recorded", "arr_planned", "arr_recorded"):
                        if stop.get(field):
                            state["timestamps"].add(datetime.fromisoformat(stop[field]))
            if rows and (name == "train_stops" or (name == "find_trains" and len(rows) <= 5 and not result.get("truncated"))):
                state["no_claim_evidence"] = True
        if name == "file_claim" and result.get("status") == "accepted":
            state["filed"].add(filing_key)
        if name == "no_claim" and result.get("status") == "recorded":
            state["declared"].add(" ".join(tokenize(result["company"])))
        if name == "read_rule_document":
            if len(json.dumps(result)) > 16000:
                return {"error": "Document is too large for direct context. Use search_rules with focused keywords."}
            state["rules"] = state["rules"] or bool(result.get("text"))
        return result
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation) as error:
        return {"error": str(error)}


def should_stop(messages, tools):
    """Stop on explicit completion or before exceeding the course call budgets."""
    for message in messages:
        if message["role"] == "tool":
            result = json.loads(message["content"])
            if isinstance(result, dict) and result.get("status") == "finished":
                return True
    remaining = tools.budget()
    return remaining["model_calls_left"] <= 0 or remaining["tool_calls_left"] <= 0


def run_agent(request_text, tools):
    """Ask the model, run its requested tools, and return their results to it."""
    index = load_rule_index(tools)
    companies = sorted({p["company"] for p in index["passages"]})
    messages = [{"role": "system", "content": INSTRUCTIONS + "\nRule companies: " + ", ".join(companies)
                 + "\nAvailable traveler topics: " + json.dumps(tools.topics, ensure_ascii=False)},
                {"role": "user", "content": request_text}]
    definitions = tool_definitions()
    state = {"asked": {}, "forms": {}, "trains": {}, "timestamps": set(), "rules": False, "filed": set(),
             "declared": set(), "no_claim_evidence": False, "finished": False}
    for _ in range(20):
        if should_stop(messages, tools):
            break
        if len(json.dumps([messages, definitions], ensure_ascii=False)) > 190000:
            return "incomplete: conversation reached the context limit"
        response = tools.llm(messages, model=MODEL, tools=definitions,
                             parallel_tool_calls=False, max_tokens=1800)
        if not isinstance(response, dict):
            return "incomplete: invalid model response"
        if "error" in response:
            return "incomplete: model error: " + str(response["error"])
        try:
            message = response["choices"][0]["message"]
            if not isinstance(message, dict):
                raise ValueError("Expected a message object.")
            calls = message.get("tool_calls") or []
            if message.get("role") != "assistant":
                raise ValueError("Expected an assistant message.")
            if not isinstance(calls, list) or any(not isinstance(c, dict) or not isinstance(c.get("id"), str)
                                                 or not isinstance(c.get("function"), dict) for c in calls):
                raise ValueError("Invalid tool-call structure.")
        except (KeyError, IndexError, TypeError, ValueError):
            return "incomplete: invalid model response"
        # Replay the complete message, including encrypted_reasoning, unchanged.
        messages.append(message)
        if not calls:
            messages.append({"role": "user", "content": "Use the tools to continue, or finish after all decisions are recorded."})
        for call in calls:
            result = execute_tool(call, tools, index, state)
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)})
    return "finished" if state["finished"] else "incomplete: call budget or round limit reached"


def solve(request_text, tools):
    return run_agent(request_text, tools)
