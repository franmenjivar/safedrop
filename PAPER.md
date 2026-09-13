# Paper plan — Risk-Ranked Emergency Landing Site Selection Under Partial Observability with Freshness-Decayed Evidence Fusion

Status: plan, not a draft. Target: IEEE conference with Xplore-indexed proceedings (ICUAS, DASC).
Built on the SafeDrop hackathon codebase in this repository.

---

## 1. One-paragraph summary

When a delivery UAV declares a forced landing, the recovery site must be chosen from
evidence that is heterogeneous, *aging*, and *correlated*: a preplanned site list, a
static land-use map, a ground sensor feed of unknown latency, and site imagery of
unknown reliability. Current practice selects the nearest reachable preplanned site and
ignores current ground conditions entirely. This paper proposes a two-layer algorithm:
(1) a per-site occupancy posterior that attenuates each evidence source by its age using
a decay law derived from an occupancy Markov model, and fuses sources conservatively
under unknown correlation; and (2) a selection rule that ranks candidates by the
conditional value at risk of total expected ground harm — transit plus landing — and
abstains when no candidate clears a risk threshold.

---

## 2. Problem

Forced landing in an urban BVLOS delivery operation. At declaration time the aircraft has
seconds to low tens of seconds of usable endurance. The site chosen at mission-planning
time may now be occupied — a parked van, a school letting out, a work crew.

Three properties of the evidence make this hard, and each is ignored by current practice:

1. **Evidence ages.** A ground observation 90 s old is not the same claim as one 5 s old,
   and how fast it decays depends on the site: an industrial lot changes slowly, a
   playground at recess changes fast.
2. **Evidence is correlated.** Site imagery and a ground feed may derive from the same
   camera; a GIS layer and an OSM prior may share provenance. Naive Bayesian fusion
   double-counts them and can manufacture a confident *clear* from one physical sensor.
3. **The estimate is uncertain, and the decision is asymmetric.** Landing on an occupied
   site is not symmetric with abstaining. Minimizing expected harm is the wrong objective.

Observed failure motivating (2) and (3), recorded in `CHANGELOG.md` of this repository:
a vision tool reported *0 animals at 0.9 confidence* on a pasture holding four cattle.
With no independent corroboration, that made a hazardous site VALID.

---

## 3. Contributions

- **C1.** A freshness attenuation law for occupancy evidence *derived* from a two-state
  continuous-time Markov occupancy model, with per-land-use-class mixing rates, replacing
  the binary current/stale threshold used in practice.
- **C2.** Conservative fusion of correlated, aged evidence via Chernoff / covariance-
  intersection weighting over a source-provenance graph, with a proof that the resulting
  posterior is never overconfident for any correlation structure consistent with the
  source marginals.
- **C3.** A risk-averse selection rule minimizing CVaR of joint transit-plus-landing
  ground harm subject to reachability and hard exclusion constraints, with principled
  abstention as a risk threshold rather than a hand rule.
- **C4.** An open, reproducible benchmark and ablation study over the SafeDrop scenario
  harness, including calibration analysis of the vision evidence source.

---

## 4. Method

### 4.1 Notation

At forced-landing declaration: aircraft state $s$; candidate sites $\mathcal{C} = \{c_1,\dots,c_M\}$
emitted by a deterministic generator; evidence sources $k \in \mathcal{K}$, each producing an
observation $z_k$ with age $\tau_k$ and source identity. $H_c \in \{0,1\}$ denotes the site
being unsafe due to ground occupancy; $N_{\text{exp}}(c)$ the number of exposed persons.

### 4.2 Layer 1 — freshness-decayed, correlation-robust occupancy posterior

**Occupancy dynamics.** Model per-site occupancy as a two-state CTMC (clear ⇄ occupied)
with rates $\mu_{01}, \mu_{10}$, stationary occupancy $\pi_1$, total rate $\mu = \mu_{01} + \mu_{10}$:

$$P(H_t = 1 \mid H_{t-\tau}) = \pi_1 + \left(\mathbb{1}[H_{t-\tau}=1] - \pi_1\right) e^{-\mu \tau}$$

**Lemma 1 (attenuation).** The log-likelihood ratio contributed by an observation of age
$\tau$ equals the fresh LLR scaled by a factor decaying monotonically to zero as
$e^{-\mu\tau}$. Freshness decay is therefore a consequence of the occupancy dynamics, not
a modeling choice. $\mu$ is indexed by land-use class.

$$\Lambda_k(c) = \gamma_{\text{class}(c)}(\tau_k)\cdot \lambda_k(z_k), \qquad
\gamma(\tau) = e^{-\mu\tau}, \qquad
\lambda_k(z_k) = \log \frac{P(z_k \mid H=1)}{P(z_k \mid H=0)}$$

**Fusion under unknown correlation.** Combine sources by weighted geometric mean of
likelihoods (Chernoff / covariance intersection):

$$p(\mathcal{E} \mid H) \propto \prod_k p_k(z_k \mid H)^{w_k}, \qquad \sum_k w_k = 1,\ w_k \ge 0$$

Weights are allocated across groups of a source-provenance graph; sources sharing a
modality or upstream feed split a single group's weight. Selecting $w$ to minimize
posterior entropy is a small convex program.

**Proposition 2 (conservativeness).** For any joint correlation structure consistent with
the source marginals, the fused posterior is no more confident than the true posterior.
Correlated sensors cannot manufacture a confident *clear*.

**Proposition 3 (subsumption).** The "two independent agreeing sources required for a
last-resort site" heuristic already shipped in SafeDrop is the special case of uniform
weights with binarized LLRs at a fixed threshold.

**Calibration.** $\lambda_k$ must be a calibrated likelihood ratio, not a raw model
confidence. Fit per-source likelihood ratios on labeled data; report reliability diagrams
and Brier scores; apply temperature scaling or isotonic regression.

### 4.3 Layer 2 — risk-ranked selection

**Ground harm.** Landing harm uses the established UAS ground-risk formulation — impact
kinetic energy, sheltering factor, lethality curve (JARUS SORA and the ground-risk-map
literature) — rather than authored site tiers:

$$R_{\text{land}}(c) = P(\text{impact outside footprint}) \cdot \mathbb{E}[N_{\text{exp}}(c)] \cdot P(\text{fatality} \mid \text{impact})$$

**Transit harm.** The descent corridor overflies people, so site and route are optimized
jointly rather than sequentially:

$$R_{\text{tot}}(c) = \int_{\text{route}(s,c)} \rho(\mathbf{x})\,\kappa(\mathbf{x})\, d\ell \;+\; R_{\text{land}}(c)$$

**Risk-averse selection.** $R_{\text{tot}}(c)$ is a random variable induced by the Layer-1
posterior. Select by conditional value at risk at level $\alpha$ over the feasible set
$\mathcal{F}$ (reachable with energy reserve, hard exclusions satisfied, freshness floor met):

$$c^\star = \arg\min_{c \in \mathcal{F}} \ \mathrm{CVaR}_\alpha\!\left[R_{\text{tot}}(c)\right]$$

**Abstention.** If $\min_{c \in \mathcal{F}} \mathrm{CVaR}_\alpha[R_{\text{tot}}(c)] > R_{\text{accept}}$,
emit ESCALATE. Abstention becomes a threshold on estimated risk, enabling risk–coverage
analysis (AURC) instead of a bare abstention rate.

**Proposition 4 (staleness monotonicity).** $\mathrm{CVaR}_\alpha[R_{\text{tot}}(c)]$ is
non-decreasing in evidence age for every candidate; the system cannot become more
confident by waiting.

### 4.4 Algorithm

```
Input: state s, candidates C, sources K, risk level alpha, threshold R_accept
1  F <- { c in C : hard_constraints(c) and reachable(c, s, reserve) }
2  for c in F:
3      l <- logit p0(class(c), time_of_day)               # land-use prior
4      for k in K(c):
5          Lambda_k <- exp(-mu_class(c) * tau_k) * lambda_k(z_k)
6      w <- argmin_w  H[posterior]  s.t. sum(w)=1, provenance groups
7      l <- l + sum_k w_k * Lambda_k                      # Chernoff fusion
8      sample N_exp(c) from posterior
9      R(c) <- transit_risk(route(s,c)) + land_risk(c, N_exp)
10 c* <- argmin_{c in F} CVaR_alpha[R(c)]
11 return ESCALATE if CVaR_alpha[R(c*)] > R_accept else (c*, evidence trace)
```

Complexity $O(MK)$ for fusion plus $O(ML)$ for route sampling. Real time at operational
candidate counts.

---

## 5. Evaluation

**Baselines**
- Nearest-feasible preplanned site (current documented practice; `baseline/conventional.py`)
- Tier heuristic (current SafeDrop selection)
- Naive Bayes fusion: no decay, no correlation handling
- Best-single-source
- Oracle with true occupancy, for regret

**Ablations**
- decay off / decay fixed rather than derived per class
- Chernoff fusion replaced by naive log-odds sum
- CVaR replaced by expectation
- transit risk removed

**Sweeps**: risk level $\alpha$; staleness distribution; injected source correlation;
injected miscalibration of the vision source.

**Metrics**: false-safe rate; realized harm; regret against oracle; posterior calibration
(Brier score, reliability diagram); AURC for abstention; end-to-end latency.

**Scale**: procedurally generated scenarios to n ≈ 200 across the five failure families,
with a held-out split not inspected during development.

---

## 6. Parameter grounding

The credibility of the paper rests on these not being authored:

| Parameter | Source |
|---|---|
| $\mu_{\text{class}}$ occupancy mixing rate | fit from time-lapse / pedestrian-count open data per land-use class |
| $p_0$ prior occupancy by class and time of day | open pedestrian and parking-occupancy datasets |
| lethality curve, sheltering factor | published SORA / ground-risk-model literature |
| $\lambda_k$ per-source likelihood ratios | fit on labeled imagery; NOT from the renderer that drew the fixtures |
| $\alpha$, $R_{\text{accept}}$ | swept, reported as a curve, not fixed |

Anything that cannot be fitted must be reported with a full sensitivity analysis.

---

## 7. Related work to verify before committing

Not yet surveyed. Must confirm the specific composition is unclaimed:

- emergency / forced landing site selection for UAS (vision-based, semantic segmentation, risk-map based)
- UAS ground risk models and risk maps (JARUS SORA; la Cour-Harbo; Primatesta; Dalamagkidis)
- covariance intersection and conservative fusion under unknown correlation (Julier & Uhlmann)
- occupancy grids with temporal decay; information decay in sensor scheduling
- CVaR and risk-averse decision making in robotics and path planning
- selective prediction and risk–coverage analysis

The individual components are established. The claimed novelty is the composition and its
application to time-critical forced-landing site selection.

---

## 8. Risks

- **Parameter grounding.** If priors, mixing rates, and lethality are authored, the result
  is a function of chosen constants and the contribution collapses. This is the primary risk.
- **Simulation only.** No real ground feed, no real aerial imagery, no flight test. Vision
  ground truth currently comes from the same renderer that draws the images, which is not
  evidence about real photographs.
- **Double conservatism.** Chernoff fusion is conservative and CVaR is risk-averse;
  composing them may over-abstain. Must be measured and discussed, not assumed benign.
- **Sample size.** n = 15 today. Must reach a few hundred with a held-out split.
- **Scope drift.** This algorithm is fully deterministic. The LLM agents from the hackathon
  are not part of the contribution; the repository supplies the harness, scenarios, tools
  and baseline, not the thesis.

---

## 9. Plan

| Phase | Work | Estimate |
|---|---|---|
| 1 | Literature verification; fix or drop the vision evidence source | 1–2 wk |
| 2 | Parameter fitting from open data; calibration harness | 2–3 wk |
| 3 | Implement Layer 1 and Layer 2; wire into the existing workflow | 2–3 wk |
| 4 | Procedural scenario generation to n ≈ 200; held-out split | 1–2 wk |
| 5 | Ablation grid, sweeps, figures | 1–2 wk |
| 6 | Writing and internal review | 2 wk |

Roughly 9–14 weeks part-time to a submittable full paper.

---

## 10. Reused from this repository

`app/tools/reachability.py`, `energy.py`, `route_planner.py`, `separation.py`,
`geometry.py`, `candidate_generator.py`, `austin_gis.py`, `osm_overpass.py`;
`baseline/conventional.py`; the whole of `evaluation/`; `data/scenarios/` and
`data/geo_cache/`; the Docker reproduction path in `REPRODUCE.md`.

Replaced by the contribution: `freshness.py` (binary staleness), the candidate tier
constants in `candidate_generator.py`, and the heuristic corroboration rule in the ELZ
evaluation path.
