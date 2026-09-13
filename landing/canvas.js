/* Hero atmosphere: a delivery aircraft holding station in volumetric haze.
 *
 * Canvas rather than SVG because the fog, the light shaft and the drift are
 * generated. One accent colour only — the aircraft's light band — so the glow
 * reads as a light source rather than as decoration. Stops entirely if the
 * viewer has asked for reduced motion. */
(function () {
  const canvas = document.getElementById("sky");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const CYAN = "53, 220, 216";
  const EMBER = "201, 112, 92";

  let w = 0, h = 0, dpr = 1, motes = [];

  function rng(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.clientWidth; h = canvas.clientHeight;
    canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const r = rng(20260830);
    motes = [];
    const n = w < 700 ? 26 : 54;
    for (let i = 0; i < n; i++) {
      motes.push({ x: r() * w, y: r() * h, s: .5 + r() * 1.5, v: .06 + r() * .22, a: .05 + r() * .2, p: r() * 6.28 });
    }
  }

  /* --- the aircraft: a lozenge pod with one equatorial light band ------- */

  function drawCraft(cx, cy, k, t) {
    ctx.save();
    ctx.translate(cx, cy + Math.sin(t * .0009) * 10);
    ctx.scale(k, k);

    const bw = 250, bh = 150;

    // ground bloom beneath
    const bloom = ctx.createRadialGradient(0, bh * .9, 4, 0, bh * .9, 300);
    bloom.addColorStop(0, "rgba(" + EMBER + ",.16)");
    bloom.addColorStop(1, "rgba(" + EMBER + ",0)");
    ctx.fillStyle = bloom;
    ctx.beginPath(); ctx.ellipse(0, bh * .9, 300, 90, 0, 0, 6.29); ctx.fill();

    // landing legs
    ctx.strokeStyle = "#0A100F"; ctx.lineWidth = 8; ctx.lineCap = "round";
    [[-72, 20], [-30, 30], [34, 30], [76, 20]].forEach(function (p) {
      ctx.beginPath(); ctx.moveTo(p[0] * .82, bh * .18);
      ctx.lineTo(p[0], bh * .78); ctx.stroke();
    });

    // hull — dark glossy lozenge
    const hull = ctx.createLinearGradient(0, -bh * .6, 0, bh * .6);
    hull.addColorStop(0, "#2B3A38");
    hull.addColorStop(.42, "#131D1C");
    hull.addColorStop(1, "#080E0D");
    ctx.fillStyle = hull;
    ctx.beginPath(); ctx.ellipse(0, 0, bw * .5, bh * .5, 0, 0, 6.29); ctx.fill();

    // top sheen
    const sheen = ctx.createLinearGradient(0, -bh * .5, 0, 0);
    sheen.addColorStop(0, "rgba(220, 245, 244, .16)");
    sheen.addColorStop(1, "rgba(220, 245, 244, 0)");
    ctx.fillStyle = sheen;
    ctx.beginPath(); ctx.ellipse(0, -bh * .12, bw * .43, bh * .3, 0, 0, 6.29); ctx.fill();

    // panel seams
    ctx.strokeStyle = "rgba(150, 190, 186, .10)"; ctx.lineWidth = 1.2;
    for (let i = -2; i <= 2; i++) {
      ctx.beginPath();
      ctx.ellipse(0, 0, bw * .5 - Math.abs(i) * 14, bh * .5, 0, -1.6 + i * .0, 1.6 + i * .0);
      ctx.stroke();
    }
    for (let i = -1; i <= 1; i++) {
      ctx.beginPath();
      ctx.moveTo(i * 62, -bh * .46); ctx.lineTo(i * 74, bh * .3); ctx.stroke();
    }

    // the light band — the single accent in the whole design
    const pulse = .82 + .18 * Math.sin(t * .0016);
    ctx.save();
    ctx.beginPath(); ctx.ellipse(0, 0, bw * .5, bh * .5, 0, 0, 6.29); ctx.clip();
    const band = ctx.createLinearGradient(0, bh * .04, 0, bh * .2);
    band.addColorStop(0, "rgba(" + CYAN + ",0)");
    band.addColorStop(.5, "rgba(" + CYAN + "," + (pulse).toFixed(2) + ")");
    band.addColorStop(1, "rgba(" + CYAN + ",0)");
    ctx.fillStyle = band;
    ctx.fillRect(-bw * .5, bh * .02, bw, bh * .2);
    ctx.restore();

    // band spill into the fog
    const spill = ctx.createRadialGradient(0, bh * .12, 10, 0, bh * .12, 340);
    spill.addColorStop(0, "rgba(" + CYAN + "," + (.3 * pulse).toFixed(2) + ")");
    spill.addColorStop(.35, "rgba(" + CYAN + "," + (.09 * pulse).toFixed(2) + ")");
    spill.addColorStop(1, "rgba(" + CYAN + ",0)");
    ctx.fillStyle = spill;
    ctx.beginPath(); ctx.ellipse(0, bh * .12, 340, 190, 0, 0, 6.29); ctx.fill();

    ctx.restore();
  }

  /* --- loop -------------------------------------------------------------- */

  let t0 = 0;
  function frame(now) {
    if (!t0) t0 = now;
    const t = still ? 4000 : now - t0;

    // base fog
    const base = ctx.createLinearGradient(0, 0, 0, h);
    base.addColorStop(0, "#1A2725");
    base.addColorStop(.45, "#16211F");
    base.addColorStop(1, "#080E0D");
    ctx.fillStyle = base; ctx.fillRect(0, 0, w, h);

    const bx = w * .5, craftY = h * .58;

    // vertical light shaft from above
    const shaft = ctx.createLinearGradient(bx, 0, bx, craftY);
    shaft.addColorStop(0, "rgba(232, 251, 250, .5)");
    shaft.addColorStop(.5, "rgba(200, 240, 238, .12)");
    shaft.addColorStop(1, "rgba(200, 240, 238, 0)");
    ctx.fillStyle = shaft;
    ctx.beginPath();
    ctx.moveTo(bx - 3, 0); ctx.lineTo(bx + 3, 0);
    ctx.lineTo(bx + 90, craftY); ctx.lineTo(bx - 90, craftY);
    ctx.closePath(); ctx.fill();

    // hot core of the shaft
    const core = ctx.createLinearGradient(bx - 2, 0, bx + 2, 0);
    core.addColorStop(0, "rgba(232, 251, 250, 0)");
    core.addColorStop(.5, "rgba(255, 255, 255, .8)");
    core.addColorStop(1, "rgba(232, 251, 250, 0)");
    ctx.fillStyle = core; ctx.fillRect(bx - 2.5, 0, 5, craftY * .82);

    // upper glow pool
    const pool = ctx.createRadialGradient(bx, h * .02, 0, bx, h * .02, Math.max(w, h) * .5);
    pool.addColorStop(0, "rgba(190, 235, 233, .22)");
    pool.addColorStop(1, "rgba(190, 235, 233, 0)");
    ctx.fillStyle = pool; ctx.fillRect(0, 0, w, h);

    drawCraft(bx, craftY, Math.min(1, w / 1750) * .95, t);

    // ground haze
    const haze = ctx.createLinearGradient(0, h * .62, 0, h);
    haze.addColorStop(0, "rgba(8, 14, 13, 0)");
    haze.addColorStop(.55, "rgba(20, 32, 30, .55)");
    haze.addColorStop(1, "rgba(6, 11, 10, .95)");
    ctx.fillStyle = haze; ctx.fillRect(0, h * .62, w, h * .38);

    // drifting motes
    motes.forEach(function (m) {
      const y = still ? m.y : (m.y - t * .012 * m.v + h * 2) % h;
      ctx.fillStyle = "rgba(200, 235, 232," + (m.a * (.5 + .5 * Math.sin(t * .001 + m.p))).toFixed(3) + ")";
      ctx.beginPath(); ctx.arc(m.x, y, m.s, 0, 6.29); ctx.fill();
    });

    // vignette
    const vig = ctx.createRadialGradient(w * .5, h * .45, Math.min(w, h) * .3, w * .5, h * .5, Math.max(w, h) * .78);
    vig.addColorStop(0, "rgba(0,0,0,0)");
    vig.addColorStop(1, "rgba(0,0,0,.72)");
    ctx.fillStyle = vig; ctx.fillRect(0, 0, w, h);

    if (!still) requestAnimationFrame(frame);
  }

  window.addEventListener("resize", function () {
    resize(); if (still) { t0 = 0; frame(performance.now()); }
  }, { passive: true });

  resize();
  if (still) frame(performance.now()); else requestAnimationFrame(frame);
})();
