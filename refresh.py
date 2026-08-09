#!/usr/bin/env python3
"""Refresh the LLM API pricing dataset.

Fetches two machine-readable sources, normalizes them into a snapshot CSV
(data/latest.csv), and appends field-level change events to the append-only
history CSV (data/history.csv). Stdlib only.

Sources:
  litellm     https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
              (first-party provider list prices, maintained by LiteLLM)
  openrouter  https://openrouter.ai/api/v1/models
              (prices as billed through OpenRouter, includes :free variants)

Usage:
  python refresh.py                                  # fetch live, update data/
  python refresh.py --data-dir out/                  # write elsewhere
  python refresh.py --from-files litellm.json openrouter.json   # offline
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"

SNAPSHOT_FIELDS = [
    "source",
    "provider",
    "model",
    "input_usd_per_mtok",
    "output_usd_per_mtok",
    "cache_read_usd_per_mtok",
    "context_window",
]
HISTORY_FIELDS = [
    "date",
    "event",  # added | removed | changed
    "source",
    "provider",
    "model",
    "field",  # empty for added/removed
    "old",
    "new",
]
PRICE_FIELDS = ["input_usd_per_mtok", "output_usd_per_mtok", "cache_read_usd_per_mtok"]

MTOK = Decimal(1_000_000)


def fetch_json(url, retries=3, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "llm-pricing-history"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def per_mtok(per_token):
    """USD-per-token (float or str) -> USD-per-mtok string, no sci notation."""
    if per_token is None:
        return ""
    d = Decimal(str(per_token)) * MTOK
    s = format(d.normalize(), "f")
    return s


def normalize_litellm(raw):
    rows = []
    for model, spec in raw.items():
        if not isinstance(spec, dict) or spec.get("mode") != "chat":
            continue
        inp = spec.get("input_cost_per_token")
        out = spec.get("output_cost_per_token")
        if inp is None and out is None:
            continue
        rows.append(
            {
                "source": "litellm",
                "provider": spec.get("litellm_provider") or "",
                "model": model,
                "input_usd_per_mtok": per_mtok(inp),
                "output_usd_per_mtok": per_mtok(out),
                "cache_read_usd_per_mtok": per_mtok(
                    spec.get("cache_read_input_token_cost")
                ),
                "context_window": str(spec.get("max_input_tokens") or ""),
            }
        )
    return rows


def normalize_openrouter(raw):
    rows = []
    for m in raw.get("data", []):
        pricing = m.get("pricing") or {}
        inp = pricing.get("prompt")
        out = pricing.get("completion")
        if inp is None and out is None:
            continue
        model_id = m.get("id", "")
        provider = model_id.split("/", 1)[0] if "/" in model_id else ""
        rows.append(
            {
                "source": "openrouter",
                "provider": provider,
                "model": model_id,
                "input_usd_per_mtok": per_mtok(inp),
                "output_usd_per_mtok": per_mtok(out),
                "cache_read_usd_per_mtok": per_mtok(pricing.get("input_cache_read")),
                "context_window": str(m.get("context_length") or ""),
            }
        )
    return rows


def key(row):
    return (row["source"], row["provider"], row["model"])


def diff(old_rows, new_rows, date):
    """Field-level change events between two snapshots."""
    old = {key(r): r for r in old_rows}
    new = {key(r): r for r in new_rows}
    events = []
    for k in sorted(new.keys() - old.keys()):
        s, p, m = k
        events.append(
            {"date": date, "event": "added", "source": s, "provider": p,
             "model": m, "field": "", "old": "",
             "new": new[k]["input_usd_per_mtok"] + "/" + new[k]["output_usd_per_mtok"]}
        )
    for k in sorted(old.keys() - new.keys()):
        s, p, m = k
        events.append(
            {"date": date, "event": "removed", "source": s, "provider": p,
             "model": m, "field": "", "old":
             old[k]["input_usd_per_mtok"] + "/" + old[k]["output_usd_per_mtok"],
             "new": ""}
        )
    for k in sorted(old.keys() & new.keys()):
        for f in PRICE_FIELDS:
            if old[k][f] != new[k][f]:
                s, p, m = k
                events.append(
                    {"date": date, "event": "changed", "source": s, "provider": p,
                     "model": m, "field": f, "old": old[k][f], "new": new[k][f]}
                )
    return events


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def append_csv(path, fields, rows):
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def run(data_dir, litellm_raw, openrouter_raw, date):
    snapshot = normalize_litellm(litellm_raw) + normalize_openrouter(openrouter_raw)
    snapshot.sort(key=key)

    latest_path = os.path.join(data_dir, "latest.csv")
    history_path = os.path.join(data_dir, "history.csv")

    previous = read_csv(latest_path)
    if previous:
        events = diff(previous, snapshot, date)
    else:
        # First run is the baseline; a flood of "added" events would be noise.
        events = []

    os.makedirs(data_dir, exist_ok=True)
    write_csv(latest_path, SNAPSHOT_FIELDS, snapshot)
    if not os.path.exists(history_path):
        write_csv(history_path, HISTORY_FIELDS, [])
    if events:
        append_csv(history_path, HISTORY_FIELDS, events)
    return len(snapshot), len(events)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument(
        "--from-files",
        nargs=2,
        metavar=("LITELLM_JSON", "OPENROUTER_JSON"),
        help="read sources from local files instead of the network",
    )
    args = ap.parse_args(argv)

    date = os.environ.get(
        "SNAPSHOT_DATE", datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )

    if args.from_files:
        with open(args.from_files[0], encoding="utf-8") as f:
            litellm_raw = json.load(f)
        with open(args.from_files[1], encoding="utf-8") as f:
            openrouter_raw = json.load(f)
    else:
        litellm_raw = fetch_json(LITELLM_URL)
        openrouter_raw = fetch_json(OPENROUTER_URL)

    n_rows, n_events = run(args.data_dir, litellm_raw, openrouter_raw, date)
    print(f"{date}: snapshot {n_rows} models, {n_events} change events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
