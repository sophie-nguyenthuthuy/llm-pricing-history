import copy
import csv
import json
import os
import tempfile
import unittest

import refresh

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class TestPerMtok(unittest.TestCase):
    def test_no_scientific_notation(self):
        self.assertEqual(refresh.per_mtok(8e-07), "0.8")
        self.assertEqual(refresh.per_mtok(2.5e-06), "2.5")
        self.assertEqual(refresh.per_mtok("0.00000018"), "0.18")

    def test_zero_and_none(self):
        self.assertEqual(refresh.per_mtok(0), "0")
        self.assertEqual(refresh.per_mtok(None), "")


class TestNormalize(unittest.TestCase):
    def test_litellm_filters_and_converts(self):
        rows = refresh.normalize_litellm(load("litellm_small.json"))
        models = {r["model"] for r in rows}
        self.assertEqual(models, {"gpt-x", "claude-y"})  # no embedding, no unpriced
        gpt = next(r for r in rows if r["model"] == "gpt-x")
        self.assertEqual(gpt["input_usd_per_mtok"], "2.5")
        self.assertEqual(gpt["output_usd_per_mtok"], "10")
        self.assertEqual(gpt["cache_read_usd_per_mtok"], "1.25")
        self.assertEqual(gpt["context_window"], "128000")
        self.assertEqual(gpt["provider"], "openai")

    def test_openrouter_converts(self):
        rows = refresh.normalize_openrouter(load("openrouter_small.json"))
        self.assertEqual(len(rows), 3)
        claude = next(r for r in rows if r["model"] == "anthropic/claude-y")
        self.assertEqual(claude["provider"], "anthropic")
        self.assertEqual(claude["input_usd_per_mtok"], "0.8")
        self.assertEqual(claude["cache_read_usd_per_mtok"], "0.08")
        free = next(r for r in rows if r["model"].endswith(":free"))
        self.assertEqual(free["input_usd_per_mtok"], "0")


class TestDiff(unittest.TestCase):
    def snapshot(self):
        return refresh.normalize_litellm(load("litellm_small.json"))

    def test_no_changes(self):
        self.assertEqual(refresh.diff(self.snapshot(), self.snapshot(), "2026-08-09"), [])

    def test_price_change_is_field_level(self):
        new = copy.deepcopy(self.snapshot())
        row = next(r for r in new if r["model"] == "gpt-x")
        row["input_usd_per_mtok"] = "2"
        events = refresh.diff(self.snapshot(), new, "2026-08-09")
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["event"], "changed")
        self.assertEqual(e["field"], "input_usd_per_mtok")
        self.assertEqual((e["old"], e["new"]), ("2.5", "2"))

    def test_added_and_removed(self):
        old = self.snapshot()
        new = [r for r in old if r["model"] != "gpt-x"]
        new.append({**old[0], "model": "brand-new",
                    "input_usd_per_mtok": "1", "output_usd_per_mtok": "3"})
        events = refresh.diff(old, new, "2026-08-09")
        kinds = {e["event"] for e in events}
        self.assertEqual(kinds, {"added", "removed"})
        added = next(e for e in events if e["event"] == "added")
        self.assertEqual(added["new"], "1/3")


class TestEndToEnd(unittest.TestCase):
    def test_baseline_then_change(self):
        litellm = load("litellm_small.json")
        openrouter = load("openrouter_small.json")
        with tempfile.TemporaryDirectory() as d:
            n, ev = refresh.run(d, litellm, openrouter, "2026-08-09")
            self.assertEqual(n, 5)
            self.assertEqual(ev, 0)  # baseline run: no events
            with open(os.path.join(d, "history.csv")) as f:
                self.assertEqual(len(list(csv.DictReader(f))), 0)

            changed = copy.deepcopy(litellm)
            changed["gpt-x"]["input_cost_per_token"] = 2e-06
            n, ev = refresh.run(d, changed, openrouter, "2026-08-10")
            self.assertEqual(ev, 1)
            with open(os.path.join(d, "history.csv")) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["date"], "2026-08-10")
            self.assertEqual(rows[0]["new"], "2")

            # snapshot is sorted and stable
            with open(os.path.join(d, "latest.csv")) as f:
                keys = [(r["source"], r["provider"], r["model"])
                        for r in csv.DictReader(f)]
            self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
