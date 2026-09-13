/* SafeDrop operations console.
 *
 * Polls the run endpoint and re-renders. Deliberately plain: no framework, no
 * build step, so a judge can read it end to end.
 */

/* Kept in step with styles.css — colour is information on this screen. */
const COLORS = {
  accent: "#35DCD8",
  ok: "#4FD69C",
  bad: "#FF7A72",
  unverified: "#B08BE8",
  traffic: "#F0A05E",
  muted: "#6A7E7B",
};

const STATUS_COLOR = { VALID: COLORS.ok, REJECTED: COLORS.bad, UNVERIFIED: COLORS.unverified };

const el = (id) => document.getElementById(id);
const scenarioSelect = el("scenario-select");

let runId = null;
let poller = null;
let map = null;
let layers = null;
let hasFitBounds = false;

/* The decision point should announce itself once, not on every poll. */
let announcedState = null;

/* Post-approval flight simulation. */
let lastMapData = null;
let flight = null;
let flightLayer = null;
let flownFor = null;
let mapsEmbedKey = null;

const TERMINAL_STATES = ["OPERATOR_PENDING", "ESCALATED", "APPROVED", "REJECTED", "COMPLETE"];

/* ---------------------------------------------------------------- map --- */

function initMap() {
  map = L.map("map", { zoomControl: true, attributionControl: true }).setView([30.35, -97.72], 12);

  /* Dark basemap by default so the overlays carry the contrast rather than
   * fighting a bright street map. */
  const dark = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    subdomains: "abcd",
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
  });
  const street = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  });
  /* Overhead aerial imagery, key-free. Useful for judging surface and
   * surroundings — but it is archival, so it informs the operator and is never
   * used as evidence of current ground conditions. */
  const satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 19,
      attribution:
        "Imagery &copy; Esri — Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    }
  );

  dark.addTo(map);
  L.control
    .layers(
      { "Dark": dark, "Street map": street, "Aerial imagery (archival)": satellite },
      null,
      { collapsed: true, position: "topright" }
    )
    .addTo(map);

  layers = L.layerGroup().addTo(map);
  flightLayer = L.layerGroup().addTo(map);
}

function drawMap(data) {
  if (!map) return;
  lastMapData = data;
  layers.clearLayers();
  const bounds = [];

  if (data.reachability_envelope?.length) {
    L.polygon(data.reachability_envelope, {
      color: COLORS.accent, weight: 1.5, opacity: 0.8,
      fillColor: COLORS.accent, fillOpacity: 0.08, dashArray: "4 4",
    }).addTo(layers).bindPopup("Wind-adjusted reachable envelope");
    data.reachability_envelope.forEach((p) => bounds.push(p));
  }

  (data.context_features || []).forEach((f) => {
    const kind = f.properties.feature_type;
    const style = { color: "#6b7684", weight: 1, fillOpacity: 0.25, fillColor: "#6b7684" };
    if (kind === "school" || kind === "hospital" || kind === "playground") {
      style.color = COLORS.traffic; style.fillColor = COLORS.traffic; style.fillOpacity = 0.18;
    }
    L.geoJSON(f, { style }).addTo(layers).bindPopup(`${f.properties.name || kind} — ${kind}`);
  });

  (data.obstacles || []).forEach((f) =>
    L.geoJSON(f, { style: { color: "#8b98a8", weight: 1, fillColor: "#8b98a8", fillOpacity: 0.4 } })
      .addTo(layers)
      .bindPopup(`Obstacle: ${f.properties?.name || "structure"}`)
  );

  (data.restrictions || []).forEach((f) =>
    L.geoJSON(f, { style: { color: COLORS.bad, weight: 1.5, dashArray: "6 4", fillColor: COLORS.bad, fillOpacity: 0.1 } })
      .addTo(layers)
      .bindPopup(`Restricted: ${f.properties?.name || "area"}`)
  );

  (data.prefiltered || []).forEach((c) => {
    if (!c.geometry) return;
    L.geoJSON({ type: "Feature", geometry: c.geometry, properties: {} }, {
      style: { color: COLORS.muted, weight: 1, dashArray: "3 3", fillColor: COLORS.muted, fillOpacity: 0.12 },
    })
      .addTo(layers)
      .bindPopup(`${c.candidate_id} — excluded before evaluation<br><small>${c.reason_codes.join(", ")}</small>`);
  });

  (data.unreachable_preplanned || []).forEach((c) => {
    L.circleMarker([c.latitude, c.longitude], {
      radius: 6, color: COLORS.muted, weight: 1.5, fillOpacity: 0.2,
    })
      .addTo(layers)
      .bindPopup(`${c.candidate_id} — ${c.name}<br><small>Preplanned site, outside the envelope</small>`);
    bounds.push([c.latitude, c.longitude]);
  });

  (data.candidates || []).forEach((c) => {
    const color = STATUS_COLOR[c.status] || COLORS.muted;
    const label =
      c.tier === "PREPLANNED_SIMULATED_ELZ"
        ? "SIMULATED PREPLANNED SITE"
        : "NON-CERTIFIED OFF-NOMINAL CANDIDATE";
    const links = referenceLinks(c.latitude, c.longitude);
    const popup =
      `<b>${c.candidate_id}</b> — ${c.name}<br>` +
      `<small>${label}</small><br>` +
      `${c.feature_type}, ${Math.round(c.area_m2)} m²<br>` +
      `Status: <b style="color:${color}">${c.status}</b>` +
      (c.selected ? "<br><b>RECOMMENDED</b>" : "") +
      `<br><small><a href="${links.streetView}" target="_blank" rel="noopener noreferrer">Street View</a> · ` +
      `<a href="${links.satellite}" target="_blank" rel="noopener noreferrer">Satellite</a> — archival reference</small>`;

    if (c.geometry) {
      L.geoJSON({ type: "Feature", geometry: c.geometry, properties: {} }, {
        style: {
          color, weight: c.selected ? 3 : 1.5, fillColor: color,
          fillOpacity: c.selected ? 0.42 : 0.22,
          dashArray: c.tier === "PREPLANNED_SIMULATED_ELZ" ? null : "5 3",
        },
      }).addTo(layers).bindPopup(popup);
    }
    L.circleMarker([c.latitude, c.longitude], {
      radius: c.selected ? 9 : 5.5, color, fillColor: color,
      fillOpacity: c.selected ? 1 : 0.85, weight: c.selected ? 3 : 2,
    }).addTo(layers).bindPopup(popup);
    bounds.push([c.latitude, c.longitude]);
  });

  (data.traffic || []).forEach((t) => {
    L.polyline(t.track, { color: COLORS.traffic, weight: 2, opacity: 0.75, dashArray: "2 6" })
      .addTo(layers).bindPopup(`${t.vehicle_id} — scenario UAV traffic`);
    if (t.track.length) {
      L.circleMarker(t.track[Math.floor(t.track.length / 2)], {
        radius: 5, color: COLORS.traffic, fillColor: COLORS.traffic, fillOpacity: 0.9, weight: 1,
      }).addTo(layers).bindTooltip(t.vehicle_id, { permanent: false });
    }
  });

  (data.routes || []).forEach((r) => {
    const color = r.passed ? COLORS.ok : COLORS.bad;
    L.polyline(r.path, {
      color, weight: r.selected ? 4 : 2,
      opacity: r.selected ? 0.95 : 0.6,
      dashArray: r.passed ? null : "6 5",
    })
      .addTo(layers)
      .bindPopup(`${r.route_id} (variant ${r.variant})<br>${r.passed ? "VERIFIED" : "REJECTED"}`);
  });

  if (data.track?.length > 1) {
    L.polyline(data.track, { color: COLORS.accent, weight: 2, opacity: 0.55 }).addTo(layers);
  }

  const mission = data.mission || {};
  [["base", "Base"], ["destination", "Destination"], ["alternate_hub", "Alternate hub"]].forEach(([key, label]) => {
    const point = mission[key];
    if (!point) return;
    L.circleMarker([point.latitude, point.longitude], {
      radius: 5, color: COLORS.muted, fillColor: "#0d1117", fillOpacity: 1, weight: 2,
    }).addTo(layers).bindPopup(`${label}: ${point.name}`);
    bounds.push([point.latitude, point.longitude]);
  });

  if (data.drone?.latitude) {
    const at = [data.drone.latitude, data.drone.longitude];
    L.circleMarker(at, { radius: 7, color: "#fff", weight: 2, fillColor: COLORS.accent, fillOpacity: 1 })
      .addTo(layers)
      .bindPopup(`Aircraft — ${Math.round(data.drone.altitude_m)} m AGL`);
    bounds.push(at);
  }

  if (!hasFitBounds && bounds.length) {
    map.fitBounds(L.latLngBounds(bounds).pad(0.16));
    hasFitBounds = true;
  }
}

/* -------------------------------------------------------------- panels --- */

function stateClass(value) {
  return `state-${value}`;
}

function renderTelemetry(snap) {
  mapsEmbedKey = snap.maps_embed_key || null;
  const o = snap.operational_state;
  el("sim-clock").textContent = `t = ${snap.telemetry?.timestamp_s ?? 0} s`;
  el("sim-progress").textContent = `${snap.telemetry_index} / ${Math.max(snap.telemetry_total - 1, 0)}`;
  if (!o) { el("readouts").innerHTML = ""; return; }

  const energy = snap.energy;
  const rows = [
    ["Battery", `<span class="${stateClass(o.battery_state)}">${o.battery_state}</span> · ${o.battery_soc.toFixed(1)}%`],
    ["SOC trend", `${o.battery_drop_rate_pct_min.toFixed(2)} %/min`],
    ["Pack temperature", `${o.battery_temperature_c.toFixed(0)} °C`],
    ["Motor", `<span class="${stateClass(o.motor_state)}">${o.motor_state}</span> · ${o.motor_health.toFixed(2)}`],
    ["Navigation", `<span class="${stateClass(o.navigation_state)}">${o.navigation_state}</span>`],
    ["Link", `<span class="${stateClass(o.communication_state)}">${o.communication_state}</span> · ${o.link_quality.toFixed(2)}`],
    ["Altitude", `${o.altitude_m.toFixed(0)} m AGL`],
    ["Airspeed", `${o.airspeed_mps.toFixed(1)} m/s`],
    ["Payload", `${o.payload_kg.toFixed(1)} kg`],
    ["Wind", `<span class="${stateClass(o.wind_risk)}">${o.wind_risk}</span> · ${o.wind_speed_mps.toFixed(1)} m/s from ${o.wind_direction_deg.toFixed(0)}°`],
    ["To destination", `${Math.round(o.distance_to_destination_m)} m`],
    ["To base", `${Math.round(o.distance_to_base_m)} m`],
  ];
  if (o.distance_to_hub_m != null) rows.push(["To alternate hub", `${Math.round(o.distance_to_hub_m)} m`]);
  if (energy) {
    rows.push(["Usable energy", `${energy.usable_energy_wh.toFixed(1)} Wh`]);
    rows.push(["Endurance", `${energy.remaining_flight_time_sec.toFixed(0)} s`]);
  }
  if (snap.reachability) {
    rows.push(["Max reach", `${Math.round(snap.reachability.max_reachable_distance_m)} m`]);
  }
  el("readouts").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

function renderFeasibility(snap) {
  const checks = snap.feasibility || [];
  if (!checks.length) {
    el("feasibility").innerHTML = '<p class="muted">Not yet computed.</p>';
    return;
  }
  el("feasibility").innerHTML = checks
    .map(
      (c) => `<div class="row ${c.feasible ? "pass" : "fail"}">
        <span class="label">${c.target}</span>
        <span>${Math.round(c.distance_m)} m · ${c.energy_margin_wh > 0 ? "+" : ""}${c.energy_margin_wh.toFixed(1)} Wh</span>
      </div>`
    )
    .join("");
}

function renderDecision(snap) {
  const d = snap.contingency_decision;
  if (!d) {
    el("decision").innerHTML = '<p class="muted">Awaiting the decision point.</p>';
    return;
  }
  el("decision").innerHTML = `<div class="decision-card">
      <div class="action ${stateClass(d.severity)}">${d.action}</div>
      <div class="meta muted">${d.severity}${d.emergency_type ? " · " + d.emergency_type : ""}</div>
      <div class="why">${escapeHtml(d.rationale || "")}</div>
      <div class="codes">${(d.reason_codes || []).map((c) => `<span class="code">${c}</span>`).join("")}</div>
    </div>`;
}

function renderTrace(snap) {
  const trace = snap.trace || [];
  if (!trace.length) {
    el("trace").innerHTML = '<li class="muted">No events yet.</li>';
    return;
  }
  el("trace").innerHTML = trace
    .map(
      (t) => `<li class="${t.source}">
        <span class="src">${String(t.t).padStart(3, "0")}s · ${t.source.replace(/_/g, " ")}</span>
        ${escapeHtml(t.message)}
        ${(t.reason_codes || []).length ? `<div class="codes">${t.reason_codes.map((c) => `<span class="code">${c}</span>`).join("")}</div>` : ""}
      </li>`
    )
    .join("");
}

function renderCandidates(snap) {
  const cards = snap.candidate_evaluations || [];
  const prefiltered = snap.prefilter_rejections || [];
  if (!cards.length && !prefiltered.length) {
    const action = snap.contingency_decision?.action;
    el("candidates").innerHTML =
      action && action !== "LAND_IMMEDIATELY"
        ? `<p class="muted">No landing-zone evaluation ran. The contingency action is
             <b>${action}</b>, so the forced-landing workflow was never opened.</p>`
        : '<p class="muted">No candidates evaluated yet.</p>';
    return;
  }

  const order = { REJECTED: 1, UNVERIFIED: 2, VALID: 0 };
  const sorted = [...cards].sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));

  const html = sorted
    .map((c) => {
      const obs = c.image_observation;
      const observed = obs
        ? `<li>Image (${obs.source}): ${obs.people_count} people, ${obs.vehicle_count} vehicles, ${obs.animal_count} animals${obs.visible_clear_area_percent != null ? `, ${Math.round(obs.visible_clear_area_percent)}% clear` : ""}</li>`
        : "";
      const links = referenceLinks(c.latitude, c.longitude);
      return `<article class="card ${c.status}">
        ${c.image_url ? `<img src="${c.image_url}" alt="Current site view of ${c.candidate_id}" loading="lazy">` : ""}
        <div class="body">
          <div class="title"><span>${c.candidate_id}</span><span class="pill ${c.status}">${c.status}</span></div>
          <div class="meta">${escapeHtml(c.name || "")}</div>
          <div class="meta">${c.feature_type || "—"} · ${c.area_m2 ? Math.round(c.area_m2) + " m²" : "—"}</div>
          ${c.certification_status === "NON_CERTIFIED" ? '<div class="cert">NON-CERTIFIED CONTINGENCY CANDIDATE</div>' : ""}
          <div class="codes">${(c.reason_codes || []).map((r) => `<span class="code">${r}</span>`).join("")}</div>
          <ul>${observed}${(c.evidence || []).map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul>
          <div class="streetview" data-open="0"></div>
          <div class="reference">
            <span class="reference-label">Operator reference — archival, not evidence</span>
            ${
              links.embed
                ? `<button type="button" class="reference-btn"
                     onclick="toggleStreetView(this, ${c.latitude}, ${c.longitude})">Street View</button>`
                : `<a href="${links.streetView}" target="_blank" rel="noopener noreferrer">Street View &#8599;</a>`
            }
            <a href="${links.satellite}" target="_blank" rel="noopener noreferrer">Satellite &#8599;</a>
          </div>
        </div>
      </article>`;
    })
    .join("");

  const excluded = prefiltered.length
    ? `<article class="card">
        <div class="body">
          <div class="title"><span>Excluded before evaluation</span></div>
          <div class="meta">Deterministic land-use buffers, applied before any agent sees the candidate.</div>
          <ul>${prefiltered.map((p) => `<li>${p.candidate_id}: ${p.reason_codes.join(", ")}</li>`).join("")}</ul>
        </div>
      </article>`
    : "";

  el("candidates").innerHTML = html + excluded;
}

function renderRecommendation(snap) {
  const verification = snap.verification;
  const recommendation = snap.recommendation;

  el("verification").innerHTML = verification
    ? verification.checks
        .map((c) => {
          const cls = c.passed === true ? "pass" : c.passed === false ? "fail" : "na";
          const mark = c.passed === true ? "PASS" : c.passed === false ? "FAIL" : "N/A";
          return `<div class="check ${cls}"><span class="mark">${mark}</span>
            <span><b>${c.name.replace(/_/g, " ")}</b><br><span class="detail">${escapeHtml(c.detail || "")}</span></span></div>`;
        })
        .join("")
    : "";

  const briefing = el("briefing");
  if (recommendation?.briefing) {
    briefing.textContent = recommendation.briefing;
    briefing.classList.remove("muted");
  } else {
    briefing.textContent = "No recommendation yet.";
    briefing.classList.add("muted");
  }

  renderOutcome(snap);

  const ready = Boolean(recommendation?.recommendation_ready) && !snap.operator_decision;
  el("btn-approve").disabled = !ready;
  el("btn-reject").disabled = !ready;
  el("operator-note").textContent = snap.operator_decision
    ? "Decision recorded. Reset to run another scenario."
    : "Remote operator authorisation is required. SafeDrop never sends a flight command.";

  const mode = el("badge-mode");
  if (snap.agent_mode === "scripted") {
    mode.hidden = false;
    mode.className = "badge scripted";
    mode.textContent = "SCRIPTED STAND-INS — NOT AGENT OUTPUT";
    mode.title = "Rule-driven stand-ins are running in place of the three agents.";
  } else {
    mode.hidden = false;
    mode.className = "badge";
    mode.textContent = snap.agent_model || "live agents";
  }

  const badge = el("badge-state");
  badge.textContent = snap.workflow_state;
  badge.className = "badge";
  if (snap.workflow_state === "OPERATOR_PENDING" || snap.workflow_state === "APPROVED") badge.classList.add("ok");
  if (snap.workflow_state === "ESCALATED" || snap.workflow_state === "REJECTED") badge.classList.add("bad");
}

/* ------------------------------------------------ simulated execution --- */

/* What the aircraft would fly once a remote pilot acts on the recommendation.
 *
 * This is a depiction, not a command. SafeDrop issues nothing to any aircraft —
 * it hands a verified recommendation to a person, and the person flies it. The
 * animation exists so the consequence of the decision is legible on the map.
 */
function approvedPath(snap, data) {
  const selected = (data.routes || []).find((r) => r.selected);
  if (selected) {
    return {
      points: selected.path.map((p, i) => [p[0], p[1], selected.altitudes?.[i] ?? 0]),
      label: `${selected.route_id} to ${snap.recommendation?.recommended_candidate_name || "site"}`,
      landing: true,
    };
  }

  // Non-landing branches fly to a mission point rather than a landing site.
  const targets = {
    CONTINUE: ["destination", "destination"],
    RETURN_TO_BASE: ["base", "launch base"],
    DIVERT_TO_SAFE_HUB: ["alternate_hub", "alternate hub"],
  };
  const entry = targets[snap.contingency_decision?.action];
  const start = data.drone;
  if (!entry || !start?.latitude) return null;
  const target = (data.mission || {})[entry[0]];
  if (!target) return null;
  return {
    points: [
      [start.latitude, start.longitude, start.altitude_m || 0],
      [target.latitude, target.longitude, start.altitude_m || 0],
    ],
    label: `Proceeding to ${target.name} (${entry[1]})`,
    landing: false,
  };
}

function haversineM(a, b) {
  const R = 6371008.8;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[0] - a[0]);
  const dLon = toRad(b[1] - a[1]);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

function startFlight(snap) {
  const plan = approvedPath(snap, lastMapData);
  if (!plan || plan.points.length < 2) return;

  stopFlight();
  const legs = [];
  let total = 0;
  for (let i = 0; i < plan.points.length - 1; i++) {
    const d = haversineM(plan.points[i], plan.points[i + 1]);
    legs.push(d);
    total += d;
  }
  if (total <= 0) return;

  const bar = el("exec-bar");
  bar.hidden = false;
  bar.className = "exec";
  el("exec-label").textContent = "SIMULATED EXECUTION — flown by the remote pilot";
  el("exec-replay").hidden = true;

  const trail = L.polyline([], { color: "#3fb950", weight: 4, opacity: 0.95 }).addTo(flightLayer);
  const marker = L.circleMarker(plan.points[0].slice(0, 2), {
    radius: 8,
    color: "#fff",
    weight: 2,
    fillColor: "#3fb950",
    fillOpacity: 1,
  }).addTo(flightLayer);

  const durationMs = 9000;
  const started = performance.now();

  function frame(now) {
    const f = Math.min(1, (now - started) / durationMs);
    let travelled = total * f;
    let idx = 0;
    while (idx < legs.length - 1 && travelled > legs[idx]) {
      travelled -= legs[idx];
      idx++;
    }
    const t = legs[idx] > 0 ? travelled / legs[idx] : 1;
    const a = plan.points[idx];
    const b = plan.points[idx + 1];
    const at = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    const alt = a[2] + (b[2] - a[2]) * t;

    marker.setLatLng(at);
    trail.addLatLng(at);
    el("exec-readout").textContent =
      `${Math.round(total * (1 - f))} m to run · ${Math.round(alt)} m AGL`;

    if (f < 1) {
      flight = requestAnimationFrame(frame);
      return;
    }
    flight = null;
    bar.className = "exec done";
    el("exec-label").textContent = plan.landing
      ? "SIMULATED TOUCHDOWN — no command was issued by SafeDrop"
      : "SIMULATED ARRIVAL — no command was issued by SafeDrop";
    el("exec-readout").textContent = plan.label;
    el("exec-replay").hidden = false;
    L.circleMarker(plan.points[plan.points.length - 1].slice(0, 2), {
      radius: 11,
      color: "#3fb950",
      weight: 3,
      fillColor: "#3fb950",
      fillOpacity: 0.3,
    }).addTo(flightLayer);
  }

  flight = requestAnimationFrame(frame);
}

function stopFlight() {
  if (flight) cancelAnimationFrame(flight);
  flight = null;
  if (flightLayer) flightLayer.clearLayers();
  el("exec-bar").hidden = true;
  el("exec-replay").hidden = true;
}

/* --------------------------------------------------- decision announcement --- */

/* Scroll to whichever panel actually has something to read: the candidate
 * cards on a forced landing, the recommendation itself on any other branch. */
let reviewTargetId = "panel-recommendation";

function scrollToReview() {
  const target = document.getElementById(reviewTargetId);
  target.scrollIntoView({ behavior: "smooth", block: "start" });
  target.classList.add("flash");
  setTimeout(() => target.classList.remove("flash"), 1300);
}

/* The decision surface is a button in the top bar, not an overlay. It carries
 * the outcome in its colour and stays put, so nothing ever covers the console. */
function setReviewButton({ tone, label, armed, pulse = false }) {
  const button = el("btn-review");
  button.textContent = label;
  button.disabled = !armed;
  button.className = "review-btn" + (tone ? ` ${tone}` : "") + (armed ? " armed" : "");
  if (pulse) {
    // Restart the animation rather than relying on a class that is already set.
    void button.offsetWidth;
    button.classList.add("pulse");
    setTimeout(() => button.classList.remove("pulse"), 2800);
  }
}

function announce(snap) {
  if (snap.workflow_state === announcedState) return;
  if (!TERMINAL_STATES.includes(snap.workflow_state)) return;
  announcedState = snap.workflow_state;

  reviewTargetId = (snap.candidate_evaluations || []).length
    ? "panel-candidates"
    : "panel-recommendation";

  if (snap.operator_decision) return; // renderOutcome owns the button from here

  const escalated = snap.workflow_state === "ESCALATED";
  const landing = snap.contingency_decision?.action === "LAND_IMMEDIATELY";
  const action = snap.contingency_decision?.action || "DECISION";

  setReviewButton({
    tone: escalated ? "bad" : landing ? "warn" : "ok",
    label: escalated ? `${action} — NO VERIFIED PLAN` : `REVIEW — ${action}`,
    armed: true,
    pulse: true,
  });
  scrollToReview();
}

function renderOutcome(snap) {
  const outcome = el("operator-outcome");
  if (!snap.operator_decision) {
    outcome.hidden = true;
    return;
  }
  const approved = snap.operator_decision === "APPROVE";
  const site = snap.recommendation?.recommended_candidate_name;
  const route = snap.recommendation?.recommended_route_id;
  outcome.hidden = false;
  outcome.className = "outcome " + (approved ? "approved" : "rejected");
  outcome.innerHTML = `<div>
      <div class="verdict">OPERATOR ${approved ? "APPROVED" : "REJECTED"}</div>
      <div class="said">${
        approved
          ? `Recorded as approved${site ? `: ${escapeHtml(site)}` : ""}${route ? ` via ${route}` : ""}. ` +
            "<b>SafeDrop does not send commands to the aircraft — it sends a verified recommendation " +
            "to the remote Pilot in Command, who flies it.</b> The track now animating on the map is a " +
            "depiction of what that pilot would fly. No command left this system."
          : "Recorded as rejected. The recommendation was not acted on, and nothing was flown."
      }</div>
    </div>`;

  setReviewButton({
    tone: "done",
    label: approved ? "APPROVED — SIMULATED TRACK" : "REJECTED — NOT FLOWN",
    armed: true,
  });

  if (approved && flownFor !== snap.run_id) {
    flownFor = snap.run_id;
    startFlight(snap);
  }
  if (!approved) stopFlight();
}

/* Key-free links into Google Maps using the documented `api=1` URL scheme.
 *
 * These open in a new tab for the operator to eyeball the surroundings. The
 * imagery is archival and Google's platform terms bar passing it to a vision
 * model, so it is reference only — it never enters the evidence package and no
 * verification check reads it.
 */
function referenceLinks(lat, lon) {
  const streetView = `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lon}`;
  const satellite = `https://www.google.com/maps/search/?api=1&query=${lat},${lon}&basemap=satellite`;
  const embed = mapsEmbedKey
    ? `https://www.google.com/maps/embed/v1/streetview?key=${encodeURIComponent(mapsEmbedKey)}` +
      `&location=${lat},${lon}&heading=210&pitch=5&fov=90`
    : null;
  return { streetView, satellite, embed };
}

/* Street View renders inline only when a Maps Embed key is configured;
 * otherwise the operator gets a link out. Either way it sits behind a toggle so
 * archival imagery never shares the frame with the current site image. */
function toggleStreetView(button, lat, lon) {
  const holder = button.closest(".card").querySelector(".streetview");
  if (holder.dataset.open === "1") {
    holder.innerHTML = "";
    holder.dataset.open = "0";
    button.textContent = "Street View";
    return;
  }
  const { embed } = referenceLinks(lat, lon);
  holder.innerHTML =
    `<iframe title="Street View reference" loading="lazy" referrerpolicy="no-referrer-when-downgrade"
       src="${embed}" allowfullscreen></iframe>
     <div class="streetview-note">Google Street View — archival imagery of unknown date. Reference only.</div>`;
  holder.dataset.open = "1";
  button.textContent = "Hide Street View";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

/* --------------------------------------------------------------- polling --- */

async function refresh() {
  if (!runId) return;
  const [snap, mapData] = await Promise.all([
    fetch(`/api/runs/${runId}`).then((r) => r.json()),
    fetch(`/api/runs/${runId}/map`).then((r) => r.json()),
  ]);
  renderTelemetry(snap);
  renderFeasibility(snap);
  renderDecision(snap);
  renderTrace(snap);
  renderCandidates(snap);
  renderRecommendation(snap);
  drawMap(mapData);
  announce(snap);

  if (!snap.playing && TERMINAL_STATES.includes(snap.workflow_state)) stopPolling();
}

function startPolling(interval = 600) {
  stopPolling();
  poller = setInterval(refresh, interval);
  refresh();
}

function stopPolling() {
  if (poller) clearInterval(poller);
  poller = null;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return response.json();
}

/* Selecting a scenario starts from a blank console. Leaving the previous run's
 * recommendation or approve/reject state on screen is how you demo the wrong
 * scenario's answer. */
function resetUi() {
  announcedState = null;
  flownFor = null;
  stopFlight();
  setReviewButton({ tone: null, label: "No decision yet", armed: false });

  el("operator-outcome").hidden = true;
  el("readouts").innerHTML = "";
  el("sim-clock").textContent = "t = 0 s";
  el("sim-progress").textContent = "0 / 0";
  el("feasibility").innerHTML = '<p class="muted">Not yet computed.</p>';
  el("decision").innerHTML = '<p class="muted">Awaiting the decision point.</p>';
  el("trace").innerHTML = '<li class="muted">No events yet.</li>';
  el("candidates").innerHTML = '<p class="muted">No candidates evaluated yet.</p>';
  el("verification").innerHTML = "";
  el("briefing").textContent = "No recommendation yet.";
  el("briefing").classList.add("muted");
  el("btn-approve").disabled = true;
  el("btn-reject").disabled = true;
  el("operator-note").textContent =
    "Remote operator authorisation is required. SafeDrop never sends a flight command.";

  const badge = el("badge-state");
  badge.textContent = "IDLE";
  badge.className = "badge";
  el("badge-mode").hidden = true;

  if (layers) layers.clearLayers();
  lastMapData = null;
  hasFitBounds = false;
}

async function startRun(immediate) {
  resetUi();
  const scenario = scenarioSelect.value;
  const meta = await fetch(`/api/scenarios/${scenario}`).then((r) => r.json());
  el("scenario-desc").textContent = meta.description;
  const result = await post(`/api/scenarios/${scenario}/start`, { immediate });
  runId = result.run_id;
  startPolling();
}

/* ------------------------------------------------------------------ wire --- */

initMap();

el("btn-start").onclick = () => startRun(false);
el("btn-complete").onclick = () => startRun(true);
el("btn-pause").onclick = async () => { if (runId) { await post(`/api/runs/${runId}/pause`); refresh(); } };
el("btn-step").onclick = async () => { if (runId) { await post(`/api/runs/${runId}/step`); refresh(); } };
el("btn-reset").onclick = async () => {
  if (!runId) return;
  stopPolling();
  const result = await post(`/api/runs/${runId}/reset`);
  runId = result.run_id;
  resetUi();
  refresh();
};

window.toggleStreetView = toggleStreetView;

el("btn-review").onclick = scrollToReview;
el("exec-replay").onclick = async () => {
  const snap = await fetch(`/api/runs/${runId}`).then((r) => r.json());
  startFlight(snap);
};
el("btn-approve").onclick = async () => { await post(`/api/runs/${runId}/operator-decision`, { decision: "APPROVE" }); refresh(); };
el("btn-reject").onclick = async () => { await post(`/api/runs/${runId}/operator-decision`, { decision: "REJECT" }); refresh(); };

scenarioSelect.onchange = async () => {
  stopPolling();
  runId = null;
  resetUi();
  const meta = await fetch(`/api/scenarios/${scenarioSelect.value}`).then((r) => r.json());
  el("scenario-desc").textContent = meta.description;
};

scenarioSelect.dispatchEvent(new Event("change"));
