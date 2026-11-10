"""The traveler: answers one topic at a time from the request's fact sheet, up to a question limit.

The topics are those in starter/topics.json. For a topic that the fact sheet doesn't cover, the traveler answers "I don't
know." A topic that isn't on the list, or isn't text, is an error, which doesn't count as a question. Every other
question counts, also a repeated one; after the limit the traveler answers no more questions.
"""
import json
from pathlib import Path

from .data import ToolError

TOPICS_FILE = Path(__file__).resolve().parent.parent / "topics.json"
UNKNOWN = "I don't know."
NO_MORE = "The traveler does not answer any more questions."


def load_topics(path=TOPICS_FILE):
    """{topic: meaning} from topics.json."""
    return {t["topic"]: t["meaning"] for t in json.loads(Path(path).read_text())}


TOPICS = load_topics()


def norm_topic(t):
    return str(t or "").strip().lower().replace(" ", "_").replace("-", "_")


class Traveler:
    def __init__(self, facts, limit, topics=None):
        self.topics = TOPICS if topics is None else topics
        self.facts = {norm_topic(k): v for k, v in (facts or {}).items()}
        self.limit = limit
        self.asked = []          # (topic, answered)
        self.over_limit = 0

    def ask(self, topic):
        t = norm_topic(topic) if isinstance(topic, str) else None
        if t not in self.topics:
            raise ToolError(f"unknown topic {topic!r}: ask about one topic of topics.json ({', '.join(self.topics)}); "
                            "this call doesn't count as a question")
        if len(self.asked) >= self.limit:
            self.over_limit += 1
            self.asked.append((t, False))
            return NO_MORE
        self.asked.append((t, True))
        return self.facts.get(t, UNKNOWN)
