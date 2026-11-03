"""The exceptions that end a request or a run. They derive from BaseException, so that a system's
`except Exception` can't swallow them.

This module imports nothing else, because the team's process (p2kit/child.py) needs the classes and nothing more.
"""


class BudgetExceeded(BaseException):
    """The request used its 20 tool calls or its 15 model calls."""


class TimeLimit(BaseException):
    """The request used its time."""


class CostLimit(BaseException):
    """The run's cost cap, or the team's budget, can't cover the next call. The run ends."""


class ModelAccessError(BaseException):
    """Model calls can't work: a setting is missing, or the token or key was refused. The run ends."""


STOPS = {c.__name__: c for c in (BudgetExceeded, TimeLimit, CostLimit, ModelAccessError)}
