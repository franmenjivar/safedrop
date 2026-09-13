"""In-process run manager for the operations console.

Holds live runs, drives telemetry replay, and triggers the agentic workflow
once the replay reaches the scenario's decision step. Everything lives in
memory: one process, no broker, no database.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from app.config import SafeDropConfig, get_config
from app.models.enums import WorkflowState
from app.models.state import RunState
from app.orchestration.workflow import apply_operator_decision, run_workflow
from app.services.scenario_loader import ScenarioData, load_scenario_cached
from app.tools.state_preprocessing import build_operational_state, row_to_telemetry


@dataclass
class RunSession:
    run_id: str
    scenario: ScenarioData
    state: RunState
    cfg: SafeDropConfig
    task: asyncio.Task | None = None
    workflow_started: bool = False
    workflow_error: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, RunSession] = {}

    # -- lifecycle ---------------------------------------------------------

    def create(self, scenario_dir: str, cfg: SafeDropConfig | None = None) -> RunSession:
        cfg = cfg or get_config()
        scenario = load_scenario_cached(scenario_dir)
        run_id = f"{scenario.scenario.id}-{uuid.uuid4().hex[:8]}"
        state = RunState(
            run_id=run_id,
            scenario_id=scenario.scenario.id,
            telemetry_total=len(scenario.telemetry),
        )
        self._apply_telemetry(scenario, state, 0)
        session = RunSession(run_id=run_id, scenario=scenario, state=state, cfg=cfg)
        self._runs[run_id] = session
        return session

    def get(self, run_id: str) -> RunSession | None:
        return self._runs.get(run_id)

    def _apply_telemetry(self, scenario: ScenarioData, state: RunState, index: int) -> None:
        """Advance the replay to ``index`` and recompute the compressed state."""
        index = min(max(index, 0), len(scenario.telemetry) - 1)
        state.telemetry_index = index
        state.latest_telemetry = row_to_telemetry(scenario.telemetry.iloc[index])
        state.operational_state = build_operational_state(
            scenario.telemetry, index, scenario.scenario.mission
        )

    # -- playback ----------------------------------------------------------

    async def start(self, session: RunSession) -> None:
        if session.task and not session.task.done():
            session.state.playing = True
            return
        session.state.playing = True
        session.task = asyncio.create_task(self._play(session))

    def pause(self, session: RunSession) -> None:
        session.state.playing = False

    async def step(self, session: RunSession) -> None:
        session.state.playing = False
        await self._advance(session)

    async def run_to_completion(self, session: RunSession) -> None:
        """Jump straight to the decision point and run the workflow."""
        session.state.playing = False
        self._apply_telemetry(session.scenario, session.state, session.scenario.scenario.decision_step)
        await self._trigger_workflow(session)

    def reset(self, session: RunSession) -> RunSession:
        if session.task and not session.task.done():
            session.task.cancel()
        self._runs.pop(session.run_id, None)
        return self.create(session.scenario.scenario.directory.name, session.cfg)

    async def _play(self, session: RunSession) -> None:
        delay = session.cfg.simulation.playback_ms / 1000.0
        try:
            while True:
                if not session.state.playing:
                    await asyncio.sleep(0.1)
                    continue
                finished = await self._advance(session)
                if finished:
                    return
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

    async def _advance(self, session: RunSession) -> bool:
        """Advance one simulated second. Returns True when the run is done."""
        state = session.state
        decision_step = session.scenario.scenario.decision_step
        if state.telemetry_index >= decision_step:
            await self._trigger_workflow(session)
            state.playing = False
            return True
        self._apply_telemetry(session.scenario, state, state.telemetry_index + 1)
        if state.telemetry_index >= decision_step:
            await self._trigger_workflow(session)
            state.playing = False
            return True
        return False

    async def _trigger_workflow(self, session: RunSession) -> None:
        async with session.lock:
            if session.workflow_started:
                return
            session.workflow_started = True
        session.state.add_trace("orchestrator", "Decision point reached; running the SafeDrop workflow.")
        try:
            await run_workflow(
                session.scenario.scenario.directory.name,
                run_id=session.run_id,
                cfg=session.cfg,
                telemetry_index=session.scenario.scenario.decision_step,
                state=session.state,
                scenario=session.scenario,
            )
        except Exception as exc:  # the console must survive a failed run
            session.workflow_error = str(exc)
            session.state.workflow_state = WorkflowState.ESCALATED
            session.state.add_trace("orchestrator", f"Workflow error: {exc}")

    # -- operator ----------------------------------------------------------

    def operator_decision(self, session: RunSession, approve: bool) -> RunState:
        return apply_operator_decision(session.state, approve)


RUNS = RunManager()
