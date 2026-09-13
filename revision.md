# Submission revision — micro1 Agentic Workflows Hackathon

An honest audit of SafeDrop against the brief, the six scoring criteria, the ten
ground rules, and the four deliverables. Written to find gaps, not to award
marks to itself.

**Verdict: the four required deliverables are met and the project is well
positioned. Three gaps were open when this audit started and have been closed
during it; two known weaknesses remain and are stated below rather than hidden.**

---

## 1. The four framing questions

| | Question | Answer |
|---|---|---|
| 01 | **Who has this problem?** | A remote Pilot in Command in a delivery-drone operations centre, supervising a fleet rather than flying an aircraft. Named and described in the README opening, on `/about`, and given a dedicated section — *"What changes for the operator on shift"* — that walks their actual workflow. |
| 02 | **What bottleneck makes it worth solving?** | When a warning fires they are not flying; they are assembling an answer against the clock from tools that do not talk to each other: telemetry, a site list chosen at planning time, camera feeds, a traffic display. The recovery site that was fine at planning time may have a van parked on it now. Seconds of endurance, several disconnected sources. |
| 03 | **Does the agent solve it well?** | 15 scenarios, five failure families, scored against ground truth authored before any agent ran. SCRR **33% → 100%**; repeated 4× for **60/60**. Two scenarios where the correct output is *no answer* are handled correctly. |
| 04 | **Can another person reproduce it?** | `REPRODUCE.md` is written for a clean machine: exact commands, versions, measured runtime and cost, expected output, and a path that needs no API key at all. |

---

## 2. Scoring criteria

### Problem & User Value — 15 pts

**Position: strong.**

The user is specific and the bottleneck is concrete. The framing deliberately
*starts from what already works* — Wing publishes designated landing zones,
Zipline flies a parachute, the FAA specifies lost-link procedures — because a
pitch that opens with "drones can't handle emergencies" is false and a judge
who knows the industry stops listening. The claimed gap is narrow and defensible:
re-checking whether the preplanned site is still safe *this minute*.

The `SCENARIO-26` case strengthens this: a contingency with **no aircraft fault
at all** (the receiving pad closed after launch), which a purely health-driven
failsafe never sees.

*Risk:* the user is described rather than interviewed. No operator was consulted.
Stated as a limitation in `results/analysis.md`.

### Agent Solution & Engineering — 30 pts (the heaviest)

**Position: strong, and this is where the project invests.**

The brief asks which design choices *helped*. Concretely:

| Choice | Why it helped, with evidence |
|---|---|
| **Three specialised agents, not one** | Each answers a genuinely different question and gets a narrow toolset. Agent 1 cannot see imagery; Agent 2 cannot plan routes; Agent 3 cannot re-litigate ground safety. |
| **Deterministic tools for every physical quantity** | There is no tool that lets an agent assert a number. Range, energy, separation and geometry are all computed in code. |
| **Better context: a state preprocessor** | Measured, not assumed — Experiment A shows SCRR drops **100% → 73%** without it, and the failure mode is over-escalation on a missing CSV column. |
| **Verification: a safety gateway** | A separate deterministic pass; every applicable check must be explicitly true, and a check that never ran is not a pass. |
| **Reconciliation** | Agent verdicts are re-checked against the rules. A `VALID` claim the rules reject is demoted. A route counts as verified only if its stored checks passed *and* it leads to a ground-valid candidate. |
| **Orchestration that earns its keep** | `SCENARIO-09`: separation fails at 11.3 m, the agent recognises the *path* is wrong rather than the site, requests a replan, and the second route clears at 145.1 m. |
| **Explicit abstention** | Two scenarios where the right output is nothing. `SCENARIO-18` is the sharpest result in the project. |

**The single best piece of evidence for the agentic layer** is that the
rule-driven stand-ins pass 14 of 15 and fail `SCENARIO-18` — where the agent
made *zero feasibility tool calls* and escalated because disagreeing heading
sources meant the tools would be fed a position it could not trust. A threshold
rule can compare a number to a limit; it cannot express *the numbers I would be
given are wrong*.

### End-to-End Quality — 20 pts

**Position: strong.**

A working operations console, not a notebook: scenario selection, telemetry
replay, live map with reachability envelope and routes, candidate cards with
imagery and evidence, a decision trace, the verification checklist, a templated
operator briefing, and approve/reject. Plus a post-approval flight animation
that is explicitly labelled as depicting what the *pilot* would fly.

The brief warns against output that "reads as clearly AI generated". Guarded by:
the operator briefing being **deterministically templated from verified
evidence** rather than model-written, so no sentence can outrun its evidence;
a bespoke visual identity on both `/` and `/about`; and prose throughout that
states limits rather than performing confidence.

### Measured Improvement — 15 pts

**Position: strong.**

Baseline is a fair floor, not a straw man — it carries static geofencing and
obstacle avoidance, and it already gets 93% action accuracy. Same 15 cases for
both. Scored by the same evaluator against pre-authored ground truth,
**independently of either system's own gateway**, so neither can score well by
declining to check anything.

Exceeds the brief's "ten or more cases" target with 15, and the challenging
cases (`18`, `19`) are called out with what they revealed.

### Reproducibility — 15 pts

**Position: strong.**

`REPRODUCE.md` covers clean-environment setup, exact commands for solution and
baseline and evaluation, required data, expected output, pinned versions, and
measured runtime **and cost** ($0.27 for a full benchmark; $0.018 per scenario).

**A judge does not need Python at all.** `docker compose build` then
`docker compose run --rm benchmark` reproduces the headline result on the same
image the console runs on. Verified: tests and the baseline both pass under
`--network none`, which is direct evidence for the offline claim.

Two things make this unusually solid: the benchmark reaches **no third-party
service except the LLM** — the OpenStreetMap geometry is cached and committed,
so it cannot break because Nominatim is down — and there is a **full no-key
path** (`pytest`, `--baseline-only`, `SAFEDROP_AGENTS=scripted`).

### Hot Take / Insights — 5 pts

**Position: strong, and earned rather than asserted.**

Six defects, none caught by a failing test, three sitting behind a 100% score.
The hot take is sharpened by Experiment A into something more specific than the
usual claim: models cannot distinguish *missing and irrelevant* from *missing
and disqualifying*, and fail toward paralysis. That judgement is deterministic —
make it once, in code, before the agent sees anything.

---

## 3. Ground rules

| # | Rule | Status |
|---|---|---|
| 01 | Build with tools you know | ✅ PydanticAI, Starlette, Shapely, pandas — all standard |
| 02 | Make clear what pre-existed vs what was added | ✅ **Gap closed during this audit** — see §5 |
| 03 | Respect licenses and service terms | ✅ OSM/ODbL attributed; Nominatim rate limit respected (1 req/s, run rarely, results cached); CARTO and Esri attributed; Google Maps used only as key-free link-outs, and its imagery is deliberately **never** passed to a vision model |
| 04 | Consequential actions sandboxed, human approval first | ✅ `flight_command_sent` is `False` on every code path; a test asserts it; the capability to command an aircraft does not exist in the code |
| 05 | Qualified human reviewer in the loop | ✅ Remote Pilot in Command approval is mandatory; the gateway blocks release, not just display |
| 06 | Legal and ethical use case | ✅ Simulation-only decision support; no real aircraft, no personal data |
| 07 | Shareable data | ✅ OpenStreetMap (ODbL), City of Austin open GIS, Open-Meteo, and project-generated imagery. No scraped or proprietary images anywhere |
| 08 | Credentials outside the submission | ✅ `.env` gitignored, `chmod 600`; `.env.example` is a placeholder. *(A key was briefly pasted into `.env.example` during development and moved out immediately — rotate it before submitting.)* |
| 09 | Every claim tied to evidence | ✅ **Gap closed during this audit** — see §5 |
| 10 | Judges can run it and reproduce the main result | ✅ `REPRODUCE.md`, the no-key path, and a Docker image where every entry point is one `docker compose run` |

---

## 4. Deliverables

| # | Required | Status |
|---|---|---|
| 01 | Solution code + agent instructions + README (user, bottleneck, value) + labelled Improvement Changelog + failure mode + hot take | ✅ Prompts live as readable Markdown in `app/prompts/`. `CHANGELOG.md` follows the prescribed Stage / What I tried / Evidence / Decision structure, includes removed experiments, and closes with the failure mode and hot take. |
| 02 | Reproduction guide | ✅ `REPRODUCE.md` — **created during this audit** |
| 03 | Solution video ≤ 5 min | ✅ Script in `videoscript.md` |
| 04 | Representative trajectories for **every** agent, easy to follow, showing tools, feedback, retries, human checkpoints | ✅ **Gap closed during this audit** — `scripts/export_trajectories.py` renders readable Markdown per scenario into `trajectories/readable/` |

---

## 5. Gaps found by this audit, and what was done

**1. A changelog claim with no evidence (ground rule 09).** The raw-telemetry
experiment was described in the changelog as available but had **never been
run**. Fixed by running it. The result was worth having: SCRR **100% → 73%**,
and a failure mode I had not predicted.

**2. Trajectories were not a deliverable, just logs.** 121 raw JSONL files is a
machine record, not something "easy to follow from the agent instructions to the
final result". Fixed with `scripts/export_trajectories.py`, which renders each
run as Markdown: instructions, every tool call and response, the decision it
drove, the gateway checklist, and the human checkpoint.

**3. No reproduction guide, and cost was never computed.** Deliverable 02
explicitly requires runtime *and cost*. Fixed with `REPRODUCE.md`, with cost
computed from measured token counts rather than estimated.

---

## 6. Known weaknesses — stated, not hidden

**The scenarios, the ground truth and the baseline configuration are all mine.**
This is the honest ceiling on the result. The benchmark tests paths I knew
existed and cannot detect a failure mode I never imagined. An independent
scenario author is the single highest-value improvement available, and
`results/analysis.md` says so in its own words.

**Small-sample statistics.** 15/15 is a 95% interval of 80–100%; pooled over four
runs, 60/60 gives 94–100%. The abstention claim rests on **two scenarios repeated
four times**, not eight independent tests — an interval of 68–100%. The analysis
reports intervals rather than point estimates throughout, and the README leads
with that.

**Vision is measured against its own fixture.** Image ground truth is emitted by
the same renderer that draws the image. Fair evidence that the model reads a
synthetic scene; no evidence at all about a real photograph.

**Three failure families have only two scenarios each.** Flagged automatically in
`results/analysis.md` as carrying almost no statistical weight.

---

## 7. Where the points are most at risk

| Criterion | Risk | Mitigation in place |
|---|---|---|
| Agent Solution (30) | A judge reads the deterministic layer as "the agents aren't doing much" | The stand-in comparison answers this directly: rules get 14/15 and fail the one scenario needing judgement. Experiment A quantifies the preprocessor. |
| End-to-End Quality (20) | Console looks impressive but a judge cannot run it | No-key scripted mode runs the full pipeline; `REPRODUCE.md` leads with it |
| Measured Improvement (15) | "100%" reads as overfitting | `results/analysis.md` says so first, with intervals, structural-advantage decomposition, and what did *not* improve |

---

## 8. Bottom line

Meets every minimum. The distinctive strengths are the **measured** architectural
experiment, the **abstention** cases, and a changelog whose most interesting
entries are things that went wrong behind a green metric. The weakest point is
sample size and self-authored ground truth — both stated plainly in the
submission rather than left for a judge to discover.
