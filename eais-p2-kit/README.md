# Project 2: claims assistant for late trains

## Start here

Run every command below in the kit folder, `eais-p2-kit`, which you get when you unzip the kit from the Project 2 page of the course app. You need a minute or less for each step. When you download a new version of the kit, unzip it into a new, empty folder, so that you don't overwrite your `team/` folder, and then copy your `team/` folder into the new kit folder.

1. Make a Python 3.10 venv, i.e., a folder with its own Python and packages, and turn it on. We run your code with Python 3.10, the version on Gradescope, so code that uses a feature of Python 3.11 or later, e.g., `tomllib`, fails in grading.
   ```bash
   uv venv --seed --python 3.10 venv
   source venv/bin/activate
   ```
   If you don't have uv, you install it with `curl -LsSf https://astral.sh/uv/install.sh | sh`. If you already have Python 3.10, you can make the venv with `python3.10 -m venv venv` instead. In each new terminal, you turn the venv on again with `source venv/bin/activate`. On Ubuntu and WSL, the system Python is 3.12, and you can't install packages into it with pip, so you need the venv there too.
2. Install the packages at the versions we grade with, and certifi, so that HTTPS works with every Python, e.g., with the python.org Python on a Mac:
   ```bash
   python3 -m pip install --no-deps -r packages.txt -r requirements.txt
   ```
3. Build the train store, i.e., the file `data/trains.sqlite` with every train and stop of the practice days, which you search with the three train tools. You build it once, from the practice files in `data/`, in about 25 seconds, and the file is about 0.7 GB.
   ```bash
   python3 -I make_store.py
   ```
4. Set two environment variables in your shell. On the Project 2 page of the course app, make your Project 2 token, and copy these two lines.
   ```bash
   export P2_BASE_URL="https://eais-discussion-production.up.railway.app/api/p2"
   export P2_TOKEN="<your token from the Project 2 page>"
   ```
5. Run the example system on all the practice requests:
   ```bash
   python3 -I run_practice.py --system examples/example_system.py
   ```
   The example files no claim, so every request scores 0. At the end, we print the path of the run's log, and in the log you see what each counted tool returned.
6. Build your system in `team/system.py`. The kit already has this file, with an empty function for each part that you build (see "What you build" below). Put your own modules and files in `team/` too. You hand in this folder (see "Hand in" below), and we can run your system in the sandbox only from a folder of its own.

We write `python3 -I` in every command. With `-I`, Python starts without your own Python settings and without the packages that you installed outside the venv, so you import the same packages as in grading.

You can make Project 2 model calls once all three members of your team have confirmed the team on the Project 2 page of the course app. Your team then has $20 for Project 2 model calls. The example system makes no model calls, so the run in step 5 costs nothing, also before your team is confirmed. To check your token, run `python3 -I run_practice.py --system examples/probe_llm.py --only P001`, which makes one short call to each model and costs less than a cent. To try your own system without model calls, e.g., before your team is confirmed, add `--no-model` to the command. Then each model call returns an error, and the run costs nothing.

## What you build

You build a system that helps a traveler claim compensation for a late train in Switzerland, Germany, France or Italy. Your system is one Python file, `team/system.py`, with a function `solve(request_text, tools)`, and we call it once for each traveler's message.

You write the agent yourself. We give you the tools and the model calls with `tools.llm`, but no agent loop and no descriptions of the tools for the model. In `team/system.py`, you build these four parts, each in its own function:

- `build_rule_index(tools)`: your search over the rule documents. You build it in `setup(tools)` and save it in `tools.cache_dir`.
- `tool_definitions()`: how you describe each kit tool, and your own rule search, to the model.
- `run_agent(request_text, tools)`: your agent loop, i.e., the message history and the tool calls that the model asks for.
- `should_stop(messages, tools)`: your stopping rule, i.e., when you end the loop and return from `solve`.

For each request, your system works through four steps:

1. It asks the traveler for the facts that are missing from the message.
2. It looks up the trains.
3. It finds the rule of the company that handles the claim.
4. It files the claim on that company's form. When nothing is owed, it calls `tools.no_claim` with the company that would get the claim.

Your file can also have a function `setup(tools)`. We call it once per run, before the first request, so that your system can read all rule documents, build its index and save the index in a cache folder.

For the checkpoint and the final, we run your code ourselves on requests that you haven't seen, with the train data of other days. In the final, we also add companies that aren't in the kit, with their own rule documents and claim forms (see "Companies you haven't seen" below). In the Gradescope quick score, we run hidden requests on the practice days, with the kit companies only.

## What is in the kit

| Path | Contents |
|---|---|
| `run_practice.py` | The practice runner, which runs your system on the practice requests and prints the score of each request |
| `chat.py` and `chat_ui/` | The chat page, where you send your own requests to your system and answer its questions as the traveler (see "Chat with your system") |
| `check_upload.py` | The upload check, which you run on your zip file before you hand it in (see "Hand in") |
| `make_store.py` | The script that builds the train store `data/trains.sqlite` from the practice files |
| `TOOLS.md` | The tools with their arguments and results, the limits, the model calls and the scoring |
| `RULINGS.md` | Our rules for the train data, the travelers' requests and the scoring, e.g., the minimum time to change trains at each station |
| `SCOPE.md` | The trips and tickets in scope, the rule documents and the train data. You find what each company pays in that company's own rule documents |
| `topics.json` | The 14 topics you can ask the traveler about |
| `rules/` | The 45 rule documents of the published companies and the documents they refer to, with `index.json` |
| `forms/` | The claim forms of the companies in the practice requests, i.e., SBB, DB, SNCF, OUIGO, Trenitalia and Trenord |
| `practice/practice_requests.json` | The practice requests, each with the traveler's fact sheet and the expected claims. Each expected claim names its train by number and by `runs`, the `train_id` values that we accept for it. When nothing is owed, the answer has `no_claim` instead, with the company, the number of the train and `required_runs`, the `train_id` values of that train. The fact sheet holds the traveler's answer to each topic, and `ask_traveler` returns the answers from it |
| `data/` | The practice files, i.e., `trains-2026-10-19.csv.gz` to `trains-2026-10-26.csv.gz` with the train data of the practice days in Switzerland, Germany, France and Italy, and `stations.csv` |
| `team/system.py` | The empty system that you build on, with one function for each part that you build (see "What you build") |
| `examples/` | Three example scripts: `example_system.py` calls each tool except `file_claim` and `no_claim` once, prints what each tool returns, and makes no model call; `probe_llm.py` makes one short call to each model, so that you can check your token; `budget_probe.py` runs into the limit of 20 tool calls |
| `p2kit/` | The code of the tools, the model calls, the runner, the sandbox, the upload check and the scoring |
| `packages.txt` | The Python packages that your system can import, with their versions |
| `requirements.txt` | certifi, for HTTPS with the python.org Python on a Mac |
| `MANIFEST.json` | The size and SHA-256 of each file in the kit |

Don't change the files in `p2kit/`, because we grade with our own copy.

## Python and packages

We run your code with Python 3.10. Your system can import the Python standard library, your own modules, `p2kit` and the packages below, and nothing else.

| Package | Version | You import it as |
|---|---|---|
| numpy | 2.2.6 | `numpy` |
| scipy | 1.15.3 | `scipy` |
| scikit-learn | 1.7.2 | `sklearn` |
| rank-bm25 | 0.2.2 | `rank_bm25` |
| rapidfuzz | 3.14.5 | `rapidfuzz` |
| regex | 2026.9.29 | `regex` |
| python-dateutil | 3.0.0 | `dateutil` |
| pydantic | 2.14.1 | `pydantic` |

You can also import the packages that these packages depend on, i.e., joblib, threadpoolctl, cloudpickle, six, tzdata, pydantic-core, annotated-types, typing-extensions and typing-inspection. You find all of them with their versions in `packages.txt`. To install the same versions in your venv, run this in the kit folder, as in step 2 of "Start here":

```bash
python3 -m pip install --no-deps -r packages.txt -r requirements.txt
```

If your system imports any other package, Python can't load your file in grading. You then get 0 points on every request, with the status `error:ModuleNotFoundError`.

## How a request works

1. We call your `solve(request_text, tools)` with one traveler's message, in English, German, French or Italian. Travelers describe trains by time, brand and route, and not by train number.
2. Your system asks the traveler about one topic at a time with `tools.ask_traveler(topic)`. The traveler answers in their own words, sometimes in another language.
3. Your system looks up trains with `departures`, `find_trains` and `train_stops`. Each train in a result has a `train_id`, the id of that train run.
4. Your system finds the rule of the company that handles the claim. We give you no rule search, so you build the retrieval yourself, e.g., with chunks, with an index that you build in `setup(tools)` with `list_rule_documents` and `read_rule_document`, and with a model call that ranks the chunks.
5. Your system reads a company's form with `get_form(company)` and files the claim with `file_claim(company, fields)`. If a field is wrong or missing, you get a plain-text error in the form's language, and you can try again. In the train field, you name a train that your train searches returned earlier in the same request (see "The train field" in TOOLS.md).
6. When nothing is owed, your system files no claim. Instead, it calls `no_claim(company)` for the company that would get the claim.

You make all model calls with `tools.llm(...)`, with gpt-4o-mini or gpt-6-luna. We always run gpt-6-luna with low reasoning effort. In the practice runner, we send your calls to the course app, and we charge them to your team's budget. When we grade, we make the same calls with our own key.

## How we run your system

Your system runs in processes of its own, and the runner runs in another process. When your code calls a method of `tools`, we send the call to the runner, the runner runs the tool, and you get back only the result. We keep the traveler's facts, the train data, the forms, the rule documents, the model token and the counters in the runner's process, so your code can reach them only through `tools`.

We call `setup(tools)` once per run, in a process of its own, before the first request. Then we call `solve(request_text, tools)` once per request, each time in a fresh process, and we end that process when `solve` returns. So nothing in memory lasts from one request to the next, e.g., a module variable or a thread.

You can keep data between requests only in the cache folder `tools.cache_dir`. In `setup`, you write your index there, and in `solve`, you can only read it. Each new process adds about 0.1 seconds to a request, plus the time your code spends loading the cache.

When we grade, we also run your process in a sandbox on Linux, with no network. In the sandbox, your code can read only its own folder, the kit's code, the packages and the cache. It can write only to its own `/tmp`, and in `setup`, to the cache.

To try your system in the same sandbox, add `--sandbox` to the practice runner. You need Linux or WSL with bubblewrap, which you install on Ubuntu and WSL with `sudo apt install bubblewrap`. In the sandbox, your code can import the packages of `packages.txt` from your venv, read-only, and no other package of the venv, as in grading. Your system has to be in a folder of its own, e.g., `team/`, because we can't mount the kit folder itself into the sandbox.

## Limits

Per request, you have 20 tool calls, 15 model calls and 600 seconds. Every tool call counts, including `ask_traveler`, calls that return an error, and rejected filings. You also have 600 seconds for `setup(tools)`, and the cache can hold at most 20,000 files and folders and 500 MB. You have the 600 seconds in the practice runner, the chat page, the checkpoint and the final. In the Gradescope quick score and in `check_upload.py`, you have 180 seconds per request and 180 seconds for `setup(tools)`.

We don't count reading the rule documents against the 20 tool calls, because your system reads every document in `setup(tools)` to build its index. `tools.budget()` and `tools.topics` are free too.

For each practice run, you set a cost cap with `--max-cost-usd`, and the default is $0.25. Before each model call, we reserve the most that the call could cost, and we refuse a call that could pass the cap. We then end the run, and we mark the remaining requests as not run, with 0 points. In the checkpoint and the final, the cost cap of each run is $0.02 times the number of requests, e.g., $2.00 for 100 requests, and the cap is for all requests of the run together. In the Gradescope quick score, the cost cap is $0.10 for each upload, and at either cap we end the run in the same way. You find the details of every limit in TOOLS.md.

In our own tests, an agent loop with gpt-6-luna cost about half a cent per request, so a run on all the practice requests can reach the default cap before the last request. To raise the cap, give a higher amount, e.g., `--max-cost-usd 1.00`. We charge what the run actually costs to your team's budget, so keep the cap at an amount your team can pay.

## Companies you haven't seen

In the final, we add the rule documents and claim forms of companies that aren't in the kit. You find them in the same lists as the published companies, i.e., in `tools.list_rule_documents()` and `tools.list_forms()`.

So you have to write your system so that it reads and indexes new documents during the run, in `setup(tools)`. It also has to fill in forms whose fields it has never seen, from what `get_form` returns. We add the new documents before we call `setup`, so you can index them there.

## Rules for your system

- Make every model call through `tools.llm`. We remove `P2_TOKEN` from the environment before we start your system.
- Use only the methods of `tools` in TOOLS.md.
- Import only the standard library, your own modules, `p2kit` and the packages in `packages.txt`.
- Keep data between requests only in the files that your `setup(tools)` writes into the cache.
- Read no files outside your own folder and the kit's code. In grading, your code can't read any other file.
- Don't share the kit outside the course.
- Everything your system prints goes to the run log, and you won't see the run log of the grading run.

## Scoring

Each claim in the answer key is worth one point. You get the point only if all three of these are right:

- the amount, within one cent, and in the right currency when the form has a currency field;
- the train whose delay or cancellation caused the claim (for a missed connection, the late train you came in on);
- where the facts came from, which means two things. The ticket price on the form, when the form asks for it, is the price of the ticket as the form's help text describes it, and before the filing, your system asked about every fact that the claim depends on and that the request leaves out.

For the train, we accept the form's train field only when it names exactly one train that your train searches returned earlier in the same request, and we keep the `train_id` of that train with the claim. We then check that train. So `ICE 377`, `377` and the train's `train_id` all count, as long as your searches in the request returned only one train numbered 377.

When nothing is owed, the request is worth one point only if all four of these are right:

- your system called `no_claim` for the company that would get the claim;
- your system filed no claim;
- before the `no_claim` call, the required train appeared in `train_stops` or in a complete `find_trains` result with at most 5 trains;
- before the `no_claim` call, your system asked about every fact that the decision depends on and that the request leaves out.

If your system files nothing and makes no `no_claim` call, you get 0 points for the request. We grade the last `no_claim` call for the company that would get the claim.

We take off 0.5 points for each extra or duplicate claim. A wrong `no_claim` call, or a claim on a request where nothing is owed, costs 0.5 points from your total for the set. Your total never goes below 0. A `no_claim` call is wrong if a claim is owed or it names the wrong company. We take off 0.25 points for each question after the question limit. A request never scores below 0. The question limit of a request is the number of facts that the answer depends on, plus 2.

## Run your system on the practice requests

In the kit folder:

```bash
python3 -I run_practice.py --system team/system.py --max-cost-usd 0.25
```

| Option | Meaning |
|---|---|
| `--only P001,P003` | Run only these requests |
| `--max-cost-usd 0.25` | The cost cap for the whole run, in US dollars |
| `--trains FILE` | Another train store, instead of `data/trains.sqlite` (or set `P2_TRAINS`) |
| `--timeout-s 600` | The time per request in seconds |
| `--out DIR` | The folder for the log, instead of `runs/<time>-<process id>-<system>/`. If you give `--out` to several runs at once, give each run its own folder |
| `--no-model` | Make no model calls: each call of `tools.llm` returns an error, and the run costs nothing |
| `--sandbox` | Run your system in the sandbox that we use in grading, with the packages of `packages.txt` from your venv (Linux or WSL with bubblewrap) |
| `--sandbox-ro PATH` | With `--sandbox`, another read-only folder for your system's imports. We mount it at `/extra/<n>` and add it to the path. You can give it more than once |

If your system has `setup`, you first get a line such as `setup ok cache=4.8 MB entries=12 seconds=2.5`. If your file doesn't load, e.g., because of an import error, or if `setup` raises an exception, the line starts with `setup error:` and the type of the error, e.g., `setup error:ModuleNotFoundError`, and the next line has the error message. Then you get one line per request, and under it the checks of each expected claim:

```text
id     points penalty  claims extra  quest over tools model      usd  status  issues
P001  1.00/1     0.00   1/1       0   2/3     0     7     4   0.0021  ok
P016  0.00/1     0.00   0/1       0   1/4     0     6     3   0.0018  ok      AP
```

- `points` is the request's score before the separate `penalty` for wrong `no_claim` calls or claims where nothing is owed. We subtract those penalties from the sum of request points, then floor the set total at 0.
- `claims` is the number of right claims out of the expected claims, `quest` is the number of questions out of the limit, and `over` is the number of questions after the limit.
- `status` is one of these:
  - `ok`;
  - `budget`, when your system used up the 20 tool calls or the 15 model calls;
  - `time`;
  - `cost-cap`;
  - `no-model`, when the token or the address is missing, or when the token is wrong. With a wrong address, you get failed model calls instead (see the `TOTAL` lines below);
  - `not-run`, for the requests after a `cost-cap` or a `no-model`;
  - `error:<type>`, for a problem in your code, e.g., an exception such as `error:KeyError`, a file that doesn't load, such as `error:SyntaxError`, or a process that ended during a request (`error:ProcessExited`).
- The issue codes for a claim that we expected are `A` (amount), `T` (train), `P` (where the facts came from) and `missing-claim`. `no-claim-when-owed` means that your system called `no_claim`, but a claim is owed.
- The issue codes when nothing is owed are `filed-when-nothing-owed`, `missing-no-claim` (no `no_claim` call for the company that would get the claim), `no-claim-P` (the required train did not appear in a train-data result before the call), `no-claim-wrong-company` (a call for another company) and `topics` (a fact your system didn't ask about before the `no_claim` call).

At the end, you get two `TOTAL` lines with the points, the claims, the calls, the tokens and the cost of the whole run. If model calls failed, e.g., because your team isn't confirmed yet (HTTP 403) or the address is wrong (HTTP 404), the second line includes their number, e.g., `model 46 (0 input and 0 output tokens), 46 of them failed`, and the line under it has the first error.

We write every tool call, every model call, every error and everything your system printed to `runs/<time>-<process id>-<system>/log.jsonl`, with a summary in `summary.json`. At the end of the run, we print the path of the log again. What your system printed is in the `stdout` line of each request, and each tool call and its result are in a `tool` line. You find the meaning of the checks under each request in TOOLS.md, under "Scoring". What your code prints while Python loads your file, and what other processes that your code starts print, go to `child-output.txt` in the same folder.

## Chat with your system

In the chat page, you type a traveler's request, your system runs on it, and you answer its questions as the traveler. You can also run a practice request again, with the answers from its fact sheet, and see every tool call and the score. In the kit folder, run:

```bash
python3 -I chat.py --system team/system.py
```

| Option | Meaning |
|---|---|
| `--rules-extra DIR` | Another folder of rule documents, in the same layout as `rules/` |
| `--forms-extra DIR` | Another folder of claim forms, in the same layout as `forms/` |

You then get an address such as `http://127.0.0.1:43117/#token=...`, and we try to open it in your browser. If no browser opens, e.g., on WSL, copy the whole address, with the part from `#token=` on, into your browser. Without that part, you can't use the page. The page runs on your computer only, and you stop it with Ctrl+C. In the page, an answer marked "Traveler (fact sheet)" comes from the fact sheet of a practice request, and "Traveler (you)" is your own answer. In the list of practice requests, you find each request by its id, its travel day and the start of its message. The model calls go to the course app, as in the practice runner, and they count against your team's budget.

When you change a file in your system's folder, we run your new code from the next request on. We run `setup(tools)` with your first request, and again after a change to your files, the rule documents, the forms, the train data or the kit, after a setup that failed, or when you click "Rebuild index" at the top of the page. `run_practice.py` and our grading run `setup(tools)` in every run.

## Hand in

You hand in your team folder on Gradescope as one zip file, and one of you uploads it for the whole team, with the other two added as group members.

1. Make the zip file of your team folder, in the kit folder:
   ```bash
   python3 -I -m zipfile -c upload.zip team
   ```
2. Check the zip file:
   ```bash
   python3 -I check_upload.py upload.zip
   ```
3. Upload `upload.zip` on Gradescope. Until the checkpoint deadline, you upload to the assignment "Project 2 checkpoint", and after that deadline, to "Project 2 final".

In step 2, we first check the zip file in the same way, and with the same messages, as on Gradescope. Then we run your system from the unpacked zip file on two practice requests, P001 and P016, with 180 seconds for `setup(tools)` and for each request, as on Gradescope. We run it in the sandbox if you have bubblewrap, and with no model calls, so the check costs nothing. At the end, you get either "Your upload passed both checks" or the problem to fix.

Without model calls, each call of `tools.llm` returns `{"error": "..."}`. If your system reads `resp["choices"]` without checking for `"error"` first, it ends with an error in the check, although it would run on Gradescope. So handle the error in your system, or run the check with `--model`, which makes the model calls through the course app and charges them to your team's budget.

We accept this layout:

```text
upload.zip
  team/
    system.py      your solve(request_text, tools), and setup(tools) if you have it
    helpers.py     your own modules, if you have any, which you import in system.py with import helpers
    words.txt      files that your code reads, if you have any
```

- The file with `solve` has to be named `system.py`, and it has to be at the top of the zip file or in its one top folder, e.g., `team/`. Your own modules go next to it, and you import them with `import helpers`, not with `from . import helpers`.
- You can import only the standard library, your own modules, `p2kit` and the packages in `packages.txt`, and we run your code with Python 3.10.
- Read your own files with a path from the folder of your code, e.g., `Path(__file__).parent / "words.txt"`.
- We run your `setup(tools)` before the first request each time we run your system, on Gradescope too, so you don't upload your cache, and you build your index in `setup`. In the final, we add the documents of the companies that aren't in the kit before we call `setup`.
- Your upload can have at most 200 MB and 10,000 files and folders, with folders at most 64 deep, when we unpack it. Leave out the train data, your venv and the kit's own files. We skip the folders `venv`, `.venv`, `__pycache__` and `.git`, and every symbolic link.

When you upload to Gradescope, we run the same upload check, and then your system on ten hidden requests (see the handout). On Gradescope, we start no new request after about 33 minutes, and the run ends by about 39 minutes, within Gradescope's limit of 40 minutes. If your system is slow, we mark the requests that we didn't start as not run, with 0 points, and add a note with the reason. For the checkpoint and the final, we grade your team's active upload on Gradescope at the deadline, and Gradescope makes your last upload the active one.
