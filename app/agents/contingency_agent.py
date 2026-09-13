"""Agent 1 — Contingency Decision Agent.

Decides which contingency branch the operator should be shown. Every physical
quantity it cites comes from a deterministic tool defined here; the agent's
contribution is choosing which options to test and weighing a degraded
subsystem against a feasible alternative.
"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.common import agent_model, load_prompt, model_settings, timed
from app.agents.deps import AgentDeps
from app.config import SafeDropConfig
from app.models.contingency import ContingencyDecision, EnergyEstimate, FeasibilityCheck
from app.models.enums import ContingencyAction, ReasonCode, Severity
from app.models.telemetry import OperationalState
from app.tools.energy import check_feasibility, estimate_energy
from app.tools.reachability import compute_reachability

AGENT_NAME = "contingency_decision_agent"


def build_agent(cfg: SafeDropConfig) -> Agent[AgentDeps, ContingencyDecision]:
    agent = Agent(
        agent_model(cfg),
        deps_type=AgentDeps,
        output_type=ContingencyDecision,
        instructions=load_prompt("contingency_agent"),
        name=AGENT_NAME,
        retries=cfg.agents.retries,
        model_settings=model_settings(cfg),
    )

    @agent.tool
    def calculate_remaining_energy(ctx: RunContext[AgentDeps]) -> EnergyEstimate:
        """Remaining usable energy and endurance after the mandated reserve."""
        state = _state(ctx)
        with timed() as t:
            estimate = estimate_energy(state, ctx.deps.cfg)
        ctx.deps.state.energy_estimate = estimate
        ctx.deps.logger.log_tool(
            AGENT_NAME, "calculate_remaining_energy", {}, estimate, t.ms
        )
        return estimate

    def _feasibility(
        ctx: RunContext[AgentDeps],
        target: str,
        lat: float,
        lon: float,
        distance_m: float | None,
        insufficient: ReasonCode,
        sufficient: ReasonCode,
    ) -> FeasibilityCheck:
        state = _state(ctx)
        estimate = ctx.deps.state.energy_estimate or estimate_energy(state, ctx.deps.cfg)
        ctx.deps.state.energy_estimate = estimate
        with timed() as t:
            check = check_feasibility(
                target, lat, lon, state, estimate, ctx.deps.cfg, insufficient, sufficient, distance_m
            )
        ctx.deps.state.feasibility_checks = [
            c for c in ctx.deps.state.feasibility_checks if c.target != target
        ] + [check]
        ctx.deps.logger.log_tool(
            AGENT_NAME,
            f"check_{target.lower()}_feasibility",
            {"target": target},
            check,
            t.ms,
            decision="FEASIBLE" if check.feasible else "NOT_FEASIBLE",
            reason_code=check.reason_codes[0] if check.reason_codes else None,
        )
        return check

    @agent.tool
    def check_destination_feasibility(ctx: RunContext[AgentDeps]) -> FeasibilityCheck:
        """Can the aircraft still complete the delivery to its destination?"""
        mission = ctx.deps.scenario.scenario.mission
        return _feasibility(
            ctx,
            "DESTINATION",
            mission.destination.latitude,
            mission.destination.longitude,
            _state(ctx).distance_to_destination_m,
            ReasonCode.INSUFFICIENT_MISSION_ENERGY,
            ReasonCode.MISSION_ENERGY_SUFFICIENT,
        )

    @agent.tool
    def check_return_to_base_feasibility(ctx: RunContext[AgentDeps]) -> FeasibilityCheck:
        """Can the aircraft return to its launch base on remaining usable energy?"""
        mission = ctx.deps.scenario.scenario.mission
        return _feasibility(
            ctx,
            "BASE",
            mission.base.latitude,
            mission.base.longitude,
            _state(ctx).distance_to_base_m,
            ReasonCode.INSUFFICIENT_RTB_ENERGY,
            ReasonCode.RTB_ENERGY_SUFFICIENT,
        )

    @agent.tool
    def check_alternate_hub_feasibility(ctx: RunContext[AgentDeps]) -> FeasibilityCheck:
        """Can the aircraft reach the alternate recovery hub, if the scenario has one?"""
        mission = ctx.deps.scenario.scenario.mission
        if mission.alternate_hub is None:
            check = FeasibilityCheck(
                target="ALTERNATE_HUB",
                distance_m=0.0,
                ground_speed_mps=0.0,
                required_time_sec=0.0,
                required_energy_wh=0.0,
                energy_margin_wh=0.0,
                feasible=False,
                reason_codes=[ReasonCode.INSUFFICIENT_HUB_ENERGY],
            )
            ctx.deps.logger.log_tool(
                AGENT_NAME,
                "check_alternate_hub_feasibility",
                {},
                {"status": "NO_HUB_DEFINED"},
                0.0,
                decision="NOT_FEASIBLE",
            )
            return check
        return _feasibility(
            ctx,
            "ALTERNATE_HUB",
            mission.alternate_hub.latitude,
            mission.alternate_hub.longitude,
            _state(ctx).distance_to_hub_m,
            ReasonCode.INSUFFICIENT_HUB_ENERGY,
            ReasonCode.HUB_ENERGY_SUFFICIENT,
        )

    @agent.tool
    def calculate_reachability_envelope(ctx: RunContext[AgentDeps]) -> dict:
        """Wind-adjusted reachable distance and which preplanned ELZs fall inside it."""
        state = _state(ctx)
        estimate = ctx.deps.state.energy_estimate or estimate_energy(state, ctx.deps.cfg)
        with timed() as t:
            result = compute_reachability(
                state, estimate, ctx.deps.scenario.known_elzs, ctx.deps.cfg
            )
        ctx.deps.state.reachability = result
        summary = {
            "max_reachable_distance_m": result.max_reachable_distance_m,
            "remaining_flight_time_sec": result.remaining_flight_time_sec,
            "reachable_preplanned_elz_ids": result.reachable_candidate_ids,
            "unreachable_preplanned_elz_ids": result.unreachable_candidate_ids,
            "status": result.status,
        }
        ctx.deps.logger.log_tool(
            AGENT_NAME, "calculate_reachability_envelope", {}, summary, t.ms
        )
        return summary

    return agent


def _state(ctx: RunContext[AgentDeps]) -> OperationalState:
    state = ctx.deps.state.operational_state
    if state is None:
        raise RuntimeError("Operational state has not been prepared.")
    return state


def build_prompt(state: OperationalState, scenario_name: str) -> str:
    """The compressed state the agent actually sees. No raw telemetry rows."""
    hub = (
        f"\n- Distance to alternate hub: {state.distance_to_hub_m:.0f} m"
        if state.distance_to_hub_m is not None
        else "\n- Alternate hub: none defined for this mission"
    )
    notes = "\n".join(f"  - {n}" for n in state.notes) or "  - (none)"

    def line(label: str, value: str | None) -> str:
        return f"\n- {label}: {value}" if value else ""

    propulsion = line(
        "Airframe vibration",
        f"{state.vibration_state} ({state.vibration_g:.2f} g RMS)"
        if state.vibration_g is not None
        else None,
    ) + line("ESC temperature", f"{state.esc_temp_c:.0f} C" if state.esc_temp_c is not None else None)

    power = line(
        "Power draw",
        f"{state.power_state} — {state.power_draw_w:.0f} W, "
        f"{state.power_excess_ratio:.2f}x expected"
        if state.power_draw_w is not None and state.power_excess_ratio is not None
        else None,
    )

    gnss = line(
        "GNSS",
        f"{state.gps_satellites} satellites, HDOP {state.gps_hdop:.1f}"
        if state.gps_satellites is not None
        else None,
    ) + line(
        "Sensor agreement",
        f"{state.sensor_agreement_state} — heading sources differ by "
        f"{state.heading_disagreement_deg:.0f} deg"
        if state.heading_disagreement_deg is not None
        else None,
    )

    gust = line("Gusting to", f"{state.wind_gust_mps:.0f} m/s" if state.wind_gust_mps else None)

    return f"""\
Mission: {scenario_name}
Elapsed mission time: {state.timestamp_s:.0f} s (state compressed over the last {state.trend_window_sec:.0f} s)

AIRCRAFT STATE
- Battery: {state.battery_state} at {state.battery_soc:.1f}% SOC, \
falling {state.battery_drop_rate_pct_min:.2f} %/min, pack {state.battery_temperature_c:.0f} C
- Motor: {state.motor_state} (health index {state.motor_health:.2f}){propulsion}{power}
- Navigation: {state.navigation_state}{gnss}
- Communication link: {state.communication_state} (quality {state.link_quality:.2f})
- Altitude: {state.altitude_m:.0f} m AGL
- Airspeed: {state.airspeed_mps:.1f} m/s
- Payload: {state.payload_kg:.1f} kg
- Wind: {state.wind_speed_mps:.1f} m/s from {state.wind_direction_deg:.0f} deg, risk {state.wind_risk}{gust}

GEOMETRY
- Distance to destination: {state.distance_to_destination_m:.0f} m
- Distance to base: {state.distance_to_base_m:.0f} m{hub}

PREPROCESSOR NOTES
{notes}

Decide the contingency action. Use your tools for every feasibility judgement.
"""


def build_raw_prompt(
    telemetry, index: int, mission, scenario_name: str, rows: int = 60
) -> str:
    """The control condition for the state-compression experiment.

    Same aircraft, same instant, same tools — but the agent is handed raw
    telemetry rows and has to do its own trend reading. Everything the
    preprocessor would have labelled (battery state, motor state, navigation
    quality, wind risk, distances) is absent.
    """
    start = max(0, index - rows + 1)
    window = telemetry.iloc[start : index + 1]
    csv = window.to_csv(index=False)
    return f"""\
Mission: {scenario_name}

Below are the last {len(window)} seconds of raw aircraft telemetry, one row per second,
as recorded. No preprocessing has been applied.

MISSION GEOMETRY
- Base: {mission.base.name} at {mission.base.latitude:.5f}, {mission.base.longitude:.5f}
- Destination: {mission.destination.name} at {mission.destination.latitude:.5f}, {mission.destination.longitude:.5f}
""" + (
        f"- Alternate hub: {mission.alternate_hub.name} at "
        f"{mission.alternate_hub.latitude:.5f}, {mission.alternate_hub.longitude:.5f}\n"
        if mission.alternate_hub
        else "- Alternate hub: none defined for this mission\n"
    ) + f"""
RAW TELEMETRY
```csv
{csv}```

Decide the contingency action. Use your tools for every feasibility judgement.
"""


def fallback_decision(reason: str) -> ContingencyDecision:
    """Used when the agent call itself fails. Abstention, never a guess."""
    return ContingencyDecision(
        action=ContingencyAction.ESCALATE,
        severity=Severity.WARNING,
        emergency_type=None,
        mission_completion_possible=False,
        return_to_base_possible=False,
        alternate_hub_possible=False,
        forced_landing_required=False,
        reason_codes=[ReasonCode.AGENT_FAILURE],
        rationale=f"Contingency agent unavailable: {reason}. Escalating to the operator.",
    )
