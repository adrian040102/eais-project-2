"""Exercise the model/tool conversation with scripted responses and fake tools.

No model endpoint, train store, answer labels, or rule files are read. These
tests check control flow and tool evidence, not compensation-policy accuracy.
"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("agent_loop_system", PROJECT / "team" / "system.py")
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)


class FakeLimit(BaseException):
    """Match the kit's deliberately non-Exception budget/time limit behavior."""


def call(call_id, name, arguments):
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": encoded}}


def assistant(*calls, reasoning=None):
    message = {"role": "assistant", "content": None, "tool_calls": list(calls)}
    if reasoning is not None:
        message["encrypted_reasoning"] = reasoning
    return message


def prerequisites():
    return assistant(
        call("rules", "search_rules", {"query": "Example Rail compensation"}),
        call("form", "get_form", {"company": "Example Rail"}),
        call("trains", "find_trains", {"origin": "A", "destination": "B",
             "date": "2026-10-20", "from_time": "10:00", "to_time": "11:00"}),
    )


def filing(call_id="file", train="run-a", amount="12.50"):
    return assistant(call(call_id, "file_claim", {"company": "Example Rail",
                     "fields": {"train": train, "amount": amount}}))


def finish(call_id="finish"):
    return assistant(call(call_id, "finish", {}))


class FakeTools:
    def __init__(self, responses, tool_budget=20, model_budget=15, filings=None):
        self.responses = list(responses)
        self.tool_calls_left = tool_budget
        self.model_calls_left = model_budget
        self.calls = []
        self.model_requests = []
        self.filings = list(filings or [])
        self.raise_on = {}
        self.forbidden_calls = 0
        self.topics = {"ticket_price": "the price paid", "travel_date": "day of travel"}

    def budget(self):
        return {"tool_calls_left": self.tool_calls_left,
                "model_calls_left": self.model_calls_left}

    def llm(self, messages, model, **options):
        if self.model_calls_left <= 0:
            raise FakeLimit("model budget exceeded")
        self.model_calls_left -= 1
        self.model_requests.append({"messages": copy.deepcopy(messages),
                                    "model": model, "options": copy.deepcopy(options)})
        if not self.responses:
            raise AssertionError("Agent requested an unexpected model response")
        response = self.responses.pop(0)
        if not isinstance(response, dict) or "error" in response or "choices" in response:
            return response
        return {"choices": [{"message": response}]}

    def counted(self, name, arguments):
        if self.tool_calls_left <= 0:
            raise FakeLimit("tool budget exceeded")
        self.tool_calls_left -= 1
        self.calls.append({"name": name, "arguments": copy.deepcopy(arguments)})
        if name in self.raise_on:
            raise self.raise_on[name]

    def ask_traveler(self, topic):
        self.counted("ask_traveler", {"topic": topic})
        return "25 euros."

    def get_form(self, company):
        self.counted("get_form", {"company": company})
        return {"company": company, "title": "Synthetic claim form", "language": "en",
                "instructions": "One claim per ticket.", "fields": [
                    {"name": "train", "label": "Train", "type": "train", "required": True},
                    {"name": "amount", "label": "Compensation", "type": "amount", "required": True},
                ]}

    def find_trains(self, origin, destination, date, from_time, to_time):
        self.counted("find_trains", {"origin": origin, "destination": destination,
                     "date": date, "from_time": from_time, "to_time": to_time})
        return {"trains": [{"train": "EX 1", "train_id": "run-a", "operator": "Example Rail",
                            "arr_planned": "2026-10-20 15:50"},
                           {"train": "EX 2", "train_id": "run-b", "operator": "Example Rail",
                            "arr_recorded": "2026-10-20 19:33:00"}]}

    def file_claim(self, company, fields):
        self.counted("file_claim", {"company": company, "fields": fields})
        return self.filings.pop(0) if self.filings else {
            "status": "accepted", "claim_id": "synthetic-claim", "train": {"train_id": fields["train"]}}

    def train_stops(self, train, date):
        self.counted("train_stops", {"train": train, "date": date})
        return {"trains": [{"train_id": "run-a", "stops": [
            {"station": "B", "arr_planned": "2026-10-20 22:10", "arr_recorded": "2026-10-20 23:11:00"},
        ]}]}

    def no_claim(self, company):
        self.counted("no_claim", {"company": company})
        return {"status": "recorded", "company": company}

    def forbidden_method(self):
        self.forbidden_calls += 1
        return "This method must never be dispatched."


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.index = {"passages": [{"company": "Example Rail"}]}
        self.load = patch.object(system, "load_rule_index", return_value=self.index).start()
        self.search = patch.object(system, "search_rules", return_value={"passages": [{
            "doc_id": "synthetic-policy", "company": "Example Rail", "language": "en",
            "title": "Passenger rights", "section": "Compensation",
            "text": "Synthetic policy passage for control-flow tests.", "score": 1.0,
        }]}).start()
        self.addCleanup(patch.stopall)

    def run_agent(self, tools):
        return system.run_agent("Synthetic request about tickets on Example Rail.", tools)

    def named_calls(self, tools, name):
        return [item for item in tools.calls if item["name"] == name]

    def tool_reply(self, tools, request_number, call_id):
        messages = tools.model_requests[request_number]["messages"]
        replies = [message for message in messages
                   if message.get("role") == "tool" and message.get("tool_call_id") == call_id]
        self.assertEqual(len(replies), 1, (call_id, messages))
        self.assertIsInstance(replies[0]["content"], str)
        return replies[0], json.loads(replies[0]["content"])

    def test_tool_schemas_have_unique_explicit_names(self):
        schemas = system.tool_definitions()
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue({"search_rules", "finish", "file_claim", "no_claim",
                         "ask_traveler", "find_trains", "get_form"}.issubset(names))
        self.assertNotIn("forbidden_method", names)
        for schema in schemas:
            self.assertEqual(schema["type"], "function")
            self.assertEqual(schema["function"]["parameters"]["type"], "object")

    def test_reasoning_replay_matching_tool_ids_and_cached_retrieval(self):
        first = prerequisites()
        first["encrypted_reasoning"] = "opaque-synthetic-reasoning"
        original = copy.deepcopy(first)
        tools = FakeTools([first, filing(), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        self.assertEqual(first, original)
        self.assertIn(original, tools.model_requests[1]["messages"])
        for call_id in ("rules", "form", "trains"):
            reply, content = self.tool_reply(tools, 1, call_id)
            self.assertEqual(set(reply), {"role", "tool_call_id", "content"})
            self.assertIsInstance(content, dict)
        self.assertEqual(self.tool_reply(tools, 1, "rules")[1]["passages"][0]["doc_id"], "synthetic-policy")
        self.load.assert_called_once_with(tools)
        self.assertEqual(self.search.call_count, 1)
        self.assertIs(self.search.call_args.args[0], self.index)
        self.assertTrue(all(request["model"] == "gpt-6-luna" for request in tools.model_requests))

    def test_invalid_json_and_unlisted_method_return_errors_then_repair(self):
        broken = assistant(call("bad-json", "get_form", "{"),
                           call("not-object", "get_form", "[]"),
                           call("unknown", "forbidden_method", {}),
                           call("valid", "ask_traveler", {"topic": "ticket_price"}))
        tools = FakeTools([broken, prerequisites(), filing(), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        for call_id in ("bad-json", "not-object", "unknown"):
            self.assertIn("error", self.tool_reply(tools, 1, call_id)[1])
        self.assertEqual(self.tool_reply(tools, 1, "valid")[1], "25 euros.")
        self.assertEqual(tools.forbidden_calls, 0)
        self.assertEqual(len(self.named_calls(tools, "get_form")), 1)
        self.assertEqual(len(self.named_calls(tools, "ask_traveler")), 1)

    def test_rejected_filing_can_be_corrected_before_finishing(self):
        tools = FakeTools([prerequisites(), filing("first", amount="13.00"), filing("retry"), finish()], filings=[
            {"status": "rejected", "errors": ["Correct the amount field."]},
            {"status": "accepted", "claim_id": "synthetic-retry"},
        ])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 2)
        self.assertEqual(self.tool_reply(tools, 2, "first")[1]["status"], "rejected")
        self.assertEqual(self.tool_reply(tools, 3, "retry")[1]["status"], "accepted")

    def test_wrong_argument_value_types_are_errors_and_can_be_repaired(self):
        invalid = assistant(call("numeric-topic", "ask_traveler", {"topic": 123}),
                            call("list-fields", "file_claim", {"company": "Example Rail", "fields": []}))
        tools = FakeTools([prerequisites(), invalid, filing(), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        for call_id in ("numeric-topic", "list-fields"):
            self.assertIn("error", self.tool_reply(tools, 2, call_id)[1])
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)
        self.assertEqual(self.named_calls(tools, "ask_traveler"), [])

    def test_two_tickets_are_filed_before_explicit_finish(self):
        tools = FakeTools([prerequisites(), filing("ticket-a", "run-a"),
                           filing("ticket-b", "run-b"), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        claims = self.named_calls(tools, "file_claim")
        self.assertEqual([claim["arguments"]["fields"]["train"] for claim in claims], ["run-a", "run-b"])
        self.assertEqual(len(tools.model_requests), 4)

    def test_filing_before_rules_and_form_is_blocked_then_can_recover(self):
        tools = FakeTools([filing("premature"), prerequisites(), filing("ready"), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        self.assertIn("error", self.tool_reply(tools, 1, "premature")[1])
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)

    def test_unseen_train_is_blocked_before_native_filing(self):
        tools = FakeTools([prerequisites(), filing("wrong-train", "run-unseen"),
                           filing("known-train", "run-a"), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        self.assertIn("error", self.tool_reply(tools, 2, "wrong-train")[1])
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)

    def test_observed_train_name_is_resolved_before_duplicate_check(self):
        tools = FakeTools([prerequisites(), filing("named", "EX 1"), filing("duplicate", "run-a"), finish()])
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertEqual(self.named_calls(tools, "file_claim")[0]["arguments"]["fields"]["train"], "run-a")
        self.assertIn("error", self.tool_reply(tools, 3, "duplicate")[1])
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)

    def test_ambiguous_observed_name_requires_a_train_id(self):
        tools = FakeTools([prerequisites(), filing("ambiguous", "EX 1"), filing("exact", "run-a"), finish()])
        original = tools.find_trains

        def find(*args, **kwargs):
            result = original(*args, **kwargs)
            result["trains"][1]["train"] = "EX 1"
            return result

        tools.find_trains = find
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertIn("error", self.tool_reply(tools, 2, "ambiguous")[1])
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)

    def test_resolved_connection_times_and_delay_cannot_be_replaced_when_filing(self):
        fields = {"train": "EX 1", "amount": "12.50", "planned": "15:50", "actual": "19:33", "minutes": 223}
        responses = [prerequisites(), assistant(call("clarify", "resolve_connection", {})),
                     assistant(call("connection", "resolve_connection", {})),
                     assistant(call("amount", "calculate_compensation", {"price": "50", "minimum": "0",
                                    "rate_bands": [{"from_minutes": 0, "percentage": "25"}]})),
                     assistant(call("wrong-time", "file_claim", {"company": "Example Rail",
                                    "fields": {**fields, "planned": "16:00"}})),
                     assistant(call("wrong-delay", "file_claim", {"company": "Example Rail",
                                    "fields": {**fields, "minutes": 213}})),
                     assistant(call("correct", "file_claim", {"company": "Example Rail", "fields": fields})),
                     finish()]
        tools = FakeTools(responses)
        original_form = tools.get_form

        def form(company):
            result = original_form(company)
            result["fields"].extend([{"name": "planned", "type": "time"},
                                     {"name": "actual", "type": "time"}, {"name": "minutes", "type": "int"}])
            return result

        tools.get_form = form
        resolved = {"incoming": {"train_id": "run-a", "train": "EX 1"},
                    "planned_connection": {"train_id": "planned", "train": "EX 2"},
                    "replacement": {"train_id": "replacement", "train": "EX 3"},
                    "planned_arrival": "2026-10-20 15:50", "recorded_arrival": "2026-10-20 19:33:00",
                    "minutes": 223}
        with patch.object(system, "resolve_connection", return_value=resolved) as resolve:
            self.assertEqual(self.run_agent(tools), "finished")
        resolve.assert_called_once()
        self.assertIn("onward_answer", self.tool_reply(tools, 2, "clarify")[1])
        self.assertEqual(self.named_calls(tools, "ask_traveler")[0]["arguments"]["topic"],
                         "train_after_missed_connection")
        self.assertIn("error", self.tool_reply(tools, 5, "wrong-time")[1])
        self.assertIn("error", self.tool_reply(tools, 6, "wrong-delay")[1])
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)
        self.assertEqual(self.named_calls(tools, "file_claim")[0]["arguments"]["fields"]["train"], "run-a")

    def test_connection_rate_bands_use_the_final_delay_including_boundaries(self):
        bands = [{"from_minutes": 30, "percentage": "25"}, {"from_minutes": 120, "percentage": "50"},
                 {"from_minutes": 180, "percentage": "75"}]
        for minutes, amount in [(29, "0.00"), (30, "25.00"), (119, "25.00"), (120, "50.00"),
                                (179, "50.00"), (180, "75.00"), (223, "75.00")]:
            with self.subTest(minutes=minutes):
                decision = (assistant(call("nothing", "no_claim", {"company": "Example Rail"}))
                            if amount == "0.00" else filing(amount=amount))
                tools = FakeTools([
                    prerequisites(), assistant(call("clarify", "resolve_connection", {})),
                    assistant(call("connection", "resolve_connection", {})),
                    assistant(call("missing-bands", "calculate_compensation",
                                   {"price": "100", "percentage": "1", "minimum": "0"})),
                    assistant(call("with-bands", "calculate_compensation",
                                   {"price": "100", "percentage": "1", "minimum": "0", "rate_bands": bands})),
                    decision, finish(),
                ])
                resolved = {"incoming": {"train_id": "run-a", "train": "EX 1"},
                            "planned_connection": {"train_id": "planned", "train": "EX 2"},
                            "replacement": {"train_id": "replacement", "train": "EX 3"},
                            "planned_arrival": "2026-10-20 15:50", "recorded_arrival": "2026-10-20 19:33:00",
                            "minutes": minutes}
                with patch.object(system, "resolve_connection", return_value=resolved):
                    self.assertEqual(self.run_agent(tools), "finished")
                self.assertIn("error", self.tool_reply(tools, 4, "missing-bands")[1])
                self.assertEqual(self.tool_reply(tools, 5, "with-bands")[1], {"amount": amount})

    def test_payment_choice_is_requested_before_a_filing_is_sent(self):
        tools = FakeTools([prerequisites(), filing("needs-choice"), filing("with-choice"), finish()])
        original_form = tools.get_form

        def form(company):
            result = original_form(company)
            result["fields"].append({"name": "method", "type": "enum", "values": ["voucher", "transfer"]})
            return result

        tools.get_form = form
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertIn("payment_preference", self.tool_reply(tools, 2, "needs-choice")[1])
        self.assertEqual(len(self.named_calls(tools, "ask_traveler")), 1)
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)

    def test_currency_and_delay_enums_do_not_trigger_payment_questions(self):
        tools = FakeTools([prerequisites(), filing(), finish()])
        original_form = tools.get_form

        def form(company):
            result = original_form(company)
            result["fields"].extend([{"name": "currency", "type": "enum", "values": ["CHF", "EUR"]},
                                     {"name": "delay_at", "type": "enum", "values": ["in arrivo", "in partenza"]}])
            return result

        tools.get_form = form
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertEqual(self.named_calls(tools, "ask_traveler"), [])

    def test_duplicate_claim_and_contradictory_no_claim_are_blocked(self):
        duplicate = filing("duplicate")["tool_calls"][0]
        contradictory = call("contradictory", "no_claim", {"company": "Example Rail"})
        tools = FakeTools([prerequisites(), filing(), assistant(duplicate, contradictory), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        for call_id in ("duplicate", "contradictory"):
            self.assertIn("error", self.tool_reply(tools, 3, call_id)[1])
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)
        self.assertEqual(self.named_calls(tools, "no_claim"), [])

    def test_recorded_no_claim_allows_finish_without_filing(self):
        tools = FakeTools([prerequisites(), assistant(call("nothing-owed", "no_claim",
                           {"company": "Example Rail"})), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        self.assertEqual(len(self.named_calls(tools, "no_claim")), 1)
        self.assertEqual(self.named_calls(tools, "file_claim"), [])

    def test_missing_payout_minimum_is_an_error_then_zero_payout_can_finish(self):
        tools = FakeTools([
            prerequisites(),
            assistant(call("missing-minimum", "calculate_compensation", {"price": "12.50", "percentage": "25"})),
            assistant(call("with-minimum", "calculate_compensation",
                           {"price": "12.50", "percentage": "25", "minimum": "4"})),
            assistant(call("nothing-owed", "no_claim", {"company": "Example Rail"})),
            finish(),
        ])
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertIn("error", self.tool_reply(tools, 2, "missing-minimum")[1])
        self.assertEqual(self.tool_reply(tools, 3, "with-minimum")[1], {"amount": "0.00"})
        self.assertEqual(self.named_calls(tools, "file_claim"), [])

    def test_zero_payout_cannot_be_filed_then_no_claim_can_finish(self):
        tools = FakeTools([
            prerequisites(),
            assistant(call("zero-payout", "calculate_compensation",
                           {"price": "12.50", "percentage": "25", "minimum": "4"})),
            filing("zero-filing", amount="0.00"),
            assistant(call("nothing-owed", "no_claim", {"company": "Example Rail"})),
            finish(),
        ])
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertIn("error", self.tool_reply(tools, 3, "zero-filing")[1])
        self.assertEqual(self.named_calls(tools, "file_claim"), [])

    def test_positive_payout_after_zero_allows_another_ticket_to_be_filed(self):
        tools = FakeTools([
            prerequisites(),
            assistant(call("first-ticket", "calculate_compensation",
                           {"price": "12.50", "percentage": "25", "minimum": "4"})),
            assistant(call("second-ticket", "calculate_compensation",
                           {"price": "50.00", "percentage": "25", "minimum": "4"})),
            filing("payable-ticket", "run-b"),
            finish(),
        ])
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)

    def test_positive_calculation_blocks_no_claim_then_allows_filing(self):
        tools = FakeTools([
            prerequisites(),
            assistant(call("owed", "calculate_compensation", {"price": "50", "percentage": "25", "minimum": "4"})),
            assistant(call("contradiction", "no_claim", {"company": "Example Rail"})),
            filing(), finish(),
        ])
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertIn("error", self.tool_reply(tools, 3, "contradiction")[1])
        self.assertEqual(self.named_calls(tools, "no_claim"), [])

    def test_recalculation_can_establish_no_payout_before_no_claim(self):
        tools = FakeTools([
            prerequisites(),
            assistant(call("old", "calculate_compensation", {"price": "50", "percentage": "25", "minimum": "4"})),
            assistant(call("revised", "calculate_compensation", {"price": "10", "percentage": "25", "minimum": "4"})),
            assistant(call("nothing-owed", "no_claim", {"company": "Example Rail"})), finish(),
        ])
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertEqual(len(self.named_calls(tools, "no_claim")), 1)

    def test_delay_rejects_invented_time_then_accepts_equivalent_observed_timestamps(self):
        tools = FakeTools([
            prerequisites(),
            assistant(call("invented-time", "calculate_delay",
                           {"planned": "2026-10-20 16:00", "recorded": "2026-10-20 19:33"})),
            assistant(call("observed-times", "calculate_delay",
                           {"planned": "2026-10-20T15:50:00", "recorded": "2026-10-20T19:33:00"})),
            filing(),
            finish(),
        ])
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertIn("error", self.tool_reply(tools, 2, "invented-time")[1])
        self.assertEqual(self.tool_reply(tools, 3, "observed-times")[1], {"minutes": 223})

    def test_delay_accepts_timestamps_inside_train_stops(self):
        tools = FakeTools([
            prerequisites(),
            assistant(call("stops", "train_stops", {"train": "EX 1", "date": "2026-10-20"})),
            assistant(call("stop-times", "calculate_delay",
                           {"planned": "2026-10-20T22:10:00", "recorded": "2026-10-20T23:11:00"})),
            filing(),
            finish(),
        ])
        self.assertEqual(self.run_agent(tools), "finished")
        self.assertEqual(self.tool_reply(tools, 3, "stop-times")[1], {"minutes": 61})

    def test_repeated_normalized_topic_reuses_the_first_answer(self):
        questions = assistant(call("first-question", "ask_traveler", {"topic": "ticket_price"}),
                              call("same-question", "ask_traveler", {"topic": " Ticket-Price "}))
        tools = FakeTools([questions, prerequisites(), filing(), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        self.assertEqual(len(self.named_calls(tools, "ask_traveler")), 1)
        self.assertEqual(self.tool_reply(tools, 1, "first-question")[1],
                         self.tool_reply(tools, 1, "same-question")[1])

    def test_premature_finish_returns_error_and_can_recover(self):
        tools = FakeTools([finish("too-early"), prerequisites(), filing(), finish()])
        result = self.run_agent(tools)
        self.assertNotIn("incomplete", str(result).lower())
        self.assertIn("error", self.tool_reply(tools, 1, "too-early")[1])
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)

    def test_model_error_returns_incomplete_without_declaring_no_claim(self):
        tools = FakeTools([{"error": "no model calls in this test"}])
        result = self.run_agent(tools)
        self.assertIn("incomplete", str(result).lower())
        self.assertEqual(tools.calls, [])
        self.assertEqual(len(tools.model_requests), 1)

    def test_invalid_model_shapes_are_incomplete_without_native_tool_calls(self):
        responses = [None, [], {"choices": []}, {"choices": [{"message": "not an object"}]},
                     {"role": "user", "content": "wrong role"},
                     assistant(call(123, "get_form", {"company": "Example Rail"}))]
        for response in responses:
            with self.subTest(response=response):
                tools = FakeTools([response])
                result = self.run_agent(tools)
                self.assertIn("incomplete", str(result).lower())
                self.assertEqual(tools.calls, [])
                self.assertEqual(len(tools.model_requests), 1)

    def test_empty_budgets_stop_without_making_a_model_call(self):
        for budgets in ({"tool_budget": 0}, {"model_budget": 0}):
            with self.subTest(budgets=budgets):
                tools = FakeTools([], **budgets)
                result = self.run_agent(tools)
                self.assertIn("incomplete", str(result).lower())
                self.assertEqual(tools.model_requests, [])
                self.assertEqual(tools.calls, [])

    def test_tool_budget_exhaustion_is_incomplete_without_an_extra_call(self):
        tools = FakeTools([assistant(call("question", "ask_traveler", {"topic": "ticket_price"}))],
                          tool_budget=1)
        result = self.run_agent(tools)
        self.assertIn("incomplete", str(result).lower())
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(len(tools.model_requests), 1)

    def test_model_budget_exhaustion_after_search_is_incomplete(self):
        tools = FakeTools([assistant(call("rules", "search_rules", {"query": "compensation"}))],
                          model_budget=1)
        result = self.run_agent(tools)
        self.assertIn("incomplete", str(result).lower())
        self.assertEqual(len(tools.model_requests), 1)
        self.assertEqual(tools.calls, [])
        self.assertEqual(self.search.call_count, 1)

    def test_final_allowed_model_response_still_executes_its_filing(self):
        tools = FakeTools([prerequisites(), filing()], model_budget=2)
        result = self.run_agent(tools)
        self.assertIn("incomplete", str(result).lower())
        self.assertEqual(tools.model_calls_left, 0)
        self.assertEqual(len(self.named_calls(tools, "file_claim")), 1)

    def test_kit_style_baseexception_limit_propagates(self):
        tools = FakeTools([assistant(call("question", "ask_traveler", {"topic": "ticket_price"}))])
        tools.raise_on["ask_traveler"] = FakeLimit("request ended")
        with self.assertRaises(FakeLimit):
            self.run_agent(tools)

    def test_free_tool_loop_is_bounded_even_with_an_overgenerous_budget(self):
        responses = [assistant(call(f"search-{i}", "search_rules", {"query": "compensation"}))
                     for i in range(25)]
        tools = FakeTools(responses, model_budget=25)
        result = self.run_agent(tools)
        self.assertIn("incomplete", str(result).lower())
        self.assertLessEqual(len(tools.model_requests), 20)
        self.assertEqual(tools.calls, [])

    def test_compensation_rounds_half_up_then_applies_the_minimum(self):
        self.assertEqual(system.calculate_compensation("12.25", "25", "0"), {"amount": "3.06"})
        self.assertEqual(system.calculate_compensation("12.50", "25", "0"), {"amount": "3.13"})
        self.assertEqual(system.calculate_compensation("12.50", "25", "4"), {"amount": "0.00"})

    def test_delay_floors_seconds_and_handles_midnight(self):
        self.assertEqual(system.calculate_delay("2026-10-20 13:35:00", "2026-10-20 14:34:59"),
                         {"minutes": 59})
        self.assertEqual(system.calculate_delay("2026-10-20 22:13", "2026-10-21 00:23:00"),
                         {"minutes": 130})


if __name__ == "__main__":
    unittest.main()
