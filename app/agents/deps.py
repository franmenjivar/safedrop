"""Shared dependency object handed to every agent's tools.

Tools reach the scenario fixtures, the config, the mutable run state, and the
trajectory logger through this object. Agents themselves get none of it
directly — they only see the structured prompt and the tool results.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import SafeDropConfig
from app.models.state import RunState
from app.services.scenario_loader import ScenarioData
from app.services.trajectory_logger import TrajectoryLogger


@dataclass
class AgentDeps:
    cfg: SafeDropConfig
    scenario: ScenarioData
    state: RunState
    logger: TrajectoryLogger
