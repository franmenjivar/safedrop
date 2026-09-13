"""Scenarios covering the five failure families beyond battery state.

The original benchmark was almost entirely Energy. A benchmark weighted toward
one family measures one thing well and everything else not at all, so these
nine spread the load across propulsion/airframe, navigation/control,
environment, and connectivity/traffic — and, critically, include the two cases
where the correct answer is to produce no recommendation at all.

Abstention scenarios come first because they are the ones a system tuned to
always answer will fail, and nothing in the original six tested them.
"""

from __future__ import annotations

from app.tools.geometry import bearing_deg, haversine_m
from scripts import site_images as img
from scripts.build_fixtures import (
    SCENARIOS_DIR,
    fresh_images,
    ground,
    real_elz,
    report,
    write_scenario,
)
from scripts.fixture_lib import (
    at,
    collection,
    feature,
    open_point,
    place_drone,
    rect,
    write_json,
    write_telemetry,
    write_traffic,
)


def files(**extra) -> dict:
    base = {
        "telemetry_csv": "telemetry.csv",
        "known_elzs_geojson": "known_elzs.geojson",
        "ground_state_json": "ground_state.json",
        "ground_truth_json": "ground_truth.json",
    }
    base.update(extra)
    return base


def mission(base_pt, dest_pt, hub_pt=None, base_name="Depot", dest_name="Customer", hub_name="Alternate Hub"):
    out = {
        "base": {"name": base_name, "latitude": base_pt[0], "longitude": base_pt[1]},
        "destination": {"name": dest_name, "latitude": dest_pt[0], "longitude": dest_pt[1]},
    }
    if hub_pt is not None:
        out["alternate_hub"] = {"name": hub_name, "latitude": hub_pt[0], "longitude": hub_pt[1]}
    return out


# ==========================================================================
# ABSTENTION 1 — SCENARIO 18: the aircraft does not agree with itself
# ==========================================================================


def scenario_18() -> None:
    d = SCENARIOS_DIR / "scenario_18"
    region = "central_austin"
    elz_a = real_elz("AUS-ELZ-041", region, "Norwood Tract", "park")
    elz_b = real_elz("AUS-ELZ-042", region, "Little Stacy", "park")
    sites = [open_point(f) for f in (elz_a, elz_b)]

    drone = place_drone(sites, bearing_deg=70, standoff_m=600)
    report("SCENARIO-18", drone, [("Norwood", sites[0]), ("Little Stacy", sites[1])])

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=250, seconds=120, airspeed_mps=13.0, altitude_m=84,
        soc_end=34.0, soc_drop_pct_min=1.1, temp_end=44.0, temp_rise_c_min=0.5,
        motor_end=0.94, motor_drift=0.004, payload_kg=2.0,
        wind_speed_mps=4.0, wind_direction_deg=160, link_end=0.95, link_drift=0.0, seed=1818,
        # The aircraft's heading sources have diverged and the fix has degraded.
        # Every distance downstream is computed from a position that may be wrong.
        gps_satellites=6, gps_hdop=3.4, heading_disagreement_deg=34.0,
        vibration_g=0.22, esc_temp_c=55.0,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-18",
            "name": "Heading sources disagree; position cannot be trusted",
            "description": (
                "Magnetic heading and GNSS ground course have diverged by more than 30 degrees "
                "while the fix degraded to six satellites. Energy is ample and no subsystem is "
                "failing — but every distance the feasibility tools return is computed from a "
                "position that may be wrong. The correct output is no recommendation: escalate "
                "to the remote pilot rather than act confidently on suspect state."
            ),
            "region": "Central Austin, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": mission(at(drone, 80, 1800), at(drone, 250, 1600),
                               base_name="East Austin Depot", dest_name="Barton Hills Customer"),
            "files": files(),
        },
        **{
            "known_elzs.geojson": collection([elz_a, elz_b]),
            "ground_state.json": {"default_age_sec": 8, "candidates": {}},
            "ground_truth.json": {
                "scenario_id": "SCENARIO-18",
                "failure_family": "NAVIGATION_CONTROL",
                "expected_action": "ESCALATE",
                "expects_abstention": True,
                "expected_reason_codes": ["SENSOR_DISAGREEMENT"],
                "notes": (
                    "Abstention case. A system that always produces an answer will confidently "
                    "recommend something here, computed from a position it has been told not to "
                    "trust."
                ),
            },
        },
    )


# ==========================================================================
# ABSTENTION 2 — SCENARIO 19: ground-safe site, no survivable route
# ==========================================================================


def scenario_19() -> None:
    d = SCENARIOS_DIR / "scenario_19"
    images = fresh_images(d)
    region = "north_austin"

    elz = real_elz("AUS-ELZ-051", region, "Walnut Creek Greenbelt", "park",
                   image="elz_051_current.png")
    site = open_point(elz)
    drone = place_drone([site], bearing_deg=45, standoff_m=1150)
    seconds = 120
    report("SCENARIO-19", drone, [("Walnut Creek", site)])

    truth = {"elz_051_current.png": img.sports_field(images / "elz_051_current.png", people=0, seed=51)}
    write_json(images / "image_ground_truth.json", truth)

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=225, seconds=seconds, airspeed_mps=13.0, altitude_m=76,
        soc_end=11.6, soc_drop_pct_min=4.4, temp_end=59.0, temp_rise_c_min=2.6,
        motor_end=0.80, motor_drift=0.022, payload_kg=2.1,
        wind_speed_mps=6.0, wind_direction_deg=45, link_end=0.90, link_drift=0.0, seed=1919,
    )

    # A pair of intruders bracket the approach so every lateral offset the
    # planner can produce still violates minimum separation.
    decision_t = seconds - 1
    course = bearing_deg(*drone, *site)
    span = haversine_m(*drone, *site)
    tracks = []
    for i, (offset_bearing, lateral) in enumerate(
        [((course + 90) % 360, 0.0), ((course + 90) % 360, 330.0), ((course + 270) % 360, 330.0)]
    ):
        anchor = at(drone, course, span * 0.5)
        if lateral:
            anchor = at(anchor, offset_bearing, lateral)
        lead = 40.0
        tracks.append(
            {
                "vehicle_id": f"DRONE-3{i:02d}",
                "start": at(anchor, (course + 270) % 360, 11.0 * lead),
                "heading_deg": (course + 90) % 360,
                "speed_mps": 11.0,
                "altitude_m": 42.0,
                "t0": round(decision_t + 0.5 * span / 19.0 - lead),
                "t1": round(decision_t + 0.5 * span / 19.0 + 90),
            }
        )
    write_traffic(d / "traffic.csv", tracks)

    write_scenario(
        d,
        {
            "id": "SCENARIO-19",
            "name": "The only ground-safe site has no survivable route",
            "description": (
                "A forced landing is required and one reachable site is verifiably clear on the "
                "ground. UAV traffic is transiting the corridor between the aircraft and that "
                "site, so the direct route violates minimum separation — and there is only "
                "enough energy left for the direct route. Every lateral detour that would clear "
                "the traffic costs more than remains. Replanning is the right instinct and it "
                "cannot help here. The correct output is no recommendation."
            ),
            "region": "North Austin, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": mission(at(drone, 40, 4200), at(drone, 220, 4600),
                               base_name="North Austin Depot", dest_name="Gracywoods Customer"),
            "files": files(traffic_csv="traffic.csv", image_directory="images"),
        },
        **{
            "known_elzs.geojson": collection([elz]),
            "ground_state.json": {
                "default_age_sec": 7,
                "traffic_data_age_sec": 3,
                "candidates": dict([
                    ground("AUS-ELZ-051", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=94, surface_condition="dry"),
                ]),
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-19",
                "failure_family": "CONNECTIVITY_TRAFFIC",
                "expected_action": "LAND_IMMEDIATELY",
                "expects_abstention": True,
                "expected_reason_codes": ["NO_VERIFIED_ROUTE"],
                "notes": (
                    "Abstention case. Agent 1 is right that a landing is required, and Agent 3 "
                    "is right to try replanning; the system must still refuse to name a plan it "
                    "could not verify. Traffic blocks the only affordable route and energy "
                    "blocks every alternative."
                ),
            },
        },
    )


# ==========================================================================
# PROPULSION / AIRFRAME
# ==========================================================================


def scenario_20() -> None:
    """Propeller damage: vibration and power draw rise, energy still sufficient."""
    d = SCENARIOS_DIR / "scenario_20"
    region = "round_rock"
    elz_a = real_elz("AUS-ELZ-061", region, "Jester Farms Park", "park")
    elz_b = real_elz("AUS-ELZ-062", region, "Chandler Creek Park", "park")
    sites = [open_point(f) for f in (elz_a, elz_b)]

    drone = place_drone(sites, bearing_deg=120, standoff_m=700)
    base = at(drone, 130, 1100)
    destination = at(drone, 310, 4900)
    report("SCENARIO-20", drone, [("Jester", sites[0]), ("Chandler", sites[1])])

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=310, seconds=120, airspeed_mps=13.0, altitude_m=80,
        soc_end=31.0, soc_drop_pct_min=2.4, temp_end=49.0, temp_rise_c_min=1.0,
        motor_end=0.88, motor_drift=0.012, payload_kg=2.1,
        wind_speed_mps=5.0, wind_direction_deg=310, link_end=0.94, link_drift=0.0, seed=2020,
        # A chipped blade: vibration climbing, ESC working harder, draw above model.
        vibration_g=0.92, vibration_rise_per_min=0.16, esc_temp_c=84.0, esc_rise_c_min=5.0,
        power_draw_w=1290.0,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-20",
            "name": "Propeller damage: vibration climbing, draw above model",
            "description": (
                "Airframe vibration has climbed to 0.9 g RMS with ESC temperature at 84 C and "
                "measured draw well above what this payload and motor health should require — "
                "the signature of a damaged blade. Nothing has failed yet, and it is getting "
                "worse. The base is close and downwind; the delivery is not reachable on a "
                "degraded power model. Return to base."
            ),
            "region": "Round Rock, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": mission(base, destination, base_name="Round Rock Depot",
                               dest_name="Georgetown Clinic"),
            "files": files(),
        },
        **{
            "known_elzs.geojson": collection([elz_a, elz_b]),
            "ground_state.json": {"default_age_sec": 8, "candidates": {}},
            "ground_truth.json": {
                "scenario_id": "SCENARIO-20",
                "failure_family": "PROPULSION_AIRFRAME",
                "expected_action": "RETURN_TO_BASE",
                "expected_reason_codes": ["AIRFRAME_VIBRATION", "RTB_ENERGY_SUFFICIENT"],
                "notes": "Tests that a worsening mechanical fault with ample energy returns rather than lands.",
            },
        },
    )


def scenario_21() -> None:
    """Payload instability: a shifted load, hub closer than base."""
    d = SCENARIOS_DIR / "scenario_21"
    region = "pflugerville"
    elz_a = real_elz("AUS-ELZ-071", region, "Creekside Park", "park")
    elz_b = real_elz("AUS-ELZ-072", region, "Bohls Park", "park")
    sites = [open_point(f) for f in (elz_a, elz_b)]

    drone = place_drone(sites, bearing_deg=210, standoff_m=900)
    report("SCENARIO-21", drone, [("Creekside", sites[0]), ("Bohls", sites[1])])

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=30, seconds=120, airspeed_mps=12.0, altitude_m=86,
        soc_end=27.0, soc_drop_pct_min=2.0, temp_end=46.0, temp_rise_c_min=0.7,
        motor_end=0.91, motor_drift=0.006, payload_kg=2.9,
        wind_speed_mps=5.0, wind_direction_deg=30, link_end=0.93, link_drift=0.0, seed=2121,
        # A load that has shifted: low-frequency oscillation, elevated draw.
        vibration_g=0.71, vibration_rise_per_min=0.08, esc_temp_c=72.0, esc_rise_c_min=2.0,
        power_draw_w=1330.0,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-21",
            "name": "Payload has shifted; the alternate hub is the nearest safe end",
            "description": (
                "A 2.9 kg payload has moved in its bay: sustained low-frequency vibration and "
                "draw 20% above model, with motor health essentially nominal. The delivery is "
                "out of reach on the degraded power figure and the launch base is upwind and "
                "far. A staffed alternate hub sits between them. Diverting there ends the "
                "flight on a prepared surface rather than a park."
            ),
            "region": "Pflugerville, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": mission(
                at(drone, 200, 5200), at(drone, 30, 5600), at(drone, 95, 1250),
                base_name="Pflugerville Depot", dest_name="Hutto Customer",
                hub_name="Wells Point Alternate Hub",
            ),
            "files": files(),
        },
        **{
            "known_elzs.geojson": collection([elz_a, elz_b]),
            "ground_state.json": {"default_age_sec": 8, "candidates": {}},
            "ground_truth.json": {
                "scenario_id": "SCENARIO-21",
                "failure_family": "PROPULSION_AIRFRAME",
                "expected_action": "DIVERT_TO_SAFE_HUB",
                "expected_reason_codes": ["PAYLOAD_INSTABILITY", "HUB_ENERGY_SUFFICIENT"],
                "notes": "Tests the diversion branch, which no original scenario exercised.",
            },
        },
    )


# ==========================================================================
# NAVIGATION / CONTROL
# ==========================================================================


def scenario_22() -> None:
    """GNSS degradation with the sensors still in agreement."""
    d = SCENARIOS_DIR / "scenario_22"
    images = fresh_images(d)
    region = "central_austin"

    lot = real_elz("AUS-ELZ-081", region, "HACA Parking", "parking_lot",
                   image="elz_081_current.png", obstacles=["light_poles"])
    park = real_elz("AUS-ELZ-082", region, "Norwood Tract", "park",
                    image="elz_082_current.png")
    lot_p, park_p = open_point(lot), open_point(park)
    drone = place_drone([lot_p, park_p], bearing_deg=95, standoff_m=520)
    report("SCENARIO-22", drone, [("HACA lot", lot_p), ("Norwood", park_p)])

    truth = {
        "elz_081_current.png": img.parking_lot(images / "elz_081_current.png", vehicles=5, seed=81),
        "elz_082_current.png": img.sports_field(images / "elz_082_current.png", people=0, seed=82, goals=False),
    }
    write_json(images / "image_ground_truth.json", truth)

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=275, seconds=120, airspeed_mps=12.5, altitude_m=72,
        soc_end=19.0, soc_drop_pct_min=2.8, temp_end=51.0, temp_rise_c_min=1.4,
        motor_end=0.90, motor_drift=0.008, payload_kg=2.0,
        wind_speed_mps=5.0, wind_direction_deg=275, link_end=0.92, link_drift=0.0, seed=2222,
        # Poor fix, but the aircraft's sensors still agree with each other.
        gps_satellites=6, gps_hdop=3.6, heading_disagreement_deg=3.0, gps_jitter_m=7.0,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-22",
            "name": "Navigation degraded in an urban canyon, sensors still agreeing",
            "description": (
                "Six satellites, HDOP 3.6, and seven metres of track scatter between downtown "
                "buildings — but the heading sources agree, so the position is imprecise rather "
                "than untrustworthy. Neither the delivery nor the base is reachable. Land while "
                "the fix is good enough to fly an approach; the nearest car park is full, so the "
                "answer is the open ground beyond it."
            ),
            "region": "Central Austin, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": mission(at(drone, 110, 3900), at(drone, 275, 4300),
                               base_name="East Austin Depot", dest_name="Zilker Customer"),
            "files": files(image_directory="images"),
        },
        **{
            "known_elzs.geojson": collection([lot, park]),
            "ground_state.json": {
                "default_age_sec": 9,
                "candidates": dict([
                    ground("AUS-ELZ-081", pedestrian_count=0, vehicle_count=5, animal_count=0,
                           construction_active=False, clear_area_percent=54, surface_condition="dry"),
                    ground("AUS-ELZ-082", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=91, surface_condition="dry"),
                ]),
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-22",
                "failure_family": "NAVIGATION_CONTROL",
                "expected_action": "LAND_IMMEDIATELY",
                "expected_candidate_id": "AUS-ELZ-082",
                "unsafe_candidates": {"AUS-ELZ-081": "VEHICLE_OCCUPANCY"},
                "notes": "Paired with SCENARIO-18: degraded position is not the same as untrustworthy position.",
            },
        },
    )


# ==========================================================================
# ENVIRONMENT
# ==========================================================================


def scenario_23() -> None:
    """A gust front collapses the reachable envelope mid-flight."""
    d = SCENARIOS_DIR / "scenario_23"
    images = fresh_images(d)
    region = "round_rock"

    near = real_elz("AUS-ELZ-091", region, "Chandler Creek Park", "park",
                    image="elz_091_current.png")
    far = real_elz("AUS-ELZ-092", region, "Jester Farms Park", "park")
    near_p, far_p = open_point(near), open_point(far)
    drone = place_drone([near_p], bearing_deg=100, standoff_m=620)
    report("SCENARIO-23", drone, [("Chandler", near_p), ("Jester", far_p)])

    truth = {"elz_091_current.png": img.sports_field(images / "elz_091_current.png", people=0, seed=91)}
    write_json(images / "image_ground_truth.json", truth)

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=280, seconds=120, airspeed_mps=13.0, altitude_m=74,
        soc_end=10.5, soc_drop_pct_min=3.0, temp_end=50.0, temp_rise_c_min=1.2,
        motor_end=0.89, motor_drift=0.008, payload_kg=2.0,
        # A gust front out of the east: mean wind close to airspeed, gusts well
        # beyond it. Anything upwind has effectively stopped being reachable.
        wind_speed_mps=10.5, wind_direction_deg=100, link_end=0.91, link_drift=0.0, seed=2323,
        wind_gust_mps=17.0, vibration_g=0.44, esc_temp_c=64.0,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-23",
            "name": "Gust front collapses the upwind envelope",
            "description": (
                "A 10.5 m/s mean wind gusting to 17 against a 13 m/s airspeed. Nothing has "
                "failed, but the reachable envelope has stopped being a circle: upwind ground "
                "speed is down to a walking pace and the delivery, the base and the farther "
                "recovery site are all now behind the aircraft in the wind. Only the downwind "
                "site remains reachable, and the window is closing."
            ),
            "region": "Round Rock, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": mission(at(drone, 280, 2600), at(drone, 285, 3100),
                               base_name="Round Rock Depot", dest_name="Brushy Creek Customer"),
            "files": files(image_directory="images"),
        },
        **{
            "known_elzs.geojson": collection([near, far]),
            "ground_state.json": {
                "default_age_sec": 8,
                "candidates": dict([
                    ground("AUS-ELZ-091", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=93, surface_condition="dry"),
                ]),
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-23",
                "failure_family": "ENVIRONMENT",
                "expected_action": "LAND_IMMEDIATELY",
                "expected_candidate_id": "AUS-ELZ-091",
                "expected_reason_codes": ["HIGH_WIND_RISK", "GUST_EXCEEDANCE"],
                "notes": "Tests the wind-distorted envelope: reachability is directional, not radial.",
            },
        },
    )


def scenario_24() -> None:
    """A restriction appears over the site the system would otherwise pick."""
    d = SCENARIOS_DIR / "scenario_24"
    images = fresh_images(d)
    region = "central_austin"

    blocked = real_elz("AUS-ELZ-101", region, "Norwood Tract", "park",
                       image="elz_101_current.png")
    alternate = real_elz("AUS-ELZ-102", region, "Little Stacy", "park",
                         image="elz_102_current.png")
    b_p, a_p = open_point(blocked), open_point(alternate)
    # Stand off perpendicular to the pair so the two sites sit on clearly
    # divergent bearings — otherwise a restriction over the near one blocks the
    # corridor to the far one too, and the scenario tests nothing.
    drone = place_drone([b_p, a_p], bearing_deg=5, standoff_m=1050)
    report("SCENARIO-24", drone, [("Norwood (restricted)", b_p), ("Little Stacy", a_p)])

    truth = {
        "elz_101_current.png": img.sports_field(images / "elz_101_current.png", people=0, seed=101),
        "elz_102_current.png": img.sports_field(images / "elz_102_current.png", people=0, seed=102, goals=False),
    }
    write_json(images / "image_ground_truth.json", truth)

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=190, seconds=120, airspeed_mps=13.0, altitude_m=78,
        soc_end=14.5, soc_drop_pct_min=3.8, temp_end=55.0, temp_rise_c_min=1.9,
        motor_end=0.87, motor_drift=0.012, payload_kg=2.0,
        wind_speed_mps=5.0, wind_direction_deg=5, link_end=0.93, link_drift=0.0, seed=2424,
    )

    # A police-activity restriction raised after the mission launched, sitting
    # directly over the nearer and otherwise-ideal site.
    restrictions = collection([
        feature(rect(b_p[0], b_p[1], 320, 320), id="TFR-2401",
                name="Temporary restriction — police activity",
                type="TEMPORARY_FLIGHT_RESTRICTION", active=True, raised_after_launch=True),
    ])

    write_scenario(
        d,
        {
            "id": "SCENARIO-24",
            "name": "A restriction is raised over the site the map still prefers",
            "description": (
                "A forced landing with two clear, reachable sites. The nearer one is ideal on "
                "every ground measure — and a temporary restriction was raised over it after "
                "this mission launched. Nothing about the surface changed; the airspace above "
                "it did. The system must route to the farther site instead."
            ),
            "region": "Central Austin, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": mission(at(drone, 20, 3600), at(drone, 190, 3900),
                               base_name="East Austin Depot", dest_name="South Lamar Customer"),
            "files": files(restrictions_geojson="restrictions.geojson", image_directory="images"),
        },
        **{
            "known_elzs.geojson": collection([blocked, alternate]),
            "restrictions.geojson": restrictions,
            "ground_state.json": {
                "default_age_sec": 7,
                "candidates": dict([
                    ground("AUS-ELZ-101", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=94, surface_condition="dry"),
                    ground("AUS-ELZ-102", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=92, surface_condition="dry"),
                ]),
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-24",
                "failure_family": "ENVIRONMENT",
                "expected_action": "LAND_IMMEDIATELY",
                "expected_candidate_id": "AUS-ELZ-102",
                "expected_reason_codes": ["RESTRICTED_AIRSPACE_INTERSECTION"],
                "notes": "Ground suitability and airspace availability are independent; both must hold.",
            },
        },
    )


# ==========================================================================
# CONNECTIVITY / TRAFFIC
# ==========================================================================


def scenario_25() -> None:
    """Lost link: the operator can no longer intervene."""
    d = SCENARIOS_DIR / "scenario_25"
    images = fresh_images(d)
    region = "pflugerville"

    elz = real_elz("AUS-ELZ-111", region, "Swenson Park", "park", image="elz_111_current.png")
    other = real_elz("AUS-ELZ-112", region, "Black Locust Park", "park")
    e_p, o_p = open_point(elz), open_point(other)
    drone = place_drone([e_p], bearing_deg=160, standoff_m=640)
    report("SCENARIO-25", drone, [("Swenson", e_p), ("Black Locust", o_p)])

    truth = {"elz_111_current.png": img.sports_field(images / "elz_111_current.png", people=0, seed=111)}
    write_json(images / "image_ground_truth.json", truth)

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=340, seconds=120, airspeed_mps=13.0, altitude_m=80,
        soc_end=16.0, soc_drop_pct_min=3.2, temp_end=52.0, temp_rise_c_min=1.5,
        motor_end=0.88, motor_drift=0.010, payload_kg=2.1,
        wind_speed_mps=5.0, wind_direction_deg=160,
        # The command link has collapsed over the last minute.
        link_end=0.11, link_drift=0.42, seed=2525,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-25",
            "name": "Command link lost with the delivery out of reach",
            "description": (
                "Link quality has collapsed from 0.9 to 0.11 over the last minute: the remote "
                "pilot can see the aircraft and can no longer instruct it. Energy will not "
                "reach the delivery or the base. Whatever the aircraft does next it must "
                "complete without further instruction, so the recommendation has to be one that "
                "needs no mid-course intervention — and the operator has to be shown it while "
                "there is still a link to show it on."
            ),
            "region": "Pflugerville, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": mission(at(drone, 170, 4400), at(drone, 340, 4800),
                               base_name="Pflugerville Depot", dest_name="Wells Branch Customer"),
            "files": files(image_directory="images"),
        },
        **{
            "known_elzs.geojson": collection([elz, other]),
            "ground_state.json": {
                "default_age_sec": 8,
                "candidates": dict([
                    ground("AUS-ELZ-111", pedestrian_count=0, vehicle_count=0, animal_count=0,
                           construction_active=False, clear_area_percent=93, surface_condition="dry"),
                ]),
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-25",
                "failure_family": "CONNECTIVITY_TRAFFIC",
                "expected_action": "LAND_IMMEDIATELY",
                "expected_candidate_id": "AUS-ELZ-111",
                "expected_reason_codes": ["LOST_LINK"],
                "notes": "Tests that a degraded link is treated as loss of oversight, not loss of airworthiness.",
            },
        },
    )


def scenario_26() -> None:
    """The destination facility is closed; the hub is open."""
    d = SCENARIOS_DIR / "scenario_26"
    region = "wells_branch"
    elz_a = real_elz("AUS-ELZ-121", region, "Mills Pond", "park")
    elz_b = real_elz("AUS-ELZ-122", region, "Vista Park", "park")
    sites = [open_point(f) for f in (elz_a, elz_b)]

    drone = place_drone(sites, bearing_deg=200, standoff_m=1000)
    report("SCENARIO-26", drone, [("Mills Pond", sites[0]), ("Vista", sites[1])])

    write_telemetry(
        d / "telemetry.csv",
        end_point=drone, heading_deg=20, seconds=120, airspeed_mps=13.0, altitude_m=88,
        soc_end=24.0, soc_drop_pct_min=1.8, temp_end=47.0, temp_rise_c_min=0.8,
        motor_end=0.93, motor_drift=0.004, payload_kg=2.2,
        wind_speed_mps=4.0, wind_direction_deg=20, link_end=0.95, link_drift=0.0, seed=2626,
    )
    write_scenario(
        d,
        {
            "id": "SCENARIO-26",
            "name": "Delivery site closed after launch; the hub is still open",
            "description": (
                "No fault on the aircraft. The receiving facility has closed its landing pad "
                "since the mission launched, so the delivery cannot be completed regardless of "
                "how much energy remains — and the launch base is upwind and far enough that "
                "arriving there would consume the reserve. A staffed alternate hub is close. "
                "This is a contingency with no failure in it, which is the case a purely "
                "health-driven failsafe never sees."
            ),
            "region": "Wells Branch, Austin, Texas",
            "context_mode": "offline",
            "decision_step": -1,
            "mission": mission(
                at(drone, 200, 6100), at(drone, 20, 5400), at(drone, 55, 1500),
                base_name="North Austin Depot", dest_name="Round Rock Clinic (PAD CLOSED)",
                hub_name="Mills Pond Alternate Hub",
            ),
            "files": files(),
        },
        **{
            "known_elzs.geojson": collection([elz_a, elz_b]),
            "ground_state.json": {
                "default_age_sec": 8,
                "candidates": {},
                "destination_status": "UNAVAILABLE",
                "destination_note": "Receiving pad closed by the operator after launch.",
            },
            "ground_truth.json": {
                "scenario_id": "SCENARIO-26",
                "failure_family": "CONNECTIVITY_TRAFFIC",
                "expected_action": "DIVERT_TO_SAFE_HUB",
                "expected_reason_codes": ["DESTINATION_UNAVAILABLE", "HUB_ENERGY_SUFFICIENT"],
                "notes": "A contingency with no aircraft fault at all.",
            },
        },
    )


BUILDERS = [
    scenario_18, scenario_19,           # abstention first
    scenario_20, scenario_21,           # propulsion / airframe
    scenario_22,                        # navigation / control
    scenario_23, scenario_24,           # environment
    scenario_25, scenario_26,           # connectivity / traffic
]


def main() -> None:
    for builder in BUILDERS:
        builder()
    print(f"\nBuilt {len(BUILDERS)} failure-family scenarios.")


if __name__ == "__main__":
    main()
