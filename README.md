# llm-pricing-history

[![refresh](https://github.com/sophie-nguyenthuthuy/llm-pricing-history/actions/workflows/refresh.yml/badge.svg)](https://github.com/sophie-nguyenthuthuy/llm-pricing-history/actions/workflows/refresh.yml)

A public, machine-readable **history of LLM API pricing**, refreshed daily by CI.

The product of this repo is two CSV files. A GitHub Action re-fetches the
sources every day, and commits **only when a price actually changed** — so the
git log itself is a clean audit trail of pricing events, and `history.csv`
accumulates a citable record of every price change, model launch, and delisting
across ~2,500 chat models.

## The data

### [`data/latest.csv`](data/latest.csv) — current snapshot

One row per `(source, provider, model)`. All prices in **USD per million tokens**.

| column | meaning |
|---|---|
| `source` | `litellm` (first-party list prices) or `openrouter` (as billed via OpenRouter) |
| `provider` | provider slug (`anthropic`, `openai`, `bedrock`, `meta-llama`, …) |
| `model` | model id as the source names it |
| `input_usd_per_mtok` | input price |
| `output_usd_per_mtok` | output price |
| `cache_read_usd_per_mtok` | cache-read price, when published |
| `context_window` | max input tokens, when published |

### [`data/history.csv`](data/history.csv) — append-only change log

One row per event, written the day CI observes it.

| column | meaning |
|---|---|
| `date` | UTC date the change was observed |
| `event` | `added` \| `removed` \| `changed` |
| `field` | which price field changed (empty for added/removed) |
| `old`, `new` | values; for added/removed, `input/output` price pair |

## Sources

- [LiteLLM `model_prices_and_context_window.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) — community-maintained first-party list prices.
- [OpenRouter `/api/v1/models`](https://openrouter.ai/api/v1/models) — live prices as billed through OpenRouter (includes `:free` variants).

Only `mode: chat` models are tracked. Both sources are kept separately (no
cross-source dedup) because they measure different things: list price vs.
routed price.

## Caveats

- Dates reflect **when CI observed the change**, not the provider's announcement date. Daily granularity.
- LiteLLM data is community-maintained; a lagging update there shows up as a lagging change here.
- History accumulates from **2026-08-09** (baseline); the baseline snapshot itself emits no events.

## Run it yourself

Stdlib-only Python, no dependencies:

```bash
make refresh   # fetch live sources, update data/
make test      # offline unit tests (fixtures, no network)
```

## Citing

See [`CITATION.cff`](CITATION.cff), or:

> Nguyễn Thu Thuỷ. *llm-pricing-history: a daily-refreshed history of LLM API pricing.* GitHub, 2026. https://github.com/sophie-nguyenthuthuy/llm-pricing-history

## License

Code MIT. Data ([`data/`](data/)) [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) —
underlying prices are facts; attribution appreciated.
