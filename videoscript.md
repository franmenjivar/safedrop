# Solution video — script

**Target: 4:45. Hard ceiling 5:00.**
Covers everything the brief asks for: the problem, the baseline, one realistic
execution end to end, the final comparison, the changelog, the change that
contributed most, and one experiment I removed.

Word counts assume ~150 wpm. Timings are cumulative.

---

## Before you record

```bash
# one terminal, ready but not run
python evaluation/run_all.py --mode offline

# console running, browser on http://127.0.0.1:8000
uvicorn app.main:app
```

Have these open in tabs, in this order:

1. `http://127.0.0.1:8000` — console, **Scenario 09 already selected, not started**
2. `http://127.0.0.1:8000/about` — scrolled to the results table
3. `results/analysis.md`
4. `trajectories/readable/SCENARIO-18.md`
5. `CHANGELOG.md`

Zoom the browser to ~110%. Close every other tab.

---

## 0:00 – 0:35 · The problem

> **[Screen: `/about` hero, the aircraft drifting past `SAFE GROUND`]**

"A delivery drone loses power mid-flight over Austin. It already knows how to
handle this — Wing publishes designated landing zones, Zipline flies a
parachute, the FAA specifies lost-link procedures. Modern delivery drones handle
emergencies well.

The problem is narrower than that. The recovery site was chosen when the mission
was planned. Since then a van parked on it, or a school let out, or a crew put
cones down. None of that is in the map.

The person who has to sort this out is a remote pilot supervising a fleet. When
the alarm fires they're not flying anything — they're assembling an answer
against the clock from four tools that don't talk to each other. This aircraft
has under two minutes of endurance."

---

## 0:35 – 1:05 · The baseline

> **[Screen: terminal]**

```bash
python evaluation/run_all.py --baseline-only
```

"First I built the thing they'd realistically have. Not a straw man — it carries
predefined thresholds, deterministic reachability, a preconfigured recovery
list, nearest-feasible selection, and static-map geofencing and obstacle
avoidance.

It gets **93% of the branch decisions right**. Continue, return, divert, land —
that part is basically solved.

It scores **33%** overall, because on every forced landing it picks the nearest
preplanned site without checking whether anything is standing on it right now."

> **[Point at `SCENARIO-17: LAND_IMMEDIATELY safe=False`]**

"This one it lands in a dog park."

---

## 1:05 – 2:35 · One realistic execution

> **[Screen: console, Scenario 09. Click **Run to decision**.]**

"Scenario 9. Battery critical, motor degraded, eight metres a second of wind.
Everything here is real geography — the sites come from OpenStreetMap and each
one is verified to sit on genuinely open ground."

> **[Telemetry panel fills, envelope draws on the map]**

"Raw telemetry never reaches a model. A deterministic preprocessor compresses it
into labelled state, and a deterministic engine computes the reachable envelope
— which isn't a circle, because ground speed depends on heading.

**Agent 1** calls the feasibility tools. Destination, base, alternate hub — all
unreachable. Land immediately.

**Agent 2** takes the three reachable sites and does the thing a map cannot."

> **[Scroll to candidate cards]**

"Hospital car park — three vehicles in current imagery, rejected. Pocket park —
eight people on the lawn, rejected. Walnut Creek Greenbelt — ground feed and
imagery agree, ninety-three percent clear. Valid.

And it can't overrule that. The counts go into a deterministic constraint
checker whose verdict the agent cannot argue with.

**Agent 3** now asks whether we can actually get there."

> **[Point at the two routes on the map — red dashed, green solid]**

"Direct route: **eleven point three metres** predicted separation against
DRONE-207. Thirty required. Fail.

Here's the decision that matters. The site is still good — it's the *path*
that's wrong. So the agent requests a replan rather than abandoning the site.
Second route clears at **a hundred and forty-five metres**.

Then a deterministic gateway re-reads every stored result. Seven checks. A check
that never ran is not a pass."

> **[Scroll to briefing and the approve/reject buttons]**

"And it stops. The briefing is templated from the verified evidence, not written
by a model, so no sentence can outrun what was actually checked. A human
approves."

> **[Click APPROVE — the flight animates]**

"That track is what the *pilot* would fly. SafeDrop sends nothing to the
aircraft. That capability doesn't exist in the code."

---

## 2:35 – 3:20 · The comparison

> **[Screen: `/about` results table]**

"Fifteen scenarios, five failure families, ground truth written before any agent
ran. Both systems scored by the same evaluator, independently of either one's
own gateway — so neither can score well by refusing to check anything.

Thirty-three percent to a hundred. Ground hazard avoidance zero to a hundred.
Airspace conflicts zero to a hundred."

> **[Screen: `results/analysis.md`]**

"And immediately — fifteen out of fifteen is **not** a hundred percent claim.
It's a ninety-five percent interval of eighty to a hundred. Run it four times,
sixty out of sixty, and that tightens to ninety-four.

The analysis also says what didn't improve. Both systems pick the right branch.
Almost the entire gain is in *which site* and *whether the route was verified*."

---

## 3:20 – 4:05 · The changelog, and what mattered most

> **[Screen: `CHANGELOG.md`]**

"Six iterations, each measured with the same harness. But the change that
contributed most isn't an iteration — it's the one I nearly didn't test."

> **[Screen: the Experiment A row]**

"The whole architecture rests on the claim that agents should reason over
verified operational state, not raw telemetry. I'd asserted that everywhere and
never measured it. So I ran the same fifteen scenarios feeding Agent 1 raw CSV
rows instead.

**A hundred percent down to seventy-three.**

But not the way I expected. It didn't get the physics wrong — it *over-escalated*
on four scenarios, and the cause was a blank column."

> **[Screen: the quoted rationale]**

"*'The power_draw_w telemetry channel is missing… a definitive contingency action
cannot be determined.'*

The model couldn't tell *missing and irrelevant* from *missing and
disqualifying*. It never became unsafe — false-safe rate stayed at zero. It
became useless. And *cheaper*, because it gave up before doing the work.

The preprocessor's real value isn't reading trends better. It's deciding, once
and deterministically, which absent evidence actually matters."

---

## 4:05 – 4:30 · What I removed, and the failure mode

"One I removed: a pasture with cattle, to exercise animal detection. It went
when the fixtures moved to real OpenStreetMap geometry — unnamed farmland isn't
findable, and I wasn't going to ship a synthetic site next to verified ones.

Then reverse-geocoding found that Scenario 17's largest, flattest, best-by-
geometry site is a real **off-leash dog park**. Real geography handed me a better
version of the hazard I'd invented."

> **[Screen: `trajectories/readable/SCENARIO-18.md`]**

"And the main failure mode. Six defects, none caught by a failing test, three
sitting behind a hundred percent score — a deterministic backstop quietly
producing the right answer while the agent contributed nothing.

**A system that reports only outcomes will report good outcomes right up until
the backstop is the only thing still working.** Read the trajectories even when
the tests are green."

---

## 4:30 – 4:45 · Close

> **[Screen: `SCENARIO-18.md`, the zero-tool-call escalation]**

"Last thing. This scenario: heading sources disagree by thirty-four degrees.
Energy fine, nothing failing, every threshold inside limits. The rule-based
version answers *continue*, confidently, using distances from a position it's
been told not to trust.

The agent made **zero feasibility tool calls** and escalated — because the tools
would have been fed a position it couldn't rely on.

That's the one scenario rules can't solve, and it's the clearest thing I can
show you about what the agents are actually for."

---

## Delivery notes

- **Don't read the metrics table aloud row by row.** Say three numbers, let the
  rest sit on screen.
- The **11.3 m → 145.1 m** replan and the **100% → 73%** experiment are the two
  moments to slow down for. Everything else can move.
- Say "simulation only" once, at the approve click, and mean it. Repeating it
  sounds defensive.
- If you overrun, cut the *removed experiment* paragraph (0:20) before cutting
  anything in the execution walkthrough. The walkthrough is what the brief asks
  for.
- Record the console section in one take if you can. A live replan lands harder
  than a cut.

## If you only have 3 minutes

Keep: problem (0:30) → baseline number (0:20) → full Scenario 09 execution
(1:20) → comparison with the interval caveat (0:30) → Experiment A (0:30).
Cut: the removed experiment, the trajectory close, the failure-mode section.
