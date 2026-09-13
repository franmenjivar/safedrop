"""Starlette routes for the SafeDrop operations console.

Polling-based on purpose: the demo needs to be readable and reproducible, not
real-time. No WebSockets until something actually requires them.
"""

from __future__ import annotations

import json
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from app.config import REPO_ROOT, TRAJECTORY_DIR, get_config
from app.models.enums import GroundStatus
from app.services.run_manager import RUNS, RunSession
from app.services.scenario_loader import list_scenarios, load_scenario_cached

WEB_DIR = REPO_ROOT / "app" / "web"
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


async def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"scenarios": list_scenarios()}
    )


async def about(request: Request):
    """The explainer page: the problem, the architecture, and the evidence."""
    return templates.TemplateResponse(request, "about.html", {})


# --------------------------------------------------------------------------
# Scenario catalogue
# --------------------------------------------------------------------------


async def get_scenarios(request: Request):
    return JSONResponse(list_scenarios())


async def get_scenario(request: Request):
    scenario_id = request.path_params["scenario_id"]
    scenario = load_scenario_cached(scenario_id)
    manifest = scenario.scenario
    return JSONResponse(
        {
            "id": manifest.id,
            "name": manifest.name,
            "description": manifest.description,
            "region": manifest.region,
            "telemetry_rows": len(scenario.telemetry),
            "decision_step": manifest.decision_step,
            "mission": {
                "base": manifest.mission.base.model_dump(),
                "destination": manifest.mission.destination.model_dump(),
                "alternate_hub": manifest.mission.alternate_hub.model_dump()
                if manifest.mission.alternate_hub
                else None,
            },
            "preplanned_elzs": [c.model_dump(mode="json") for c in scenario.known_elzs],
            "has_traffic": scenario.traffic is not None,
            "has_images": scenario.image_dir is not None,
            "ground_truth": scenario.ground_truth.model_dump(mode="json")
            if scenario.ground_truth
            else None,
        }
    )


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


async def start_run(request: Request):
    scenario_id = request.path_params["scenario_id"]
    session = RUNS.create(scenario_id, get_config())
    body = await _json_body(request)
    if body.get("immediate"):
        await RUNS.run_to_completion(session)
    else:
        await RUNS.start(session)
    return JSONResponse({"run_id": session.run_id})


async def pause_run(request: Request):
    session = _session(request)
    RUNS.pause(session)
    return JSONResponse({"run_id": session.run_id, "playing": False})


async def step_run(request: Request):
    session = _session(request)
    await RUNS.step(session)
    return JSONResponse(_snapshot(session))


async def continue_run(request: Request):
    session = _session(request)
    await RUNS.start(session)
    return JSONResponse({"run_id": session.run_id, "playing": True})


async def complete_run(request: Request):
    session = _session(request)
    await RUNS.run_to_completion(session)
    return JSONResponse(_snapshot(session))


async def reset_run(request: Request):
    session = _session(request)
    fresh = RUNS.reset(session)
    return JSONResponse({"run_id": fresh.run_id})


async def get_run(request: Request):
    return JSONResponse(_snapshot(_session(request)))


async def get_map(request: Request):
    return JSONResponse(_map_layers(_session(request)))


async def get_trajectory(request: Request):
    session = _session(request)
    path = TRAJECTORY_DIR / f"{session.run_id}.jsonl"
    if not path.exists():
        return JSONResponse([])
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return JSONResponse(records)


async def operator_decision(request: Request):
    session = _session(request)
    body = await _json_body(request)
    approve = str(body.get("decision", "")).upper() == "APPROVE"
    RUNS.operator_decision(session, approve)
    return JSONResponse(_snapshot(session))


async def scenario_image(request: Request):
    scenario_id = request.path_params["scenario_id"]
    filename = Path(request.path_params["filename"]).name
    scenario = load_scenario_cached(scenario_id)
    if scenario.image_dir is None:
        return PlainTextResponse("No imagery for this scenario.", status_code=404)
    path = scenario.image_dir / filename
    if not path.exists():
        return PlainTextResponse("Image not found.", status_code=404)
    return FileResponse(path)


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def _session(request: Request) -> RunSession:
    session = RUNS.get(request.path_params["run_id"])
    if session is None:
        raise KeyError(f"Unknown run {request.path_params['run_id']}")
    return session


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def _snapshot(session: RunSession) -> dict:
    state = session.state
    scenario = session.scenario
    candidates = {c.candidate_id: c for c in state.candidates}

    evaluations = []
    if state.elz_context:
        for evaluation in state.elz_context.all_evaluations():
            candidate = candidates.get(evaluation.candidate_id)
            observation = next(
                (o for o in state.image_observations if o.candidate_id == evaluation.candidate_id),
                None,
            )
            evaluations.append(
                {
                    **evaluation.model_dump(mode="json"),
                    "name": candidate.name if candidate else evaluation.candidate_id,
                    "tier": candidate.tier if candidate else None,
                    "certification_status": candidate.certification_status if candidate else None,
                    "feature_type": candidate.feature_type if candidate else None,
                    "area_m2": candidate.area_m2 if candidate else None,
                    "image_url": _image_url(scenario, candidate)
                    if candidate and candidate.image_path
                    else None,
                    "image_observation": observation.model_dump(mode="json") if observation else None,
                }
            )

    return {
        "run_id": state.run_id,
        "agent_mode": session.cfg.agents.mode,
        "agent_model": session.cfg.agents.model,
        "maps_embed_key": session.cfg.apis.google_maps_embed_key,
        "scenario": {
            "id": scenario.scenario.id,
            "name": scenario.scenario.name,
            "description": scenario.scenario.description,
            "region": scenario.scenario.region,
            "decision_step": scenario.scenario.decision_step,
        },
        "workflow_state": state.workflow_state,
        "playing": state.playing,
        "telemetry_index": state.telemetry_index,
        "telemetry_total": state.telemetry_total,
        "telemetry": state.latest_telemetry.model_dump(mode="json") if state.latest_telemetry else None,
        "operational_state": state.operational_state.model_dump(mode="json")
        if state.operational_state
        else None,
        "energy": state.energy_estimate.model_dump(mode="json") if state.energy_estimate else None,
        "feasibility": [c.model_dump(mode="json") for c in state.feasibility_checks],
        "contingency_decision": state.contingency_decision.model_dump(mode="json")
        if state.contingency_decision
        else None,
        "reachability": {
            "max_reachable_distance_m": state.reachability.max_reachable_distance_m,
            "remaining_flight_time_sec": state.reachability.remaining_flight_time_sec,
            "reachable_candidate_ids": state.reachability.reachable_candidate_ids,
            "unreachable_candidate_ids": state.reachability.unreachable_candidate_ids,
            "status": state.reachability.status,
        }
        if state.reachability
        else None,
        "generated_off_nominal": state.generated_off_nominal,
        "prefilter_rejections": [e.model_dump(mode="json") for e in state.prefilter_rejections],
        "candidate_evaluations": evaluations,
        "route_evaluations": [
            {
                "route_id": e.route.route_id,
                "candidate_id": e.route.candidate_id,
                "variant": e.route.variant,
                "strategy": e.route.strategy,
                "distance_m": e.route.distance_m,
                "passed": e.passed,
                "reason_codes": [c.value for c in e.reason_codes],
                "energy": e.energy.model_dump(mode="json") if e.energy else None,
                "separation": e.separation.model_dump(mode="json") if e.separation else None,
                "obstacles": e.obstacles.model_dump(mode="json") if e.obstacles else None,
                "restrictions": e.restrictions.model_dump(mode="json") if e.restrictions else None,
                "descent": e.descent.model_dump(mode="json") if e.descent else None,
            }
            for e in state.route_evaluations
        ],
        "diversion": state.diversion_result.model_dump(mode="json") if state.diversion_result else None,
        "verification": state.verification.model_dump(mode="json") if state.verification else None,
        "recommendation": state.recommendation.model_dump(mode="json") if state.recommendation else None,
        "operator_decision": state.operator_decision,
        "trace": state.trace,
        "terminal_reason_codes": [c.value for c in state.terminal_reason_codes],
        "workflow_error": session.workflow_error,
        "simulation_only": True,
        "flight_command_sent": False,
    }


def _image_url(scenario, candidate) -> str:
    return f"/scenario-images/{scenario.scenario.directory.name}/{candidate.image_path}"


def _map_layers(session: RunSession) -> dict:
    """GeoJSON layers for the map panel."""
    state = session.state
    scenario = session.scenario
    manifest = scenario.scenario

    telemetry = scenario.telemetry.iloc[: state.telemetry_index + 1]
    track = [[float(r.latitude), float(r.longitude)] for r in telemetry.itertuples()]

    statuses: dict[str, str] = {}
    if state.elz_context:
        for evaluation in state.elz_context.all_evaluations():
            statuses[evaluation.candidate_id] = evaluation.status
    selected = state.diversion_result.selected_candidate_id if state.diversion_result else None

    geometry_by_id = _candidate_geometries(scenario)
    candidates = []
    source_list = state.candidates or scenario.known_elzs
    for candidate in source_list:
        status = statuses.get(candidate.candidate_id, GroundStatus.UNVERIFIED.value)
        candidates.append(
            {
                "candidate_id": candidate.candidate_id,
                "name": candidate.name,
                "tier": candidate.tier,
                "certification_status": candidate.certification_status,
                "feature_type": candidate.feature_type,
                "area_m2": candidate.area_m2,
                "latitude": candidate.latitude,
                "longitude": candidate.longitude,
                "status": status,
                "selected": candidate.candidate_id == selected,
                "geometry": geometry_by_id.get(candidate.candidate_id),
            }
        )

    unreachable = []
    if state.generated_off_nominal:
        for candidate in scenario.known_elzs:
            unreachable.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "name": candidate.name,
                    "latitude": candidate.latitude,
                    "longitude": candidate.longitude,
                    "geometry": geometry_by_id.get(candidate.candidate_id),
                }
            )

    traffic = []
    if scenario.traffic is not None:
        for vehicle_id, frames in scenario.traffic.groupby("vehicle_id"):
            frames = frames.sort_values("timestamp_s")
            traffic.append(
                {
                    "vehicle_id": str(vehicle_id),
                    "track": [[float(r.latitude), float(r.longitude)] for r in frames.itertuples()],
                }
            )

    return {
        "drone": {
            "latitude": state.operational_state.latitude if state.operational_state else None,
            "longitude": state.operational_state.longitude if state.operational_state else None,
            "altitude_m": state.operational_state.altitude_m if state.operational_state else None,
        },
        "track": track,
        "mission": {
            "base": manifest.mission.base.model_dump(),
            "destination": manifest.mission.destination.model_dump(),
            "alternate_hub": manifest.mission.alternate_hub.model_dump()
            if manifest.mission.alternate_hub
            else None,
        },
        "reachability_envelope": [list(p) for p in state.reachability.envelope_polygon]
        if state.reachability
        else [],
        "candidates": candidates,
        "unreachable_preplanned": unreachable,
        "prefiltered": [
            {
                "candidate_id": e.candidate_id,
                "reason_codes": [c.value for c in e.reason_codes],
                "geometry": geometry_by_id.get(e.candidate_id),
            }
            for e in state.prefilter_rejections
        ],
        "routes": [
            {
                "route_id": e.route.route_id,
                "candidate_id": e.route.candidate_id,
                "variant": e.route.variant,
                "passed": e.passed,
                "selected": bool(
                    state.diversion_result
                    and e.route.route_id == state.diversion_result.selected_route_id
                ),
                "path": [[w.latitude, w.longitude] for w in e.route.waypoints],
                # Altitude per waypoint, so the console can animate the descent
                # profile the remote pilot would fly after approving.
                "altitudes": [w.altitude_m for w in e.route.waypoints],
            }
            for e in state.route_evaluations
        ],
        "traffic": traffic,
        "obstacles": scenario.obstacles,
        "restrictions": scenario.restrictions,
        "context_features": [
            f
            for f in scenario.map_features
            if f["properties"].get("feature_type")
            in ("school", "hospital", "playground", "major_road", "railway", "building")
        ],
    }


def _candidate_geometries(scenario) -> dict[str, dict]:
    geometries: dict[str, dict] = {}
    for path in (scenario.scenario.files.known_elzs_geojson, scenario.scenario.files.map_features_geojson):
        if path is None or not path.exists():
            continue
        payload = json.loads(path.read_text())
        for feature in payload.get("features", []):
            candidate_id = feature["properties"].get("candidate_id")
            if candidate_id:
                geometries[candidate_id] = feature["geometry"]
    return geometries


routes = [
    Route("/", index),
    Route("/about", about),
    Route("/api/scenarios", get_scenarios),
    Route("/api/scenarios/{scenario_id}", get_scenario),
    Route("/api/scenarios/{scenario_id}/start", start_run, methods=["POST"]),
    Route("/api/runs/{run_id}", get_run),
    Route("/api/runs/{run_id}/map", get_map),
    Route("/api/runs/{run_id}/trajectory", get_trajectory),
    Route("/api/runs/{run_id}/pause", pause_run, methods=["POST"]),
    Route("/api/runs/{run_id}/step", step_run, methods=["POST"]),
    Route("/api/runs/{run_id}/continue", continue_run, methods=["POST"]),
    Route("/api/runs/{run_id}/complete", complete_run, methods=["POST"]),
    Route("/api/runs/{run_id}/reset", reset_run, methods=["POST"]),
    Route("/api/runs/{run_id}/operator-decision", operator_decision, methods=["POST"]),
    Route("/scenario-images/{scenario_id}/{filename}", scenario_image),
    Mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static"),
]
