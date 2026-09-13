# Agent instructions

These are the instructions that shape each agent — the full text, versioned with the
code and loaded at runtime by `app/agents/common.py:load_prompt`. Nothing here is
generated or paraphrased for the write-up; this is what the model actually receives.

| Agent | Instructions | Its question | Tools it is given |
|---|---|---|---|
| **Agent 1** — Contingency Decision | [`contingency_agent.md`](contingency_agent.md) | Which contingency branch is appropriate? | energy · endurance · destination / base / hub feasibility · reachability envelope |
| **Agent 2** — ELZ Context Evaluation | [`elz_context_agent.md`](elz_context_agent.md) | Which reachable sites are suitable *right now*? | ground feed · vision · Austin GIS · OSM · weather · freshness · hard-constraint checker |
| **Agent 3** — Diversion Verification | [`diversion_agent.md`](diversion_agent.md) | Can the aircraft safely reach one of them? | route planner · route energy · obstacles · restrictions · UAV separation · descent corridor |

Toolsets are deliberately narrow. Agent 1 cannot see imagery, Agent 2 cannot plan
routes, and Agent 3 cannot re-litigate ground safety. Each agent returns a Pydantic
model, never free text.

Alongside the instructions, each agent receives a generated prompt describing the
situation. Those builders are `build_prompt` in each of `app/agents/*.py`, and the exact
text an agent received on a given run is recorded in its trajectory — see
[`trajectories/README.md`](../../trajectories/README.md).

## The rule these all share

No tool in this system lets an agent assert a number. Range, energy, separation and
geometry are computed in code and handed to the agent. An agent that states a figure it
did not receive from a tool is stating something the system will not stand behind, and
two mechanisms catch it: reconciliation in `app/orchestration/workflow.py`, and the
deterministic Safety Verification Gateway in `app/verification/safety_gateway.py`.
