# System interface and tools

## The system

A system is one Python file with a function `solve` and, if you want, a function `setup`:

```python
def setup(tools) -> None:
    ...

def solve(request_text: str, tools) -> str | None:
    ...
```

If your file has `setup`, we call it once per run, before the first request. Then we call `solve` once per request, each time in a fresh process and with a fresh `tools` object. You can return free text from `solve` for your own logs, and we don't grade it. We grade only the claims that your system files with `tools.file_claim` and, when nothing is owed, the answer that your system gives with `tools.no_claim`.

In `setup`, you can call only the rule document methods and `budget()`. Any other method returns `{"error": "..."}` in `setup`, and we count nothing there. We pass a new `tools` object to each call of `setup` and `solve`. After that call returns, every method of its `tools` object raises an exception.

### The cache

We run `setup` once per run in a process of its own. We run each `solve` in a new process, and we end that process when `solve` returns. So nothing in memory lasts from `setup` to `solve`, or from one request to the next. You can keep data between requests only in the folder `tools.cache_dir`. Write your index there in `setup`, and read it in `solve`.

- In `setup`, you can write to the cache folder. In `solve`, you can only read it.
- The cache can hold at most 20,000 entries (files plus folders), at most 64 folders deep, and at most 500 MB on disk. We count the disk space of each file, plus 4 KB for each entry, so many small or empty files count too. No single file can be larger than 500 MB.
- We check the cache every half second while your code loads in the setup process and while `setup` runs, and once more after `setup` ends. If the cache passes a limit, we end `setup` at once, empty the cache, and you see `setup error:CacheTooLarge`.
- In the final, we add the documents of the companies that aren't in the starter code before we call `setup`, so you can index them there.
- If `setup` raises an exception or runs out of time, we keep what it wrote, and we run the requests.
- Each new process adds about 0.1 seconds to a request, plus the time your code spends loading the cache, e.g., about 0.3 seconds for a 5 MB word index of all rule documents.

The tools are methods of `tools`:

| Group | Methods | Counted |
|---|---|---|
| The traveler | `ask_traveler(topic)` | Yes, as a tool call |
| Claim forms | `list_forms()`, `get_form(company)`, `file_claim(company, fields)`, `no_claim(company)` | Yes, as a tool call |
| Train data | `departures(...)`, `find_trains(...)`, `train_stops(train, date)` | Yes, as a tool call |
| Rule documents | `list_rule_documents(company=None)`, `read_rule_document(doc_id)` | No, free |
| Model calls | `llm(messages, model, **options)` | Yes, as a model call |
| Other methods | `budget()`, `topics` | No, free |

A tool doesn't raise an exception when something is wrong with the arguments or the data. You get a result `{"error": "..."}` with the reason in plain text, and the call still counts. Tools raise exceptions only at the limits (see "Limits" below) and after `setup` or `solve` returns.

We send each call of your code to the runner, which runs in another process. We keep the traveler's facts, the train data, the forms, the rule documents, the model token and the counters in the runner's process. So the arguments of every call must be JSON values, i.e., dicts, lists, text, numbers, `True`, `False` and `None`. For other arguments, you get `{"error": "..."}`, and the call counts.

## Limits

| Limit | Value | When you go over |
|---|---|---|
| Tool calls per request | 20 | The next call raises `BudgetExceeded`, and we end the request with the claims filed so far |
| Model calls per request | 15 | The same |
| Questions to the traveler | the number of facts that the answer depends on, plus 2 | You get the text `The traveler does not answer any more questions.`, the question counts as a tool call, and it costs 0.25 points |
| Time per request | 600 seconds, and 180 seconds in the Gradescope quick score and in `check_upload.py` | We end the request at the next call. If your code makes no call for 5 more seconds, we end its process |
| Time for `setup` | 600 seconds, and 180 seconds in the Gradescope quick score and in `check_upload.py` | We end the process of `setup` and run the requests with what it wrote to the cache |
| Size of the cache | 20,000 entries, 64 folders deep, 500 MB on disk | We end `setup` at once and empty the cache |
| Cost of the whole practice run | `--max-cost-usd`, by default $0.25 | We refuse a model call that could pass the cap. We then end the run, and we mark the remaining requests as not run, with 0 points |

Every tool call counts against the 20, including `ask_traveler`, calls that return an error, and rejected filings. We don't count reading the rule documents, because your system reads every document in `setup` to build its index.

We accept each argument up to a fixed length in characters, i.e., 40 for a date or time, 100 for a train name, 200 for a station, company or topic, 300 for a `doc_id`, and 20,000 for the fields of one claim, counted as JSON. If an argument is longer, you get an error, or a rejected filing from `file_claim` and `no_claim`, and the call still counts as one of your 20 tool calls. In the two rule document tools, you get the same error, and the call stays free.

We end a request with one of four exceptions: `BudgetExceeded`, `TimeLimit`, `CostLimit` and `ModelAccessError`. They derive from `BaseException`, so an `except Exception` in your code doesn't catch them. After one of them, every further call in the same request raises it again.

## Model calls

```python
resp = tools.llm(messages, model="gpt-4o-mini", **options)
```

- `model` is `gpt-4o-mini` or `gpt-6-luna`. We always run gpt-6-luna with reasoning effort `low`.
- `messages` is a list of chat messages with the role `system`, `developer`, `user`, `assistant` or `tool`, as in OpenAI's chat completions.
- `options` can be `tools`, `tool_choice`, `parallel_tool_calls`, `response_format`, `max_tokens` (or `max_completion_tokens`), `temperature`, `top_p`, `seed` and `reasoning_effort`.
  - `max_tokens` is a whole number from 1 to 4,000, and the default is 1,500. For gpt-6-luna, it includes the reasoning tokens.
  - `reasoning_effort` is `"low"` or left out. We refuse any other value.
  - `stream` is `False` or left out.
  - For gpt-6-luna, we leave out `temperature` and `top_p`, because OpenAI doesn't accept them for this model.
- The messages with their `encrypted_reasoning`, the tools and the response format of one call can have at most 200,000 characters together.
- You get the chat completion as a dict, e.g., `resp["choices"][0]["message"]`, with the cost of the call in `resp["cost_usd"]`.
- If we refuse the call or the call fails, you get `{"error": "..."}`, and the call still counts.

In the practice runner, we send your calls to the course app at `P2_BASE_URL` with your token `P2_TOKEN`, and we charge them to your team's budget. When we grade, we send the same calls to OpenAI with our own key, with the same models and settings, and you get answers in the same format.

### A tool call round trip

When the message in the answer has `tool_calls`, you continue in three steps.

1. Add the assistant message to your messages unchanged.
2. Run each tool call yourself, and add one message `{"role": "tool", "tool_call_id": ..., "content": "..."}` per call, with the result as text.
3. Call `tools.llm` again with the longer list of messages.

For gpt-6-luna, the assistant message with tool calls also has `encrypted_reasoning`, the model's reasoning for that turn in encrypted form. If you send the message back unchanged, the model continues from its own reasoning. If you leave `encrypted_reasoning` out, we still run the call, but the model starts its reasoning again. If you change `encrypted_reasoning`, we refuse the call.

### Costs

We count the tokens and the cost of every call. Prices in US dollars per million tokens:

| Model | Input | Cached input | Cache writes | Output |
|---|---|---|---|---|
| gpt-4o-mini | 0.15 | 0.075 | 0.15 | 0.60 |
| gpt-6-luna | 0.10 | 0.01 | 0.125 | 0.50 |

Reasoning tokens count as output tokens. Before each call, we reserve the most that the call could cost, in three steps.

1. We count each byte of the call's messages, tools and response format as one input token, and we add 1,000 tokens, all at the highest input price.
2. We add every output token up to `max_tokens`.
3. If the reservation could pass the run's cost cap, we refuse the call.

After the call, we count what it cost. If the run's total passes the cap, we end the run.

## Other methods

- `tools.budget()`: the calls you have left in this request, as `{"tool_calls_left": 13, "model_calls_left": 15}`.
- `tools.topics`: the topics from `topics.json`, as a dict from topic to meaning.

We give you no tool schemas for the model, and no method that runs a tool call of the model by name. You write the description of each tool for the model yourself, and your code runs each tool call in the model's answer. If you want the model to search the rules, you write that tool too, over your own index.

## The traveler

```python
tools.ask_traveler("ticket_price")   # in practice request P001: "25 euros."
```

You ask about one topic from `topics.json`. The answer is the traveler's own words, sometimes in German, French or Italian. For a topic that the traveler can't answer, you get `I don't know.` A topic that isn't on the list, or isn't text, gives an error, and that call doesn't count as a question (it still counts as a tool call). If you ask about the same topic twice, you get the same answer, and both questions count.

| Topic | Meaning |
|---|---|
| `travel_date` | the day of the trip |
| `departure_time` | the planned departure time of the first train, or of the train the traveler means |
| `route` | where the trip started, where the traveler changed trains and where it ended |
| `ticket_structure` | one through ticket for the whole trip, or separate tickets, and for which parts |
| `ticket_seller` | where each ticket was bought (company, app, website or ticket office) |
| `ticket_price` | the price paid for each ticket |
| `travel_class` | first or second class |
| `travelcard` | travelcards or passes, e.g., GA, Half Fare, BahnCard, Deutschland-Ticket, or none |
| `reservation` | whether the traveler had a seat reservation, with its reference |
| `booking_date` | when the tickets were bought |
| `train_after_missed_connection` | which train the traveler took after missing a connection or after a train stopped short |
| `arrival_time` | when the traveler actually arrived at the destination, as the traveler remembers it |
| `delay_reason` | the reason for the delay that staff announced, as the traveler remembers it |
| `payment_preference` | how the traveler would like to be paid, e.g., bank transfer or voucher |

## Rule documents

You read the rule documents for free, without using any of your 20 tool calls, because your system reads every document in `setup` to build its index and saves the index in the cache. We give you no rule search. You build the retrieval over the documents as part of your system, e.g., how you cut the documents into chunks and how you find the chunks for a request. You also choose how you rank the chunks and put them into the model's context.

### list_rule_documents(company=None)

```json
{"documents": [{"doc_id": "de-db-fahrgastrechte-de", "company": "DB", "title": "...", "language": "de",
                "chars": 7751}, ...]}
```

With `company`, you get only the documents of that company. You can write the company as in the `company` of its documents or its claim form, or as the `operator` of its trains in the train data, when only that company runs them; case, accents and extra spaces don't matter. In the final, the list also has the documents of companies that aren't in the starter code, so your system has to read and index them during the run, e.g., in `setup`.

### read_rule_document(doc_id)

The full text of one document, in Markdown: `{"doc_id", "company", "title", "language", "text"}`. The published documents are the same texts as the files in `rules/`.

## Train data

All three train tools read one store with the trains of Switzerland, Germany, France and Italy. In the practice runner, the store holds the practice days, and when we grade, it holds other days. Times are local, as `YYYY-MM-DD HH:MM` for planned times and `YYYY-MM-DD HH:MM:SS` or `YYYY-MM-DD HH:MM` for recorded times.

We match station names without regard to case or accents, against the names in all four countries' data. If a name matches no station or several, you get an error with suggestions, e.g., `unknown station 'Munchen'; did you mean: München Hbf, München Ost, ...`. City names in other languages, such as Genf or Mailand, don't match. If the store has no data for the day you ask about, you get an error with the days that it has.

### departures(station, date, from_time, to_time)

Departures with a planned time in the window, at most 4 hours and 40 rows.

In `departures` and `find_trains`, `to_time` has to differ from `from_time`. If `to_time` is earlier, e.g., `"23:30"` to `"00:30"`, the window runs past midnight into the next day. For `"18:26"` to `"18:26"`, you get an error, and the call counts as one of your 20 tool calls. If the window is longer than 4 hours, we cut it to 4 hours and add the note `"window cut to 4 hours"`.

```json
{"station": "Basel SBB", "from": "2026-10-20 21:30", "to": "2026-10-20 22:00",
 "departures": [{"train": "IC 641", "train_id": "tr-039dd0330332", "line": "IC6", "operator": "SBB", "to": "Bern",
   "dep_planned": "2026-10-20 21:30", "dep_recorded": "2026-10-20 21:30:42", "dep_delay_min": 0,
   "dep_kind": "measured", "dep_cancelled": false, "source": "CH"}, ...]}
```

### find_trains(origin, destination, date, from_time, to_time)

Direct trains that leave `origin` in the window and stop later at `destination`. For a trip with a change, you search each leg.

```json
{"origin": "Basel SBB", "destination": "Bern", "from": "2026-10-20 21:30", "to": "2026-10-20 22:00", "trains": [{"train": "ICE 377", "train_id": "tr-72643e1d572d", "line": "ICE", "operator": "SBB",
  "dep_planned": "2026-10-20 21:58", "dep_recorded": "2026-10-20 21:59:02", "dep_delay_min": 1, "dep_cancelled": false,
  "arr_planned": "2026-10-20 22:56", "arr_recorded": "2026-10-20 23:02:00", "arr_delay_min": 6, "arr_kind": "measured",
  "arr_cancelled": false}, ...], "note_direct_only": "direct trains only; for a change, search each leg"}
```

### train_stops(train, date)

All stops of a train on its service day, i.e., the day it started. You give the number with or without the category, e.g., `"ICE 201"` or `"201"`. Numbers aren't unique, e.g., 203 is an ICE and also a regional train near Zittau, so you can get several trains. You find a night train that started the day before under either date. If you get more than one run, pick the one whose planned departure from your station is on the date you asked about.

```json
{"trains": [{"train": "ICE 201", "train_id": "tr-122a9e584ff5", "line": "ICE", "operator": "...", "service_day": "2026-10-20",
  "stops": [..., {"station": "Basel SBB", "country": "CH", "arr_planned": "2026-10-20 20:48",
             "arr_recorded": "2026-10-20 21:29:10", "arr_delay_min": 41, "arr_kind": "measured",
             "arr_cancelled": false, "source": "CH"}]}]}
```

### Fields of the three train tools

- `train_id` is the id of the train run, e.g., `tr-122a9e584ff5`. Every result with the same run has the same `train_id`, and you can write it in the train field of a claim form.
- `*_delay_min` is the recorded time minus the planned time in whole minutes, rounded down, so 59 minutes and 59 seconds is 59.
- `*_kind` is how the time was recorded: `measured` (Swiss and Italian data), `recorded` (German data, the last value from a poll after the event), `announced` (French data, mostly rounded to 5 minutes) or `forecast`. We treat them as in ruling R4.
- `*_cancelled` is true when that arrival or departure didn't happen. A cancelled stop has no recorded time.
- `source`, in `departures` and `train_stops`, is the country data that the value came from: `CH`, `DE`, `FR` or `IT`. When two countries' data have the same train, we use the data of the station's own country (ruling R3).
- `operator` is the company that runs the train, with the name the timetable data uses. The name can differ from the company name on the claim forms, e.g., `DB Regio` for a DB train, and it isn't always the company that handles the claim.

Some of the data is odd. For example, a train can appear twice under two numbers, with one copy cancelled and one copy running under a replacement number. Moreover, a few stops have planned times but no recorded time, and some trains appear in two pieces, split at a border.

## Claim forms

### list_forms()

```json
{"forms": [{"company": "DB", "title": "Deutsche Bahn: Fahrgastrechte-Formular", "language": "de"}, ...]}
```

In the final, the list also has the forms of companies that aren't in the starter code.

### get_form(company)

```json
{"company": "DB", "title": "Deutsche Bahn: Fahrgastrechte-Formular", "language": "de", "instructions": "...",
 "fields": [{"name": "reisetag", "label": "Reisetag", "type": "date", "required": true, "format": "dd.mm.yyyy"},
            {"name": "zug", "label": "Zug mit Verspätung oder Ausfall", "type": "train", "required": true,
             "help": "Bei verpasstem Anschluss: der verspätete Zug, mit dem Sie am Umsteigebahnhof angekommen sind",
             "format": "z. B. ICE 100"}, ...]}
```

The title, the instructions, the labels and the help texts are in the company's language. The field types are:

- `date`, a real date in the form's `format`, e.g., `dd.mm.yyyy`;
- `time`, as `HH:MM`;
- `int`, a whole number;
- `amount`, a number with at most two decimals, with a dot;
- `enum`, one of the field's `values`;
- `train`, a train that your train searches returned earlier in the same request (see "The train field" below);
- `text`, at least `min_length` characters when the field has one.

A field with `required_if` is required when another field has the given value. For an unknown company, you get an error with the companies that have a form.

### file_claim(company, fields)

`fields` is a dict from field name to value. You get either an accepted claim or `{"status": "rejected", "errors": ["..."]}`, with the errors in the form's language. An accepted claim has the train of the train field in `train`, so you can see which train we filed:

```json
{"status": "accepted", "claim_id": "DB-0001", "message": "Vielen Dank, Ihr Antrag ist eingegangen.",
 "train": {"train": "ICE 201", "train_id": "tr-122a9e584ff5", "service_day": "2026-10-20", "from": "Hamburg-Altona",
           "to": "Basel SBB"}}
```

You file one claim per company and ticket. We grade the last accepted claim for each ticket, and earlier accepted claims for the same ticket count as duplicates. When a request has two tickets with the same company, we match each accepted claim to a ticket by the train on the form.

### The train field

In the train field of a form, you write a train that one of your train searches (`departures`, `find_trains` or `train_stops`) returned earlier in the same request. You can write it in one of these ways, and we ignore case and extra spaces:

- its name as the search wrote it, e.g., `ICE 201`;
- its number, e.g., `201`;
- its line with its name or number, e.g., `RB54 12469`;
- its `train_id`, e.g., `tr-122a9e584ff5`.

What you write has to fit exactly one train of your searches in the request. If it fits none, you get an error with the trains of your searches, each with its service day, its first and last stop and its `train_id`. If it fits several, e.g., `ICE 1080` on two service days, you get an error with those trains, and you can file again with the `train_id` of the train you mean. We keep the `train_id` of the accepted train with the claim, and we grade that train.

### no_claim(company)

When nothing is owed, your system files no claim and calls `no_claim` instead. Before the call, look up the relevant train with `train_stops` or a `find_trains` search that returns at most 5 trains. `company` is the company that would get the claim, as in `list_forms`.

You get `{"status": "recorded", "company": "...", "message": "..."}`, or `{"status": "rejected", "errors": ["..."]}` for an unknown company. We grade the answer only when we score the request, so a recorded answer can still score 0. The call counts against the 20 tool calls like every other tool. A wrong `no_claim` call, or a claim on a request where nothing is owed, costs 0.5 points from your total for the set. Your total never goes below 0. A `no_claim` call is wrong if a claim is owed or it names the wrong company. We grade the last `no_claim` call for the company that would get the claim.

You find the forms of the companies in the practice requests in `forms/`:

| Company | Form language | Date format | Payment values |
|---|---|---|---|
| SBB | German | dd.mm.yyyy | Überweisung, Gutschein |
| DB | German | dd.mm.yyyy | Überweisung, Gutschein |
| SNCF | French | dd/mm/yyyy | bon d'achat, virement |
| OUIGO | French | dd/mm/yyyy | bon d'achat |
| Trenitalia | Italian | dd/mm/yyyy | bonus, rimborso |
| Trenord | Italian | dd/mm/yyyy | bonifico, voucher |

On each form, you fill in these fields:

- the travel date, the late train and the stations of the ticket;
- the planned and the actual arrival, and the delay in minutes;
- the ticket price, the amount claimed and the payment form.

Some forms have more fields, e.g., the currency on the SBB form, and on the Trenord form, whether the delay was at the departure or at the arrival. The forms of the companies that aren't in the starter code have other fields and their own error messages, and your system reads them with `get_form` during the run.

## Scoring

You find the scoring rules and the score lines in README.md, under "Scoring" and "Run your system on the practice requests".

Under each request's score line in the practice runner, we print one line of checks for each expected claim, e.g.:

```text
{"company": "SBB", "filed": true, "amount": true, "train": true, "provenance": false, "provenance_issues": ["missing topic ticket_price"], "payment_ok": null, "ok": false}
```

- `filed`: your system filed an accepted claim with this company.
- `amount`, `train` and `provenance`: the three parts of the claim, and `ok` is true when all three are right.
- `provenance_issues`: what was wrong for `provenance`, i.e., `price is not the ticket price`, `price unreadable`, or `missing topic` with one or more topics. In `missing topic train_after_missed_connection|arrival_time`, the `|` means that an answer on either topic is enough.
- `payment_ok`: whether the payment field has the expected value, or `null` when no payment value is expected for the request. We don't count it in the points.

When `filed` is false, the line has no `provenance_issues` and no `payment_ok`.

When nothing is owed, the line has other fields, e.g.:

```text
{"company": "DB", "no_claim": true, "ok": false, "declared": true, "filed_anything": false, "topics_ok": true, "provenance": false}
```

- `no_claim`: true on every request where nothing is owed.
- `declared`: your system called `no_claim` for this company. If it's false, your system made no such call.
- `filed_anything`: your system filed a claim.
- `topics_ok`: before the `no_claim` call, your system asked about every fact that the decision depends on.
- `provenance`: before the `no_claim` call, the required train appeared in a `train_stops` result or in a complete `find_trains` result with at most 5 trains. We print it only when `declared` is true.
- `ok`: you get the request's point.
