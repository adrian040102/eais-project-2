"""Checks that the runner stops a system at the tool budget, even when the system catches exceptions (no model calls)."""


def solve(request_text, tools):
    for _ in range(25):
        try:
            tools.ask_traveler("travel_date")
        except Exception:
            pass
