"""Your team's system. We call setup(tools) once per run, before the first request, and solve(request_text, tools) once
per request. You find the tools and the limits in TOOLS.md, and the parts that you build in README.md.

You write the code of each function below. The two functions that setup() and solve() call do nothing yet, so the file
runs as it is and files no claim. The other two raise NotImplementedError until you write them.
"""


def setup(tools):
    # We call setup(tools) once per run, before the first request, in a process of its own.
    build_rule_index(tools)


def solve(request_text, tools):
    # We call solve(request_text, tools) once per request, in a new process. You file claims with tools.file_claim,
    # and when nothing is owed, you call tools.no_claim.
    return run_agent(request_text, tools)


def build_rule_index(tools):
    # Your search over the rule documents: you build it here, in setup(), and save it in tools.cache_dir.
    pass


def tool_definitions():
    # How you describe each kit tool, and your own rule search, to the model.
    raise NotImplementedError


def run_agent(request_text, tools):
    # Your agent loop: the message history, and the tool calls that the model asks for.
    return None


def should_stop(messages, tools):
    # Your stopping rule: when you end the loop and return from solve().
    raise NotImplementedError
