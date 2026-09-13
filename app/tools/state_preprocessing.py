"""Deterministic telemetry compression.

Raw telemetry never reaches an agent. This module turns a window of CSV rows
into a single :class:`OperationalState` with categorical subsystem labels and
trend rates. Compressing here rather than in the prompt is the architecture's
central bet (overview.md §30-31), and it is directly measurable: the evaluation
harness can run the same agents against raw rows for comparison.
"""

from __future__ import annotations

import math

import pandas as pd

from app.models.enums import Severity, SubsystemState
from app.models.scenario import MissionState
from app.models.telemetry import OperationalState, TelemetryRow
from app.tools.geometry import haversine_m

TREND_WINDOW_ROWS = 20

# Thresholds are prototype experiment parameters, not certification limits.
BATTERY_SOC_CRITICAL = 15.0
BATTERY_SOC_DEGRADED = 30.0
BATTERY_DROP_CRITICAL = 3.0
BATTERY_DROP_DEGRADED = 1.8
BATTERY_TEMP_CRITICAL = 65.0
BATTERY_TEMP_DEGRADED = 55.0

MOTOR_HEALTH_CRITICAL = 0.60
MOTOR_HEALTH_DEGRADED = 0.85

LINK_CRITICAL = 0.30
LINK_DEGRADED = 0.70

NAV_JITTER_CRITICAL_M = 12.0
NAV_JITTER_DEGRADED_M = 5.0

VIBRATION_CRITICAL_G = 1.10
VIBRATION_DEGRADED_G = 0.55
ESC_TEMP_CRITICAL_C = 95.0
ESC_TEMP_DEGRADED_C = 78.0

GPS_SATS_CRITICAL = 5
GPS_SATS_DEGRADED = 8
GPS_HDOP_CRITICAL = 4.0
GPS_HDOP_DEGRADED = 2.0

HEADING_DISAGREE_CRITICAL_DEG = 25.0
HEADING_DISAGREE_DEGRADED_DEG = 12.0

#: Measured draw over the expected draw for this payload and motor health.
POWER_EXCESS_CRITICAL = 1.35
POWER_EXCESS_DEGRADED = 1.15


def row_to_telemetry(row: pd.Series) -> TelemetryRow:
    """Build a telemetry row, dropping absent optional channels.

    A scenario that does not carry, say, ``power_draw_w`` leaves the column
    empty, and pandas reads that as NaN rather than None. NaN is not valid
    JSON, so it has to become None here — at the boundary — or it propagates
    all the way to the console and 500s the snapshot endpoint.
    """
    values = {k: v for k, v in row.to_dict().items() if not pd.isna(v)}
    return TelemetryRow.model_validate(values)


def _battery_state(soc: float, drop_rate: float, temp_c: float) -> SubsystemState:
    if soc <= BATTERY_SOC_CRITICAL or temp_c >= BATTERY_TEMP_CRITICAL:
        return SubsystemState.CRITICAL
    if soc <= BATTERY_SOC_DEGRADED and drop_rate >= BATTERY_DROP_CRITICAL:
        return SubsystemState.CRITICAL
    if (
        soc <= BATTERY_SOC_DEGRADED
        or drop_rate >= BATTERY_DROP_DEGRADED
        or temp_c >= BATTERY_TEMP_DEGRADED
    ):
        return SubsystemState.DEGRADED
    return SubsystemState.NOMINAL


def _motor_state(health: float) -> SubsystemState:
    if health < MOTOR_HEALTH_CRITICAL:
        return SubsystemState.CRITICAL
    if health < MOTOR_HEALTH_DEGRADED:
        return SubsystemState.DEGRADED
    return SubsystemState.NOMINAL


def _link_state(quality: float) -> SubsystemState:
    if quality < LINK_CRITICAL:
        return SubsystemState.CRITICAL
    if quality < LINK_DEGRADED:
        return SubsystemState.DEGRADED
    return SubsystemState.NOMINAL


def _position_jitter_m(window: pd.DataFrame) -> float:
    """RMS deviation of reported positions from a straight-line fit.

    A clean GPS track over a few seconds is nearly linear; navigation
    degradation shows up as scatter around that line.
    """
    if len(window) < 4:
        return 0.0
    lat0, lon0 = float(window.iloc[0]["latitude"]), float(window.iloc[0]["longitude"])
    latn, lonn = float(window.iloc[-1]["latitude"]), float(window.iloc[-1]["longitude"])
    span = haversine_m(lat0, lon0, latn, lonn)
    if span < 1.0:
        return 0.0
    residuals = []
    n = len(window) - 1
    for i, (_, row) in enumerate(window.iterrows()):
        expected_lat = lat0 + (latn - lat0) * i / n
        expected_lon = lon0 + (lonn - lon0) * i / n
        residuals.append(
            haversine_m(expected_lat, expected_lon, float(row["latitude"]), float(row["longitude"]))
        )
    return math.sqrt(sum(r * r for r in residuals) / len(residuals))


def _navigation_state_from_jitter(jitter_m: float) -> SubsystemState:
    if jitter_m >= NAV_JITTER_CRITICAL_M:
        return SubsystemState.CRITICAL
    if jitter_m >= NAV_JITTER_DEGRADED_M:
        return SubsystemState.DEGRADED
    return SubsystemState.NOMINAL


def _vibration_state(vibration_g: float | None, esc_temp_c: float | None) -> SubsystemState:
    """Airframe vibration and ESC heat: the propeller-damage channel.

    A chipped or fouled propeller shows up as vibration long before motor
    health degrades, and drives ESC temperature up as the controller fights it.
    """
    if vibration_g is None and esc_temp_c is None:
        return SubsystemState.UNKNOWN
    if (vibration_g is not None and vibration_g >= VIBRATION_CRITICAL_G) or (
        esc_temp_c is not None and esc_temp_c >= ESC_TEMP_CRITICAL_C
    ):
        return SubsystemState.CRITICAL
    if (vibration_g is not None and vibration_g >= VIBRATION_DEGRADED_G) or (
        esc_temp_c is not None and esc_temp_c >= ESC_TEMP_DEGRADED_C
    ):
        return SubsystemState.DEGRADED
    return SubsystemState.NOMINAL


def _gnss_state(satellites: int | None, hdop: float | None, jitter_m: float) -> SubsystemState:
    """Navigation quality from constellation geometry and observed scatter."""
    states = [_navigation_state_from_jitter(jitter_m)]
    if satellites is not None:
        states.append(
            SubsystemState.CRITICAL
            if satellites <= GPS_SATS_CRITICAL
            else SubsystemState.DEGRADED
            if satellites <= GPS_SATS_DEGRADED
            else SubsystemState.NOMINAL
        )
    if hdop is not None:
        states.append(
            SubsystemState.CRITICAL
            if hdop >= GPS_HDOP_CRITICAL
            else SubsystemState.DEGRADED
            if hdop >= GPS_HDOP_DEGRADED
            else SubsystemState.NOMINAL
        )
    order = [SubsystemState.NOMINAL, SubsystemState.DEGRADED, SubsystemState.CRITICAL]
    return max(states, key=order.index)


def _sensor_agreement_state(disagreement_deg: float | None) -> SubsystemState:
    """Do the aircraft's own sensors agree about where it is pointing?

    Sustained magnetic-versus-GNSS-course disagreement means at least one
    source is wrong and there is no onboard way to tell which. That is an
    escalation, not a landing decision.
    """
    if disagreement_deg is None:
        return SubsystemState.UNKNOWN
    if disagreement_deg >= HEADING_DISAGREE_CRITICAL_DEG:
        return SubsystemState.CRITICAL
    if disagreement_deg >= HEADING_DISAGREE_DEGRADED_DEG:
        return SubsystemState.DEGRADED
    return SubsystemState.NOMINAL


def _power_state(
    measured_w: float | None, expected_w: float
) -> tuple[SubsystemState, float | None]:
    """Measured draw against what this configuration should require.

    A shifted payload, a fouled propeller, or a failing cell all show up here
    before they show up anywhere else.
    """
    if measured_w is None or expected_w <= 0:
        return SubsystemState.UNKNOWN, None
    ratio = measured_w / expected_w
    if ratio >= POWER_EXCESS_CRITICAL:
        return SubsystemState.CRITICAL, ratio
    if ratio >= POWER_EXCESS_DEGRADED:
        return SubsystemState.DEGRADED, ratio
    return SubsystemState.NOMINAL, ratio


def _wind_risk(wind_speed: float, airspeed: float) -> Severity:
    ratio = wind_speed / max(airspeed, 1.0)
    if ratio >= 0.65:
        return Severity.CRITICAL
    if ratio >= 0.45:
        return Severity.WARNING
    if ratio >= 0.30:
        return Severity.CAUTION
    return Severity.NOMINAL


def build_operational_state(
    telemetry: pd.DataFrame,
    index: int,
    mission: MissionState,
    window_rows: int = TREND_WINDOW_ROWS,
) -> OperationalState:
    """Compress telemetry rows ``[index-window, index]`` into agent-facing state."""
    index = min(max(index, 0), len(telemetry) - 1)
    start = max(0, index - window_rows + 1)
    window = telemetry.iloc[start : index + 1]
    current = telemetry.iloc[index]

    soc = float(current["battery_soc"])
    temp = float(current["battery_temp_c"])
    motor_health = float(current["motor_health"])
    link = float(current["link_quality"])
    airspeed = float(current["airspeed_mps"])
    wind = float(current["wind_speed_mps"])

    # Prefer a measured trend over the reported instantaneous rate when we have history.
    if len(window) >= 2:
        dt_min = (
            float(window.iloc[-1]["timestamp_s"]) - float(window.iloc[0]["timestamp_s"])
        ) / 60.0
        drop_rate = (
            (float(window.iloc[0]["battery_soc"]) - soc) / dt_min if dt_min > 0 else 0.0
        )
    else:
        drop_rate = float(current["battery_drop_rate_pct_min"])
    drop_rate = max(drop_rate, 0.0)

    jitter = _position_jitter_m(window)
    lat, lon = float(current["latitude"]), float(current["longitude"])

    def optional(name: str):
        """Newer sensor channels are absent from older scenario CSVs."""
        if name not in current.index:
            return None
        value = current[name]
        return None if pd.isna(value) else float(value)

    vibration = optional("vibration_g")
    esc_temp = optional("esc_temp_c")
    hdop = optional("gps_hdop")
    sats = optional("gps_satellites")
    sats = None if sats is None else int(sats)
    disagreement = optional("heading_disagreement_deg")
    measured_power = optional("power_draw_w")
    gust = optional("wind_gust_mps")

    # Expected draw for this payload and motor health, from the same model the
    # energy engine uses — so "abnormal" means abnormal against our own physics.
    from app.config import get_config
    from app.tools.energy import estimated_power_w

    expected_power = estimated_power_w(
        float(current["payload_kg"]), motor_health, get_config().energy
    )
    power_state, power_ratio = _power_state(measured_power, expected_power)
    vibration_state = _vibration_state(vibration, esc_temp)
    navigation_state = _gnss_state(sats, hdop, jitter)
    agreement_state = _sensor_agreement_state(disagreement)

    notes: list[str] = []
    if drop_rate >= BATTERY_DROP_CRITICAL:
        notes.append(f"State of charge falling {drop_rate:.1f}%/min over the last window.")
    if motor_health < MOTOR_HEALTH_DEGRADED:
        notes.append(f"Motor health index {motor_health:.2f}.")
    if jitter >= NAV_JITTER_DEGRADED_M:
        notes.append(f"GPS track scatter {jitter:.1f} m RMS.")
    if temp >= BATTERY_TEMP_DEGRADED:
        notes.append(f"Pack temperature {temp:.0f} C.")
    if vibration is not None and vibration >= VIBRATION_DEGRADED_G:
        notes.append(f"Airframe vibration {vibration:.2f} g RMS.")
    if esc_temp is not None and esc_temp >= ESC_TEMP_DEGRADED_C:
        notes.append(f"ESC temperature {esc_temp:.0f} C.")
    if power_ratio is not None and power_ratio >= POWER_EXCESS_DEGRADED:
        notes.append(
            f"Drawing {measured_power:.0f} W against an expected {expected_power:.0f} W "
            f"({power_ratio:.2f}x)."
        )
    if sats is not None and sats <= GPS_SATS_DEGRADED:
        notes.append(f"GNSS {sats} satellites, HDOP {hdop:.1f}." if hdop else f"GNSS {sats} satellites.")
    if disagreement is not None and disagreement >= HEADING_DISAGREE_DEGRADED_DEG:
        notes.append(
            f"Magnetic heading and GNSS ground course disagree by {disagreement:.0f} deg — "
            "at least one attitude source is wrong."
        )
    if gust is not None and gust >= wind + 4.0:
        notes.append(f"Gusting to {gust:.0f} m/s against a {wind:.0f} m/s mean.")

    return OperationalState(
        timestamp_s=float(current["timestamp_s"]),
        latitude=lat,
        longitude=lon,
        altitude_m=float(current["altitude_m"]),
        battery_state=_battery_state(soc, drop_rate, temp),
        battery_soc=round(soc, 1),
        battery_drop_rate_pct_min=round(drop_rate, 2),
        battery_temperature_c=round(temp, 1),
        motor_state=_motor_state(motor_health),
        motor_health=round(motor_health, 3),
        vibration_state=vibration_state,
        vibration_g=None if vibration is None else round(vibration, 2),
        esc_temp_c=None if esc_temp is None else round(esc_temp, 1),
        power_state=power_state,
        power_draw_w=None if measured_power is None else round(measured_power, 0),
        power_excess_ratio=None if power_ratio is None else round(power_ratio, 2),
        navigation_state=navigation_state,
        gps_satellites=sats,
        gps_hdop=None if hdop is None else round(hdop, 2),
        sensor_agreement_state=agreement_state,
        heading_disagreement_deg=None if disagreement is None else round(disagreement, 1),
        communication_state=_link_state(link),
        link_quality=round(link, 2),
        airspeed_mps=round(airspeed, 1),
        payload_kg=float(current["payload_kg"]),
        wind_speed_mps=round(wind, 1),
        wind_gust_mps=None if gust is None else round(gust, 1),
        wind_direction_deg=round(float(current["wind_direction_deg"]), 1),
        wind_risk=_wind_risk(wind, airspeed),
        distance_to_base_m=round(
            haversine_m(lat, lon, mission.base.latitude, mission.base.longitude), 1
        ),
        distance_to_destination_m=round(
            haversine_m(lat, lon, mission.destination.latitude, mission.destination.longitude), 1
        ),
        distance_to_hub_m=round(
            haversine_m(
                lat, lon, mission.alternate_hub.latitude, mission.alternate_hub.longitude
            ),
            1,
        )
        if mission.alternate_hub
        else None,
        trend_window_sec=float(current["timestamp_s"]) - float(window.iloc[0]["timestamp_s"]),
        notes=notes,
    )
