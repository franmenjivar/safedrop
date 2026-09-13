"""Raw telemetry rows and the compressed operational state derived from them.

The distinction matters: agents never see :class:`TelemetryRow`. They see
:class:`OperationalState`, produced by a deterministic preprocessor. That
separation is one of the project's core experiments (overview.md §30).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import Severity, SubsystemState


class TelemetryRow(BaseModel):
    """One simulated second of raw aircraft telemetry, read from scenario CSV.

    The channels below the fold are optional so older scenario CSVs still load;
    they carry the evidence for the propulsion, navigation and power failure
    families that battery state alone cannot express.
    """

    timestamp_s: float
    latitude: float
    longitude: float
    altitude_m: float
    battery_soc: float
    battery_temp_c: float
    battery_drop_rate_pct_min: float
    motor_health: float
    airspeed_mps: float
    payload_kg: float
    wind_speed_mps: float
    wind_direction_deg: float
    link_quality: float

    #: Measured electrical draw. Diverges from the expected draw when a
    #: propeller is damaged or a payload has shifted.
    power_draw_w: float | None = None
    #: Airframe vibration, RMS g. Rises with propeller damage and payload slosh.
    vibration_g: float | None = None
    #: ESC temperature, a propulsion-side thermal channel distinct from the pack.
    esc_temp_c: float | None = None
    #: GNSS quality.
    gps_satellites: int | None = None
    gps_hdop: float | None = None
    #: Disagreement between magnetic heading and GPS ground course. Large,
    #: sustained values mean the aircraft does not agree with itself.
    heading_disagreement_deg: float | None = None
    #: Wind gust peak over the sampling second.
    wind_gust_mps: float | None = None


class OperationalState(BaseModel):
    """Compressed, agent-facing operational state.

    Every field here is either measured or computed deterministically. The
    agent is expected to reason over these labels, not to re-derive them.
    """

    timestamp_s: float
    latitude: float
    longitude: float
    altitude_m: float

    battery_state: SubsystemState
    battery_soc: float
    battery_drop_rate_pct_min: float
    battery_temperature_c: float

    motor_state: SubsystemState
    motor_health: float

    #: Airframe vibration, the propeller-damage and payload-instability channel.
    vibration_state: SubsystemState = SubsystemState.UNKNOWN
    vibration_g: float | None = None
    esc_temp_c: float | None = None

    #: Measured draw against what this payload and motor health should require.
    power_state: SubsystemState = SubsystemState.UNKNOWN
    power_draw_w: float | None = None
    power_excess_ratio: float | None = None

    navigation_state: SubsystemState
    gps_satellites: int | None = None
    gps_hdop: float | None = None

    #: Do the aircraft's own sensors agree with each other?
    sensor_agreement_state: SubsystemState = SubsystemState.UNKNOWN
    heading_disagreement_deg: float | None = None

    communication_state: SubsystemState
    link_quality: float

    airspeed_mps: float
    payload_kg: float
    wind_speed_mps: float
    wind_gust_mps: float | None = None
    wind_direction_deg: float
    wind_risk: Severity

    distance_to_base_m: float
    distance_to_destination_m: float
    distance_to_hub_m: float | None = None

    trend_window_sec: float = Field(
        default=0.0,
        description="Seconds of telemetry history summarised into this state.",
    )
    notes: list[str] = Field(default_factory=list)
