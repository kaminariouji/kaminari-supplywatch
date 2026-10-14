# SupplyWatch

**Quote-verified supply-chain change monitoring.** A small autonomous agent that watches supplier pages (price, lead time, stock, MOQ), and raises an alert **only when every number in it can be traced back to an exact quote on the page it came from**.

Built for procurement and factory teams who cannot afford hallucinated numbers in a reorder decision.

---

## The problem

Buyers watch dozens of supplier pages for changes: a price jump, a lead time that quietly doubles, a part that goes out of stock. Manual checking does not scale, and generic "AI scraping" tools report values you cannot audit — if the model misreads `EUR 11.49` as `EUR 1.49`, nobody notices until the PO is wrong.

## The idea

**No quote, no field.** Every extracted value must carry the exact span of page text it came from. A verifier checks that span against the fetched page before the value is allowed into the diff. If the quote cannot be found, the field is dropped and counted — never reported.

```
fetch ──► extract ──► verify ──► diff ──► alert
 │           │           │         │        │
 │           │           │         │        └─ LLM drafts the summary (optional,
 │           │           │         │           any OpenAI-compatible endpoint,
 │           │           │         │           e.g. vLLM on AMD ROCm) with a
 │           │           │         │           deterministic template fallback
 │           │           │         └─ compares against previous state; severity:
 │           │           │            price ±3%/±10%, lead time +20%/+50%,
 │           │           │            stock in→out = critical
 │           │           └─ quote must appear verbatim in the page text
 │           └─ heuristic field detection (price / lead time / stock / MOQ),
 │              each hit carries its quote
 └─ stdlib HTTP fetch, descriptive UA, gzip, charset-aware
```

Two design rules make the output trustworthy:

1. **Evidence-first extraction.** The extractor does not output a value without the text span it came from.
2. **Verification before diffing.** The verifier normalizes whitespace/entities and requires the quote to be a substring of the fetched page. Dropped fields are reported in the state file (`dropped_quotes`), so you can see what the agent refused to trust.

## Quickstart

No dependencies. Python 3.10+.

```bash
cd supplywatch

# 1) baseline scan (first observation: records state, no changes expected)
python -m agent.cli scan --watchlist watchlist.demo.json --out demo/data/state.json

# 2) re-scan later; changes vs. the previous state become alerts
python -m agent.cli scan --watchlist watchlist.demo.json \
    --state demo/data/state.json --out demo/data/state.json

# 3) inspect the last state / export a report
python -m agent.cli show --state demo/data/state.json
python -m agent.cli export --state demo/data/state.json --out demo/data/report.json
```

### Reproducible demo (no network needed)

`watchlist.demo.json` points at two self-hosted mirror pages (`demo/mirror/`) plus one live public page. `watchlist.demo-v2.json` points at a changed version of the same pages (`demo/mirror-v2/`): a valve price moves +19.2%, lead time +200%, stock drops to out-of-stock, and a bearing goes on backorder.

```bash
python -m agent.cli scan --watchlist watchlist.demo.json --out demo/data/baseline.json
python -m agent.cli scan --watchlist watchlist.demo-v2.json \
    --state demo/data/baseline.json --out demo/data/state.json
```

Expected: 5 verified changes, 4 critical, 1 warning — each with the quote that proves it.

### Optional: LLM-drafted alert

The alert text can be drafted by any OpenAI-compatible chat endpoint. Without it, a deterministic template is used (the pipeline never depends on the LLM).

```bash
python -m agent.cli scan --watchlist watchlist.demo.json --state demo/data/state.json \
    --out demo/data/state.json \
    --llm-base-url http://localhost:8000 --llm-model your-model --llm-api-key EMPTY
```

## Run the LLM step on AMD ROCm

The optional drafting step runs on AMD GPUs via vLLM's ROCm images, or on AMD Developer Cloud notebooks.

**AMD Developer Cloud (notebooks.amd.com):** sign in with an AMD AI Developer Program account, start a notebook with a ROCm GPU, and serve a model:

```bash
pip install vllm
vllm serve Qwen/Qwen2.5-7B-Instruct --port 8000
# then pass --llm-base-url https://<your-notebook-url> to the scan command
```

**Own hardware (ROCm container):**

```bash
docker build -f docker/rocm.Dockerfile -t supplywatch-rocm .
docker run --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
  supplywatch-rocm scan --watchlist watchlist.demo.json --out demo/data/state.json
```

The CPU image (`Dockerfile`) runs the full pipeline including the template alert:

```bash
docker build -t supplywatch .
docker run -v "$PWD/demo:/app/demo" supplywatch \
  scan --watchlist watchlist.demo.json --out demo/data/state.json
```

## CLI reference

| Command | What it does |
| --- | --- |
| `scan --watchlist W --out O [--state S] [--llm-*]` | Fetch every item, verify fields, diff against `--state`, write new state to `--out`, print a JSON summary |
| `show --state S` | Print the last scan state (items, changes, alert) |
| `export --state S --out O` | Write a shareable JSON report |

Watchlist format:

```json
{
  "name": "My watchlist",
  "items": [
    {
      "id": "valve-dn50",
      "name": "Stainless valve DN50 (316L)",
      "supplier": "Acme Components",
      "url": "https://supplier.example/valve-dn50",
      "fields": {
        "price": ["price"],
        "lead_time": ["lead time"],
        "stock": ["stock"],
        "moq": ["minimum order"]
      }
    }
  ]
}
```

`fields` maps a field kind to human hints; detection is heuristic and every hit carries a quote. Local paths are supported for mirrors and tests.

## Severity model

| Field | Warning | Critical |
| --- | --- | --- |
| price | ≥ ±3% | ≥ ±10% |
| lead time | ≥ +20% | ≥ +50% |
| stock | out-of-stock from any state | in-stock → out-of-stock / backorder |

First observations are never reported as changes — a change needs two verified points.

## Tests

```bash
python -m unittest discover -s tests -v   # 22 tests, no network required
```

Fixtures under `tests/fixtures/` reproduce a supplier page before/after a price, lead-time and stock change.

## Repository layout

```
agent/
  fetch.py    stdlib HTTP fetch (gzip, charset, descriptive UA; file:// for mirrors)
  extract.py  HTML→text + heuristic field detection, every hit carries a quote
  verify.py   quote verification: field is kept only if its quote is on the page
  watch.py    diff + severity + alert (LLM draft or deterministic template)
  llm.py      optional OpenAI-compatible client for alert drafting
  cli.py      scan / show / export
demo/
  mirror*/    reproducible before/after supplier pages
  data/       baseline + state + report artifacts
tests/        unittest suite + fixtures
Dockerfile            CPU image
docker/rocm.Dockerfile  ROCm image for AMD GPUs
```

## License

MIT
