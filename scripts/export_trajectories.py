"""Render agent trajectories as readable Markdown.

The raw JSONL under ``trajectories/`` is the machine record. This turns a run
into something a person can follow end to end: the instructions the agent was
given, the prompt it received, every tool call and what the tool answered, the
decision that result drove, retries, and the human checkpoint at the end.

    python -m scripts.export_trajectories --run SCENARIO-09-abc12345
    python -m scripts.export_trajectories --best        # one per scenario

Output lands in ``trajectories/readable/``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.config import REPO_ROOT, TRAJECTORY_DIR

OUT = TRAJECTORY_DIR / "readable"
PROMPTS = REPO_ROOT / "app" / "prompts"

AGENT_PROMPT = {
    "contingency_decision_agent": "contingency_agent.md",
    "elz_context_agent": "elz_context_agent.md",
    "diversion_verification_agent": "diversion_agent.md",
}
AGENT_TITLE = {
    "contingency_decision_agent": "Agent 1 — Contingency Decision",
    "elz_context_agent": "Agent 2 — ELZ Context Evaluation",
    "diversion_verification_agent": "Agent 3 — Diversion Verification",
}


def brief(value, limit: int = 340) -> str:
    """Compact a tool result to the fields a reader actually needs."""
    if isinstance(value, dict):
        keep = {
            k: v for k, v in value.items()
            if k in (
                "safe", "verdict", "status", "feasible", "route_id", "candidate_id",
                "variant", "energy_margin_wh", "minimum_separation_m", "required_separation_m",
                "conflicting_vehicle_id", "reason_codes", "unverified_reason_codes",
                "people_count", "vehicle_count", "animal_count", "visible_clear_area_percent",
                "pedestrian_count", "clear_area_percent", "remaining_flight_time_sec",
                "usable_energy_wh", "max_reachable_distance_m", "distance_m",
                "intersecting_restrictions", "intersecting_obstacles", "obstructions",
                "freshness_status", "missing_evidence", "source", "confidence",
            )
        }
        text = json.dumps(keep or value, default=str)
    else:
        text = json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render(path: Path) -> str:
    records = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if not records:
        return ""
    scenario = records[0]["scenario_id"]
    run_id = records[0]["run_id"]

    lines = [f"# Trajectory — {scenario}", "", f"`{run_id}`", ""]

    complete = next((r for r in records if r.get("event") == "run_complete"), None)
    if complete:
        d = complete["detail"]
        lines += [
            f"**Outcome:** `{d['workflow_state']}` · verification `{d['verification']}`",
            "",
            f"**Cost of this run:** {d['summary']['tool_calls']} tool calls · "
            f"{d['summary']['input_tokens']:,} input + {d['summary']['output_tokens']:,} output tokens · "
            f"{d['summary']['agent_latency_ms'] / 1000:.1f} s of model time",
            "",
        ]

    prompts_seen: dict[str, str] = {}
    for r in records:
        if r.get("kind") == "agent_prompt":
            prompts_seen[r["agent_name"]] = r.get("prompt", "")

    by_agent: dict[str, list[dict]] = {}
    for r in records:
        name = r.get("agent_name", "orchestrator")
        by_agent.setdefault(name, []).append(r)

    for agent, title in AGENT_TITLE.items():
        rows = by_agent.get(agent)
        if not rows:
            continue
        lines += ["---", "", f"## {title}", ""]

        prompt_file = PROMPTS / AGENT_PROMPT[agent]
        lines += [
            "<details><summary><b>Instructions this agent was given</b> "
            f"(<code>app/prompts/{AGENT_PROMPT[agent]}</code>, "
            f"{len(prompt_file.read_text().split())} words — click to expand)</summary>",
            "",
            "```markdown",
            prompt_file.read_text().strip(),
            "```",
            "</details>",
            "",
            "**What it was asked**",
            "",
            "```",
            (prompts_seen.get(agent) or "(prompt not recorded)").strip()[:900],
            "```",
            "",
            "**What it did**",
            "",
            "| # | Tool | Arguments | Tool responded | Which drove |",
            "|---:|---|---|---|---|",
        ]
        n = 0
        for r in rows:
            if r.get("kind") != "tool_call":
                continue
            n += 1
            args = ", ".join(f"{k}={v}" for k, v in (r.get("tool_arguments") or {}).items()) or "—"
            lines.append(
                f"| {n} | `{r['tool_name']}` | {args} | "
                f"`{brief(r.get('tool_result'))}` | "
                f"{('**' + r['decision'] + '**') if r.get('decision') else '—'} |"
            )
        if n == 0:
            lines.append("| — | *no tool calls* | — | — | — |")

        feedback = []
        prev = None
        for r in rows:
            if r.get("kind") != "tool_call":
                continue
            res = r.get("tool_result")
            if isinstance(res, dict) and res.get("verdict") == "PREREQUISITES_NOT_MET":
                feedback.append(
                    f"- Step {r['step_number']}: `check_hard_constraints` **refused to answer** for "
                    f"`{res['candidate_id']}` and named what was missing "
                    f"({', '.join('`' + m + '`' for m in res['missing_evidence'])}). "
                    "The agent gathered exactly those, then asked again."
                )
            if r.get("decision") == "REPLAN_ROUTE" and prev is not None:
                pres = prev.get("tool_result") or {}
                if isinstance(pres, dict) and pres.get("safe") is False:
                    detail = (
                        f"{pres.get('minimum_separation_m')} m against "
                        f"{pres.get('conflicting_vehicle_id')}, "
                        f"{pres.get('required_separation_m')} m required"
                        if pres.get("minimum_separation_m") is not None
                        else ", ".join(pres.get("intersecting_restrictions") or
                                       pres.get("intersecting_obstacles") or ["a failed check"])
                    )
                    feedback.append(
                        f"- Step {prev['step_number']}: `{prev['tool_name']}` returned **not safe** "
                        f"({detail}). The agent read that as *the path is wrong, the site is not* "
                        f"and requested variant {r['tool_arguments'].get('variant')} rather than "
                        "abandoning the candidate."
                    )
            prev = r
        if feedback:
            lines += ["", "**Feedback that shaped the next step**", ""] + feedback + [""]

        out = next((r for r in rows if r.get("kind") == "agent_output"), None)
        if out:
            lines += ["", "**Structured output**", "", "```json",
                      json.dumps(out.get("output"), indent=2, default=str)[:1600], "```", ""]
            if out.get("error"):
                lines += [f"> Agent call failed: `{out['error']}` — the orchestrator fell back "
                          "to deterministic rules rather than guessing.", ""]
            retries = out.get("retry_count") or 0
            lines += [
                f"**Retries:** {retries}"
                + ("" if retries else " — the agent's first structured output validated, so "
                   "PydanticAI did not need to re-ask."),
                "",
            ]

    gateway = [r for r in records if r.get("agent_name") == "safety_gateway"]
    if gateway:
        result = gateway[0].get("tool_result") or {}
        lines += ["---", "", "## Safety Verification Gateway", "",
                  "Deterministic. Re-reads the stored tool results; an unrun check is not a pass.",
                  "", "| Check | Result | Detail |", "|---|---|---|"]
        for c in result.get("checks", []):
            mark = "PASS" if c["passed"] else ("FAIL" if c["passed"] is False else "n/a")
            lines.append(f"| {c['name']} | **{mark}** | {c.get('detail', '')} |")
        lines += ["", f"**Verdict:** `{result.get('verification_status')}`", ""]

    lines += ["---", "", "## Human checkpoint", "",
              "The workflow stops here. A remote Pilot in Command approves or rejects; "
              "`flight_command_sent` is `false` on every path and no command is issued to any aircraft.",
              ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Render trajectories as Markdown.")
    ap.add_argument("--run", help="A specific run id.")
    ap.add_argument("--best", action="store_true", help="One representative run per scenario.")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    files = sorted(TRAJECTORY_DIR.glob("*.jsonl"))
    if args.run:
        files = [f for f in files if args.run in f.name]
    elif args.best:
        # Prefer the run with the most tool calls: the most instructive one.
        chosen: dict[str, tuple[int, Path]] = {}
        for f in files:
            sid = re.sub(r"(-[0-9a-f]{8})?\.jsonl$", "", f.name)
            calls = sum(1 for l in f.read_text().splitlines() if '"tool_call"' in l)
            if sid not in chosen or calls > chosen[sid][0]:
                chosen[sid] = (calls, f)
        files = [f for _, f in sorted(chosen.values(), key=lambda x: x[1].name)]

    written = 0
    for f in files:
        text = render(f)
        if not text:
            continue
        sid = re.sub(r"(-[0-9a-f]{8})?\.jsonl$", "", f.name)
        (OUT / f"{sid}.md").write_text(text)
        written += 1
    print(f"wrote {written} readable trajectories to {OUT}")


if __name__ == "__main__":
    main()
