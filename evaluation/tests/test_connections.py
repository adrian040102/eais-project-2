"""Check connection selection against recorded departures and the course rulings."""

from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("connection_system", PROJECT / "team/system.py")
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)


def train(name, departure, recorded_departure, arrival, recorded_arrival, **flags):
    return {"train": name, "train_id": name, "dep_planned": departure, "dep_recorded": recorded_departure,
            "arr_planned": arrival, "arr_recorded": recorded_arrival,
            "dep_cancelled": False, "arr_cancelled": False, **flags}


class Timetable:
    def __init__(self, incoming, onward, budget=30, truncated=False):
        self.incoming, self.onward = incoming, onward
        self.left, self.calls, self.truncated = budget, [], truncated

    def budget(self):
        return {"tool_calls_left": self.left, "model_calls_left": 15}

    def find_trains(self, origin, destination, date, from_time, to_time):
        self.left -= 1
        self.calls.append((origin, destination, date, from_time, to_time))
        start = datetime.fromisoformat(date + " " + from_time)
        end = datetime.fromisoformat(date + " " + to_time)
        if end < start:
            end += timedelta(days=1)
        rows = self.incoming if origin == "A" else self.onward
        result = {"origin": origin, "destination": destination,
                  "trains": [row for row in rows if start <= datetime.fromisoformat(row["dep_planned"]) <= end]}
        if self.truncated and from_time == "00:00":
            result["truncated"] = "too many trains"
        return result


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.incoming = train("incoming", "2026-10-20 10:00", "2026-10-20 10:50",
                              "2026-10-20 10:50", "2026-10-20 11:40")
        self.planned = train("missed", "2026-10-20 11:00", "2026-10-20 11:10",
                             "2026-10-20 12:00", "2026-10-20 12:10")
        self.later_recorded = train("early-plan-late-departure", "2026-10-20 11:30", "2026-10-20 12:20",
                                    "2026-10-20 12:30", "2026-10-20 13:10")
        self.first = train("replacement", "2026-10-20 12:00", "2026-10-20 12:00",
                           "2026-10-20 12:50", "2026-10-20 12:50")

    def resolve(self, tools):
        return system.resolve_connection(tools, "A", "Lyon Part Dieu", "B", "2026-10-20", "10:00", "11:00")

    def test_recorded_departure_order_and_cancelled_trains(self):
        too_early = train("too-early", "2026-10-20 11:45", "2026-10-20 11:49",
                          "2026-10-20 12:20", "2026-10-20 12:20")
        cancelled = train("cancelled", "2026-10-20 11:55", "2026-10-20 11:55",
                          "2026-10-20 12:30", "2026-10-20 12:30", arr_cancelled=True)
        tools = Timetable([self.incoming], [self.planned, self.later_recorded, self.first, too_early, cancelled])
        result = self.resolve(tools)
        self.assertEqual(result["replacement"]["train_id"], "replacement")
        self.assertEqual(result["planned_arrival"], "2026-10-20 12:00")
        self.assertEqual(result["recorded_arrival"], "2026-10-20 12:50")
        self.assertEqual(result["minutes"], 50)
        self.assertEqual(result["change_minutes"], 10)

    def test_exact_minimum_change_time_is_eligible(self):
        boundary = train("boundary", "2026-10-20 11:50", "2026-10-20 11:50",
                         "2026-10-20 12:40", "2026-10-20 12:40")
        result = self.resolve(Timetable([self.incoming], [self.planned, boundary, self.first]))
        self.assertEqual(result["replacement"]["train_id"], "boundary")

    def test_ambiguous_incoming_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.resolve(Timetable([self.incoming, dict(self.incoming, train_id="other")], [self.planned]))

    def test_truncated_results_are_not_treated_as_complete(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.resolve(Timetable([self.incoming], [self.planned, self.first], truncated=True))

    def test_missing_first_replacement_arrival_does_not_select_a_later_train(self):
        missing = dict(self.first, arr_recorded=None)
        with self.assertRaisesRegex(ValueError, "no recorded arrival"):
            self.resolve(Timetable([self.incoming], [self.planned, missing, self.later_recorded]))

    def test_reserves_calls_for_remaining_work(self):
        tools = Timetable([self.incoming], [self.planned], budget=5)
        with self.assertRaisesRegex(ValueError, "Not enough calls"):
            self.resolve(tools)
        self.assertEqual(tools.left, 4)
        self.assertEqual(len(tools.calls), 1)

    def test_next_day_candidates_compete_with_late_previous_day_trains(self):
        incoming = train("incoming", "2026-10-20 23:00", "2026-10-20 23:40",
                         "2026-10-20 23:20", "2026-10-21 00:10")
        planned = train("missed", "2026-10-20 23:30", "2026-10-20 23:30",
                        "2026-10-21 00:30", "2026-10-21 00:30")
        late = train("late", "2026-10-20 23:50", "2026-10-21 01:00",
                     "2026-10-21 00:50", "2026-10-21 02:00")
        next_day = train("next-day", "2026-10-21 00:30", "2026-10-21 00:30",
                         "2026-10-21 01:20", "2026-10-21 01:20")
        result = system.resolve_connection(Timetable([incoming], [planned, late, next_day]),
                                           "A", "Lyon Part Dieu", "B", "2026-10-20", "23:00", "23:30")
        self.assertEqual(result["replacement"]["train_id"], "next-day")
        self.assertEqual(result["minutes"], 50)

    def test_real_train_store_connection_without_model_or_answer_labels(self):
        sys.path.insert(0, str(PROJECT / "eais-p2-kit"))
        from p2kit.data import TrainData

        data = TrainData(PROJECT / "eais-p2-kit/data/trains.sqlite")
        self.addCleanup(data.db.close)

        class DataTools:
            left = 20

            def budget(self):
                return {"tool_calls_left": self.left}

            def find_trains(self, *args):
                self.left -= 1
                return data.find_trains(*args)

        tools = DataTools()
        result = system.resolve_connection(tools, "Aix-en-Provence TGV", "Lyon Part-Dieu",
                                           "Montpellier Sud de France", "2026-10-21", "12:26", "14:10")
        self.assertEqual(result["incoming"]["train"], "TGV INOUI 9866")
        self.assertEqual(result["replacement"]["train"], "TGV INOUI 9879")
        self.assertEqual(result["planned_arrival"], "2026-10-21 15:50")
        self.assertEqual(result["recorded_arrival"], "2026-10-21 19:33:00")
        self.assertEqual(result["minutes"], 223)
        self.assertEqual(tools.left, 12)


if __name__ == "__main__":
    unittest.main()
