"""End-to-end workflow tests that run with no API key and no network.

Agent reasoning is replaced by scripted tool sequences (``tests.scripted_agents``)
so the orchestration, deterministic tools, reconciliation, gateway, and briefing
are all exercised deterministically.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import pytest

from app.config import load_config
from app.models.enums import ContingencyAction, GroundStatus, VerificationStatus, WorkflowState
from app.orchestration.workflow import run_workflow
from tests import scripted_agents as scripts

import app.agents.contingency_agent as agent1
import app.agents.diversion_agent as agent3
import app.agents.elz_context_agent as agent2


@contextmanager
def scripted_workflow(action: str, candidate_ids: list[str], route_ids: list[str] | None = None):
    """Swap the three agents' models for scripted FunctionModels."""
    originals = (agent1.build_agent, agent2.build_agent, agent3.build_agent)

    def patch(module, model_factory):
        original = module.build_agent

        def build(cfg):
            agent = original(cfg)
            agent.model = model_factory()
            return agent

        module.build_agent = build

    patch(agent1, lambda: scripts.contingency_model(action))
    patch(agent2, lambda: scripts.elz_model(candidate_ids))
    patch(agent3, lambda: scripts.diversion_model(route_ids or candidate_ids))
    try:
        yield
    finally:
        agent1.build_agent, agent2.build_agent, agent3.build_agent = originals


def config():
    cfg = load_config()
    cfg.agents.model = "test"  # only used if a build path bypasses the patch
    cfg.agents.vision_model = "test"
    return cfg


def run(scenario_dir: str, action: str, candidate_ids: list[str], route_ids: list[str] | None = None):
    with scripted_workflow(action, candidate_ids, route_ids):
        return asyncio.run(run_workflow(scenario_dir, cfg=config()))


@pytest.fixture(autouse=True)
def _stub_vision(monkeypatch):
    """Force the image tool onto its scenario-fixture path."""
    monkeypatch.setenv("SAFEDROP_VISION", "stub")


def test_scenario_09_rejects_occupied_sites_and_replans():
    state = run("scenario_09", "LAND_IMMEDIATELY", ["ELZ-A", "ELZ-B", "ELZ-C"], route_ids=["ELZ-C"])

    assert state.contingency_decision.action is ContingencyAction.LAND_IMMEDIATELY
    assert {c.candidate_id for c in state.candidates} == {"ELZ-A", "ELZ-B", "ELZ-C"}

    statuses = {e.candidate_id: e.status for e in state.elz_context.all_evaluations()}
    assert statuses["ELZ-A"] is GroundStatus.REJECTED, "vehicles are on the parking lot"
    assert statuses["ELZ-B"] is GroundStatus.REJECTED, "people are on the park lawn"
    assert statuses["ELZ-C"] is GroundStatus.VALID

    # The direct route must fail on separation and be replanned.
    direct = next(e for e in state.route_evaluations if e.route.route_id == "ROUTE-ELZ-C-1")
    assert direct.separation is not None
    assert direct.separation.minimum_separation_m < direct.separation.required_separation_m
    assert not direct.passed
    assert state.diversion_result.replan_count >= 1

    assert state.diversion_result.selected_candidate_id == "ELZ-C"
    assert state.verification.verification_status is VerificationStatus.PASS
    assert state.workflow_state is WorkflowState.OPERATOR_PENDING
    assert state.recommendation.operator_authorization_required
    assert state.recommendation.flight_command_sent is False


def test_scenario_16_generates_off_nominal_candidates():
    ids = ["AUTO-PARK-SWENSON"]
    state = run("scenario_16", "LAND_IMMEDIATELY", ids, route_ids=["AUTO-PARK-SWENSON"])

    assert state.generated_off_nominal
    assert state.reachability.reachable_candidate_ids == []

    # Two candidates sit inside the buffer of a real mapped playground (39 m
    # and 21 m), so they are excluded before any agent sees them.
    prefiltered = {
        e.candidate_id: [c.value for c in e.reason_codes] for e in state.prefilter_rejections
    }
    assert prefiltered["AUTO-PARK-BLACKLOCUST"] == ["PLAYGROUND_PROXIMITY"]
    assert prefiltered["AUTO-PARK-HEATHERWILDE"] == ["PLAYGROUND_PROXIMITY"]

    statuses = {e.candidate_id: e.status for e in state.elz_context.all_evaluations()}
    assert statuses["AUTO-PARK-SWENSON"] is GroundStatus.VALID

    assert state.diversion_result.selected_candidate_id == "AUTO-PARK-SWENSON"
    chosen = next(c for c in state.candidates if c.candidate_id == "AUTO-PARK-SWENSON")
    assert chosen.certification_status.value == "NON_CERTIFIED"
    assert chosen.operator_review_required
    assert state.verification.verification_status is VerificationStatus.PASS


def test_scenario_17_prefers_a_farther_site_over_an_occupied_park():
    ids = ["AUS-ELZ-021", "AUS-ELZ-022", "AUS-ELZ-023"]
    state = run("scenario_17", "LAND_IMMEDIATELY", ids, route_ids=["AUS-ELZ-022"])

    from app.tools.geometry import haversine_m

    origin = state.operational_state
    park = next(c for c in state.candidates if c.candidate_id == "AUS-ELZ-021")
    chosen = next(c for c in state.candidates if c.candidate_id == "AUS-ELZ-022")
    assert haversine_m(origin.latitude, origin.longitude, chosen.latitude, chosen.longitude) > haversine_m(
        origin.latitude, origin.longitude, park.latitude, park.longitude
    ), "the verified site is farther than the occupied one"

    statuses = {e.candidate_id: e.status for e in state.elz_context.all_evaluations()}
    assert statuses["AUS-ELZ-021"] is GroundStatus.REJECTED
    assert statuses["AUS-ELZ-023"] is GroundStatus.REJECTED
    assert state.diversion_result.selected_candidate_id == "AUS-ELZ-022"


def test_agent_cannot_mark_an_occupied_site_valid():
    """A VALID claim from the agent is demoted when the rules disagree."""
    ids = ["ELZ-A", "ELZ-B", "ELZ-C"]

    with scripted_workflow("LAND_IMMEDIATELY", ids, route_ids=["ELZ-C"]):
        original = agent2.build_agent

        def build(cfg):
            agent = original(cfg)
            agent.model = scripts.elz_model(ids)
            return agent

        agent2.build_agent = build

        # Force the agent's own output to claim every site is fine.
        from app.models.landing_zone import CandidateEvaluation, ElzContextResult
        import app.orchestration.workflow as workflow

        real_run = workflow._run_elz_agent

        async def lying(deps):
            await real_run(deps)
            deps.state.elz_context = ElzContextResult(
                valid_candidates=[
                    CandidateEvaluation(candidate_id=cid, status=GroundStatus.VALID)
                    for cid in ids
                ],
                summary="Agent claims everything is clear.",
            )
            deps.state.elz_context = workflow._reconcile_elz_output(deps, deps.state.elz_context)

        workflow._run_elz_agent = lying
        try:
            state = asyncio.run(run_workflow("scenario_09", cfg=config()))
        finally:
            workflow._run_elz_agent = real_run

    statuses = {e.candidate_id: e.status for e in state.elz_context.all_evaluations()}
    assert statuses["ELZ-A"] is GroundStatus.REJECTED
    assert statuses["ELZ-B"] is GroundStatus.REJECTED
    assert statuses["ELZ-C"] is GroundStatus.VALID


def test_continue_branch_does_not_open_the_landing_workflow():
    state = run("scenario_01", "CONTINUE", [])
    assert state.contingency_decision.action is ContingencyAction.CONTINUE
    assert state.candidates == []
    assert state.elz_context is None, "the ELZ workflow must not open on a CONTINUE"
    assert state.diversion_result is None
    assert state.verification.verification_status is VerificationStatus.PASS


def test_no_flight_command_is_ever_sent():
    from app.orchestration.workflow import apply_operator_decision

    state = run("scenario_09", "LAND_IMMEDIATELY", ["ELZ-A", "ELZ-B", "ELZ-C"], route_ids=["ELZ-C"])
    approved = apply_operator_decision(state, approve=True)
    assert approved.workflow_state is WorkflowState.APPROVED
    assert approved.flight_command_sent is False
    assert approved.simulation_only is True


def test_every_candidate_sits_on_real_open_ground():
    """Landing sites must be anchored on measured open space, not invented points.

    A hand-placed coordinate looks fine until someone opens a satellite basemap
    and finds the "recovery yard" on a rooftop. Every candidate carries the
    OpenStreetMap feature it came from and the radius of the largest circle
    that fits inside it.
    """
    from shapely.geometry import Point, shape

    from app.services.scenario_loader import list_scenarios, load_scenario_cached
    from app.tools.geometry import LocalFrame

    checked = 0
    for meta in list_scenarios():
        scenario = load_scenario_cached(meta["dir"])
        raw = json.loads(scenario.scenario.files.known_elzs_geojson.read_text())
        for feature in raw["features"]:
            props = feature["properties"]
            assert "osm_id" in props, f"{props['candidate_id']} has no OSM provenance"
            assert props["open_radius_m"] >= 15.0, props["candidate_id"]

            # The anchor must actually lie inside the real polygon.
            frame = LocalFrame(props["anchor_lat"], props["anchor_lon"])
            polygon = frame.geometry(feature["geometry"])
            assert polygon.contains(Point(0.0, 0.0)), (
                f"{props['candidate_id']} anchor falls outside its own footprint"
            )
            checked += 1
    assert checked >= 12, f"only checked {checked} candidates"


def test_every_anchor_passed_open_ground_verification():
    """A polygon tagged "park" is not proof of open ground.

    OSM park boundaries enclose houses, service roads and playscapes.
    ``scripts/verify_anchors.py`` reverse-geocodes each candidate point and
    measures the distance to the nearest mapped structure; only anchors with
    real clearance make it into a scenario.
    """
    from app.services.scenario_loader import list_scenarios, load_scenario_cached

    checked = 0
    for meta in list_scenarios():
        scenario = load_scenario_cached(meta["dir"])
        for path in (
            scenario.scenario.files.known_elzs_geojson,
            scenario.scenario.files.map_features_geojson,
        ):
            if path is None or not path.exists():
                continue
            for feature in json.loads(path.read_text())["features"]:
                props = feature["properties"]
                if not props.get("candidate_id"):
                    continue
                assert props["anchor_clearance_score_m"] >= 20.0, (
                    f"{props['candidate_id']} anchor is too close to "
                    f"{props['nearest_feature']}"
                )
                checked += 1
    assert checked >= 12, f"only checked {checked} candidates"


def test_run_snapshot_is_json_serialisable_for_every_scenario():
    """The console renders from JSON, so the snapshot must always encode.

    Optional telemetry channels arrive from pandas as NaN when a scenario does
    not carry them. NaN is not valid JSON: it reaches `json.dumps` and 500s the
    endpoint, which the console shows as a Start button that does nothing.
    """
    import json as _json

    from app.config import load_config
    from app.services.run_manager import RunManager
    from app.services.scenario_loader import list_scenarios
    from app.web.routes import _map_layers, _snapshot

    cfg = load_config()
    manager = RunManager()
    for meta in list_scenarios():
        session = manager.create(meta["dir"], cfg)
        for payload, label in ((_snapshot(session), "snapshot"), (_map_layers(session), "map")):
            encoded = _json.dumps(payload, allow_nan=False)
            assert "NaN" not in encoded, f"{meta['id']} {label} carries NaN"
