"""Tiny model probe: one short call per course model through tools.llm, then nothing else."""
import os


def solve(request_text, tools):
    for name in ("OPENAI_API_KEY", "P2_TOKEN"):
        assert name not in os.environ, f"{name} should not be visible to systems"
    for model in ("gpt-4o-mini", "gpt-6-luna"):
        r = tools.llm([{"role": "user", "content": "Reply with the single word: ok"}], model=model, max_tokens=200,
                      **({"temperature": 0} if model == "gpt-4o-mini" else {"reasoning_effort": "low"}))
        print(model, r.get("error") or r["choices"][0]["message"]["content"], r.get("usage"))
    return "probe done"
