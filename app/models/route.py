"""Diversion routes and the deterministic checks applied to them."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import ReasonCode


class Waypoint(BaseModel):
    latitude: float
    longitude: float
    altitude_m: float


class Route(BaseModel):
    """A deterministically planned route. Agents request routes; they never invent waypoints."""

    route_id: str
    candidate_id: str
    variant: int = Field(default=1, description="1 = direct, >1 = replanned detour.")
    distance_m: float
    estimated_time_sec: float
    cruise_time_sec: float = Field(
        default=0.0,
        description="Horizontal transit time only; the descent is covered by the landing reserve.",
    )
    waypoints: list[Waypoint]
    strategy: str = "direct"


class RouteEnergyCheck(BaseModel):
    route_id: str
    remaining_energy_wh: float
    route_energy_wh: float
    reserve_energy_wh: float
    energy_margin_wh: float
    safe: bool


class ObstacleCheck(BaseModel):
    route_id: str
    intersecting_obstacles: list[str] = Field(default_factory=list)
    minimum_clearance_m: float | None = None
    safe: bool = True


class RestrictionCheck(BaseModel):
    route_id: str
    intersecting_restrictions: list[str] = Field(default_factory=list)
    safe: bool = True


class SeparationCheck(BaseModel):
    route_id: str
    conflicting_vehicle_id: str | None = None
    minimum_separation_m: float | None = None
    required_separation_m: float
    safe: bool = False
    data_available: bool = True
    reason_codes: list[ReasonCode] = Field(default_factory=list)


class DescentCorridorCheck(BaseModel):
    candidate_id: str
    corridor_radius_m: float
    obstructions: list[str] = Field(default_factory=list)
    safe: bool = True


class RouteEvaluation(BaseModel):
    """Everything known about one attempted route to one candidate."""

    route: Route
    energy: RouteEnergyCheck | None = None
    obstacles: ObstacleCheck | None = None
    restrictions: RestrictionCheck | None = None
    separation: SeparationCheck | None = None
    descent: DescentCorridorCheck | None = None
    passed: bool = False
    reason_codes: list[ReasonCode] = Field(default_factory=list)


class DiversionVerificationResult(BaseModel):
    """Structured output of Agent 3."""

    selected_candidate_id: str | None = None
    selected_route_id: str | None = None
    verified: bool = False
    attempted_route_ids: list[str] = Field(default_factory=list)
    replan_count: int = 0
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    rationale: str = ""
