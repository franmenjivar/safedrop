# Improvement changelog

How SafeDrop got from a deterministic baseline to the final result, one measured
change at a time. Every row was scored with the same harness on the same
scenarios — `python evaluation/run_all.py --mode offline` — so the rows are
comparable to each other.

Two things to read alongside this: `results/analysis.md`, which puts confidence
intervals on every rate below, and the **What the trajectories caught** section
at the end, because most of these entries came from reading agent logs rather
than from a failing test.

---

## The progression

| Stage | What I tried and why | Evidence | Decision / learning |
|---|---|---|---|
| **Baseline** | A conventional deterministic contingency system, not a straw man: predefined thresholds, deterministic reachability, a preconfigured recovery list, nearest-feasible selection, a direct route, and static-map geofencing and obstacle avoidance. Built first so the comparison had a fair floor. | SCRR **33%** (5/15), action accuracy **93%**, ground hazard avoidance **50%** — `results/baseline.json` | **Kept as the comparison.** It already picks the right *branch* almost every time. What it cannot do is re-check whether anything is standing on the site it picked. That is the whole gap. |
| **Iteration 1** — dynamic ground context | Added Agent 2 with a scenario ground feed and deterministic hard-constraint rules, because the baseline's failure was always the same: right branch, occupied site. | Ground hazard avoidance **50% → 100%** | **Kept.** This is the single largest contributor to the headline number. |
| **Iteration 2** — visual evidence | Added still-image inspection with a vision tool, asked only for observable counts, never "is this safe". Deterministic rules convert counts to reason codes. | Image hazard false-negative rate **0%** across 60 executions; count accuracy 81–88% | **Kept, with a caveat.** Count accuracy is the only metric that moves run to run. It is honest to quote the spread, not the best run. |
| **Iteration 3** — traffic verification and replanning | Added Agent 3 and the six route checks. The interesting part is not routing — it is teaching the agent that a separation failure means *replan the path* while an energy failure means *abandon the site*. | Airspace conflict avoidance **0% → 100%**; replanning success **100%**. `SCENARIO-09`: direct route 11.3 m against `DRONE-207`, replanned route 145.1 m | **Kept.** This is the clearest demonstration of orchestration doing something a single call would not. |
| **Iteration 4** — off-nominal generation | Added deterministic candidate generation from real geospatial context for the case where nothing preplanned is reachable, with hard exclusion buffers around schools, hospitals and playgrounds. | `SCENARIO-16`: baseline has no answer at all; SafeDrop recommends a site as `NON_CERTIFIED` after excluding two candidates that sit 39 m and 21 m from real mapped playgrounds | **Kept.** Degrades usefully instead of having nothing to offer. |
| **Iteration 5** — abstention | Widened the benchmark from 6 scenarios to 15 across five failure families, including two where the correct output is **no recommendation at all**. The original six were almost entirely battery-related and nothing tested refusal. | Correct abstention **0% → 100%** (2/2). `SCENARIO-18` is failed by the rule-based stand-ins and passed by the agents | **Kept, and it changed my mind about what to measure.** A system tuned to always answer scores well everywhere except here. |
| **Experiment A** — raw telemetry vs compressed state | The project's central architectural claim was that agents should reason over verified operational state, not raw physical telemetry. It was asserted, never measured. So I ran the same 15 scenarios with Agent 1 receiving raw CSV rows instead. | SCRR **100% → 73%**, action accuracy **100% → 73%**, tool calls 15.1 → 8.4 — `results/comparison-raw-telemetry.md` | **Preprocessor kept — and for a reason I had not predicted.** See below. |
| **Final** | Everything above, `google:gemini-2.5-flash`, temperature 0. | SCRR **100%** (15/15), false-safe **0%**, ~22 s and ~$0.018 per scenario. Repeated 4×: **60/60**, 95% interval **94–100%** — `results/variance.md` | The gain is almost entirely in *site selection and route verification*. Both systems pick the right branch. Saying so is part of the result. |

---

## Experiment A in detail: what raw telemetry actually broke

I expected the compressed state to help with trend reading. That is not what
happened. Four scenarios regressed — `16`, `17`, `24`, `25` — and all four
failed the same way: the agent **over-escalated**.

The cause was not reasoning about physics. It was a blank CSV column. Scenarios
that do not carry an optional channel leave `power_draw_w` empty, and given the
raw rows the agent could not tell "this channel is absent and does not matter"
from "I lack the information I need":

> *"The power_draw_w telemetry channel is missing from the provided data… Without
> this data, a definitive contingency action cannot be determined."*
> — Agent 1 on `SCENARIO-17`, raw mode, returning `ESCALATE`

The preprocessor makes that judgement once, deterministically, and correctly: an
absent channel becomes `power_state: UNKNOWN` and the energy engine carries on,
because it never needed measured draw.

Two things worth noting. **It never became unsafe** — false-safe rate stayed at
0% and hazard avoidance stayed at 100%. Raw telemetry made the agent useless,
not dangerous, which is the failure direction you want but still a failure.
And it got *cheaper* while getting worse: 8.4 tool calls instead of 15.1,
because it gave up before doing the work.

**The lesson is narrower and more useful than the one I set out to prove.** The
preprocessor's value is not that it reads trends better. It is that it decides,
once and deterministically, which absent evidence actually matters — so the
agent never has to.

---

## What I removed

| Removed | Why it was there | What happened |
|---|---|---|
| **The livestock / agricultural-field scenario** | An `ANIMAL_EXPOSURE` case: a pasture with four cattle, to prove the vision path catches non-human hazards. | Dropped when the fixtures moved to real OpenStreetMap geometry — unnamed farmland is not findable through Nominatim, and I would not ship a synthetic site next to verified ones. **Then it came back better:** reverse-geocoding revealed that Scenario 17's largest, flattest site is a real off-leash **dog park**. Real geography supplied a hazard I had invented a worse version of. |
| **The published-artifact landing page** | The explainer was first built as a hosted artifact. | Removed and rebuilt as a route in the app itself (`/about`). A judge should not have to leave the project to read what it does. |
| **Saturated twin-neon visual direction** | First pass at the explainer used cyan + magenta on near-black. | Removed. It read as generic "AI product page". Rebuilt around a single accent and volumetric haze. |

---

## What the trajectories caught that the metrics did not

Six defects. **Not one was caught by a failing test**, and in most cases every
top-line metric read 100% while the thing was broken.

| Defect | How it was found | Why no test caught it |
|---|---|---|
| **Parallel tool calls beat the prompt.** Gemini issued a candidate's four evidence tools at once, so the constraint checker ran before imagery arrived and returned `UNVERIFIED` for everything — including a car park with five vehicles. | Reading a trajectory | Reconciliation silently carried the entire result. The final answer was right; the agent's contribution was zero. |
| **A false-safe hiding one position away.** Vision reported *0 animals* at 0.9 confidence on a pasture with four cattle. With no ground feed that made a hazardous site `VALID`. | Reading a trajectory | The right site was still chosen — because it happened to be tried first. Luck, not safety. Last-resort sites now need two agreeing sources. |
| **Coordinates that were never real.** Sites placed by bearing and distance from an arbitrary aircraft position; one "recovery yard" sat in front of a house. | A user opened the map | Every internal check was consistent. Synthetic geography is invisible to synthetic tests. |
| **A polygon is not open ground.** OSM park boundaries enclose houses, service roads and playscapes. | Reverse-geocoding each anchor | The inscribed-circle anchor proved a point was deep inside *a polygon*, which is not the same claim. **23 of 90** candidate sites failed the new check and were dropped. |
| **A metric that rewarded the wrong thing.** `correct_abstention_rate` was computed over scenarios that *did* abstain rather than those that *should*. | Re-reading the definition | A system that abstained on everything would have scored 100% on it. |
| **NaN in the snapshot.** New optional telemetry channels arrive from pandas as `NaN`; `NaN` is not valid JSON, so the console's snapshot endpoint 500'd. | Clicking Start | Looked like a dead button. Now covered by a test that JSON-encodes every scenario's payload with `allow_nan=False`. |

---

## Main failure mode

**A safety-critical agent system that reports only outcomes will report good
outcomes right up until the backstop is the only thing still working.**

Three of the six defects above sat behind a 100% score. In each case a
deterministic layer — reconciliation, the safety gateway, candidate ordering —
was quietly producing the right answer while the agentic layer contributed
nothing, or contributed something wrong. The metric could not see the
difference because the metric only looks at the end of the pipeline.

## Hot take

Safety-critical agents should reason over **verified operational state**, not
raw physical telemetry — and Experiment A says the reason is not the one usually
given. It is not that models read trends badly. It is that models cannot tell
*missing and irrelevant* from *missing and disqualifying*, and they fail toward
paralysis when they cannot. Deciding which absent evidence matters is a
deterministic judgement. Make it once, in code, before the agent sees anything.

The corollary I would carry into the next build: **log trajectories from the
first commit and read them even when the tests are green.** They were the only
place most of these failures were visible, and reading them is cheap compared
to shipping a system whose backstop is load-bearing without anyone knowing.
