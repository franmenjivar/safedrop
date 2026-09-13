# Reproduction guide

Written for someone starting from a clean machine. Every command below was run
end to end on the versions pinned at the bottom.

---

## 0. The fastest path: Docker

Everything below can be run without installing Python. One image serves every
entry point, so the benchmark runs on the same image the console runs on.

```bash
git clone <repository>
cd safedrop

cp .env.example .env          # add GOOGLE_API_KEY for the agent runs
docker compose build          # ~90 s
```

| Command | What it does | Key needed? |
|---|---|---|
| `docker compose run --rm test` | Full test suite | no |
| `docker compose run --rm baseline` | Deterministic baseline | no |
| `SAFEDROP_AGENTS=scripted docker compose up console` | Console at :8000, rule-based stand-ins | no |
| `docker compose up console` | Console at :8000, live agents | yes |
| `docker compose run --rm benchmark` | **The main result** — baseline vs SafeDrop | yes |
| `docker compose run --rm variance` | 4 repeats, run-to-run spread | yes |
| `docker compose run --rm raw` | The state-compression experiment | yes |
| `docker compose run --rm trajectories` | Render readable agent trajectories | no |
| `docker compose run --rm fixtures` | Rebuild scenarios from the cached geometry | no |

`results/` and `trajectories/` are bind-mounted, so everything the container
writes appears on your machine.

**If the console starts but the browser cannot reach it**, the port is not
published. `EXPOSE` in a Dockerfile only declares a port; it does not map it to
your machine. A container in that state still reports `healthy`, because the
healthcheck runs inside it.

```bash
docker ps        # ports: 8000/tcp              -> declared only, unreachable
                 # ports: 0.0.0.0:8000->8000/tcp -> published, reachable
```

Use `docker compose up console`, or with plain Docker add `-p 8000:8000`.
`docker compose run` ignores the `ports:` section — only `up` publishes them.

The image runs as an unprivileged user, carries no credentials — `.env` is
passed at run time and excluded from the build context — and the offline
benchmark reaches no network service except the model. You can prove that:

```bash
docker run --rm --network none safedrop:latest pytest -q
docker run --rm --network none safedrop:latest python evaluation/run_all.py --baseline-only
```

Both pass with networking switched off entirely.

Image: `python:3.11-slim`, ~695 MB, no build toolchain required — Shapely,
NumPy and pandas all install from manylinux wheels.

---

## 1. What you need (running it directly, without Docker)

| | |
|---|---|
| Python | 3.11 or newer |
| Network | Only for the LLM calls. The benchmark data is entirely local. |
| API key | One Google AI Studio key — free tier is sufficient. https://aistudio.google.com/apikey |
| Disk | ~40 MB including the geometry cache and generated imagery |

**No key?** The full pipeline still runs — see §5. You get the deterministic
baseline, the whole test suite, and the console driven by rule-based stand-ins.

---

## 2. Setup

```bash
git clone <repository>
cd safedrop

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# open .env and set GOOGLE_API_KEY=...
```

`.env` is gitignored. No credential appears anywhere in the repository.

---

## 3. Verify the install (no key, no network, ~1 second)

```bash
pytest -q
```

Expected: **9 passed**. These drive the entire workflow with scripted agent
models — orchestration, deterministic tools, reconciliation, the safety
gateway, and the operator briefing — plus two data-integrity tests that assert
every landing anchor sits inside real, verified open ground.

---

## 4. The main result

```bash
python evaluation/run_all.py --mode offline
```

**Runtime:** ~6 minutes (baseline is instant; the 15 agent runs dominate).
**Cost:** ~$0.27 at the rates in §7.

It prints a per-scenario pass/fail list for both systems, then the comparison
table, then the analysis. It writes:

| File | What it is |
|---|---|
| `results/baseline.json` | Per-scenario scores for the deterministic baseline |
| `results/safedrop.json` | Per-scenario scores for the agentic system |
| `results/comparison.md` | The headline table |
| `results/analysis.md` | Confidence intervals, per-family coverage, threats to validity |
| `trajectories/*.jsonl` | One machine-readable trajectory per run |

**Expected output** (agent runs vary slightly; the outcome metrics have been
stable across 4 repeats):

```
Metric                                Baseline    SafeDrop
Safe Contingency Resolution Rate           33%        100%
Contingency Action Accuracy                93%        100%
Ground Hazard Avoidance Rate               50%        100%
Airspace Conflict Avoidance Rate            0%        100%
Replanning Success Rate                     0%        100%
Correct Abstention Rate                     0%        100%
False-Safe Recommendation Rate              0%          0%
```

**Read `results/analysis.md` before quoting any of those numbers.** 15/15 is not
a 100% claim; it is a 95% interval of 80–100%.

---

## 5. Running without an API key

```bash
pytest -q                                          # full workflow, scripted agents
python evaluation/run_all.py --baseline-only       # deterministic baseline, ~2 seconds
SAFEDROP_AGENTS=scripted uvicorn app.main:app      # the console, end to end, no tokens
```

Scripted mode replaces the agents with rule-driven stand-ins
(`app/agents/scripted.py`). They pass 14 of 15 scenarios and **fail
`SCENARIO-18`** — that failure is the point, and it is described in the README.
Runs made this way are labelled in the console and must never be reported as
agent performance.

---

## 6. Everything else

```bash
# Repeat the benchmark and report run-to-run spread   (~25 min, ~$1.10)
python evaluation/run_variance.py --runs 4

# The state-compression experiment: raw telemetry vs compressed state (~6 min, ~$0.30)
python evaluation/run_all.py --mode offline --raw-telemetry

# Readable trajectories, one per scenario  (instant, no key)
python -m scripts.export_trajectories --best

# Rebuild every scenario dataset from the cached real geometry  (~30 s, no key)
python -m scripts.build_fixtures && python -m scripts.scenarios_families

# Refresh the geometry cache from OpenStreetMap  (~15 min; rarely needed)
python -m scripts.fetch_real_geometry && python -m scripts.verify_anchors

# The console
uvicorn app.main:app --reload
#   http://127.0.0.1:8000        operations console
#   http://127.0.0.1:8000/about  problem, architecture, evidence
```

---

## 7. Runtime and cost, measured

Measured on the runs in `results/`, on an Apple Silicon laptop with a
residential connection. Model time dominates; nothing else is close.

| Command | Wall clock | Model calls | Tokens | Cost |
|---|---|---|---|---|
| `pytest -q` | ~1 s | 0 | 0 | $0.00 |
| `run_all.py --baseline-only` | ~2 s | 0 | 0 | $0.00 |
| `run_all.py --mode offline` | ~6 min | 15 scenarios | 524k in / 46k out | **$0.27** |
| `run_variance.py --runs 4` | ~25 min | 60 scenarios | 2.12M in / 185k out | **$1.10** |
| One scenario, interactively | ~22 s | 1 scenario | ~38k | **$0.018** |

Cost uses published Gemini 2.5 Flash rates of **$0.30 / 1M input** and
**$2.50 / 1M output** tokens. Rates change — verify against current pricing
before quoting these figures. Token counts are measured, not estimated: they
come from `usage()` on each run and are recorded in the trajectories.

The free AI Studio tier covers a full benchmark run comfortably. Rate limiting
is the practical constraint on `--runs 4`, not cost; lower `--concurrency` if
you hit it.

---

## 8. Versions

| Package | Version |
|---|---|
| Python | 3.11.14 |
| pydantic-ai-slim | 2.36.0 |
| pydantic | 2.13.5 |
| google-genai | 2.20.0 |
| starlette | 1.6.0 |
| uvicorn | 0.52.4 |
| shapely | 2.1.2 |
| pandas | 3.0.5 |
| numpy | 2.4.6 |
| httpx | 0.28.1 |
| jinja2 | 3.1.6 |
| matplotlib | 3.11.1 |
| pyyaml | 6.0.3 |
| python-dotenv | 1.2.3 |
| pytest | 9.1.1 |

Model: `google:gemini-2.5-flash`, temperature 0. Override with
`SAFEDROP_MODEL` or `config/default.yaml`.

---

## 9. What the benchmark does and does not touch

**Offline mode reaches no third-party service except the LLM.** Telemetry,
geometry, ground state, traffic and imagery are all files in `data/`. The
geometry cache under `data/geo_cache/` was built from OpenStreetMap once and
committed, so the benchmark cannot break because Nominatim is down.

Live context clients exist for City of Austin ArcGIS, OSM Overpass and
Open-Meteo, behind the same interfaces. They are **off** in offline mode and
supply supplemental evidence only in `hybrid` / `live`. No result in this
repository depends on them.

The console's map loads basemap tiles from CARTO / OpenStreetMap and the
candidate cards link out to Google Maps. Both need network; neither affects
any measured result.
