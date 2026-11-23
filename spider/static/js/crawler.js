// The spider that lives on the web (Overview page), and the threads that vibrate when a device does
// something unusual. Everything here sits on #web-fx, a layer web.js keeps across its redraws.
(() => {
  const { $, esc, api, ago, signedIn } = SP;
  const svg = $("web");
  if (!svg || typeof SpiderWeb === "undefined") return;

  const NS = "http://www.w3.org/2000/svg";
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const SPEED = 46;           // web units per second
  const TAU = Math.PI * 2;

  const el = (tag, attrs = {}, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    parent?.appendChild(n);
    return n;
  };
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

  // ------------------------------------------------------------------ the spider (top-down, facing +x)
  // Legs: [attachX, attachY, kneeX, kneeY, footX, footY]; mirrored for the other side. Gait sets alternate.
  const LEGS = [
    [4.5, 2.2, 9.5, 6.5, 15, 6],
    [3.2, 2.8, 5.5, 9.5, 9, 13.5],
    [1.8, 2.9, -1.5, 9.5, -3.5, 14],
    [0.6, 2.6, -5.5, 7, -11, 9.5],
  ];

  function buildSpider(layer) {
    const g = el("g", { class: "crawler", "aria-hidden": "true" }, layer);
    const legs = [];
    const legLayer = el("g", { class: "cr-legs" }, g);
    LEGS.forEach(([ax, ay, kx, ky, fx, fy], i) => {
      for (const side of [1, -1]) {
        const leg = el("path", { d: `M${ax} ${ay * side}Q${kx} ${ky * side} ${fx} ${fy * side}` }, legLayer);
        // Alternating tetrapod gait: L1 L3 R2 R4 move together, then the others.
        legs.push({ node: leg, ax, ay: ay * side, set: (i + (side > 0 ? 0 : 1)) % 2 });
      }
    });
    // Abdomen: the raspberry.
    el("ellipse", { cx: -6.5, cy: 0, rx: 8, ry: 6.6, class: "cr-body" }, g);
    [[-3.6, -2.4], [-3.6, 2.4], [-7, -3.6], [-7, 0], [-7, 3.6], [-10.4, -2], [-10.4, 2], [-13, 0]]
      .forEach(([x, y]) => el("circle", { cx: x, cy: y, r: 1.15, class: "cr-shine" }, g));
    // Calyx where head meets berry.
    el("path", { d: "M0.5 0C-1 -1.5 -2.6 -4.8 -2.2 -6.4C-0.2 -5 0.8 -2.4 0.5 0Z", class: "cr-leaf" }, g);
    el("path", { d: "M0.5 0C-1 1.5 -2.6 4.8 -2.2 6.4C-0.2 5 0.8 2.4 0.5 0Z", class: "cr-leaf" }, g);
    // Head and eyes.
    el("ellipse", { cx: 3.6, cy: 0, rx: 3.9, ry: 3.3, class: "cr-head" }, g);
    el("circle", { cx: 6.2, cy: -1.25, r: 0.85, class: "cr-eye" }, g);
    el("circle", { cx: 6.2, cy: 1.25, r: 0.85, class: "cr-eye" }, g);
    return { g, legs };
  }

  // ------------------------------------------------------------------ geometry helpers
  let geom = null;
  const pos = (s, f) => {
    const a = geom.angle(((s % geom.n) + geom.n) % geom.n);
    return [geom.cx + f * geom.R * Math.cos(a), geom.cy + f * geom.R * Math.sin(a)];
  };
  const nearestRing = (f) => geom.rings.reduce((b, r) => (Math.abs(r - f) < Math.abs(b - f) ? r : b), geom.rings[0]);

  function segment(fn, end) {
    let len = 0, prev = fn(0);
    for (let k = 1; k <= 12; k++) { const p = fn(k / 12); len += Math.hypot(p[0] - prev[0], p[1] - prev[1]); prev = p; }
    return { fn, len: Math.max(len, 0.01), end };
  }
  const radial = (s, f0, f1) => segment((t) => pos(s, f0 + (f1 - f0) * t), { s, f: f1 });
  const ringStep = (f, s, dir) => {
    const a0 = geom.angle(((s % geom.n) + geom.n) % geom.n);
    const a1 = a0 + dir * (TAU / geom.n);
    const r = f * geom.R, P0 = pos(s, f), P2 = pos(s + dir, f);
    const C = [geom.cx + r * 0.9 * Math.cos((a0 + a1) / 2), geom.cy + r * 0.9 * Math.sin((a0 + a1) / 2)];
    return segment((t) => {
      const u = 1 - t;
      return [u * u * P0[0] + 2 * u * t * C[0] + t * t * P2[0], u * u * P0[1] + 2 * u * t * C[1] + t * t * P2[1]];
    }, { s: (((s + dir) % geom.n) + geom.n) % geom.n, f });
  };
  const pause = (ms, tag) => ({ pause: ms, tag });

  // ------------------------------------------------------------------ route planning
  const st = { s: 0, f: 0 };
  let queue = [], current = null, progress = 0, pauseLeft = 0;
  let nextVisit = performance.now() + 4000;
  let flagged = [];

  function walkTo(targetS, targetF) {
    const out = [];
    let { s, f } = st;
    if (f === 0) { out.push(radial(targetS, 0, nearestRing(Math.max(targetF, 0.3)))); s = targetS; f = nearestRing(Math.max(targetF, 0.3)); }
    if (!geom.rings.includes(f)) { const rf = nearestRing(f); out.push(radial(s, f, rf)); f = rf; }
    let diff = (((targetS - s) % geom.n) + geom.n) % geom.n;
    const dir = diff <= geom.n / 2 ? 1 : -1;
    let steps = dir > 0 ? diff : geom.n - diff;
    while (steps-- > 0) { out.push(ringStep(f, s, dir)); s = (((s + dir) % geom.n) + geom.n) % geom.n; }
    if (f !== targetF) out.push(radial(s, f, targetF));
    return out;
  }

  function planIdle() {
    if (st.f === 0) {
      const s = Math.floor(Math.random() * geom.n);
      return [radial(s, 0, pick(geom.rings.slice(1, 6)))];
    }
    if (!geom.rings.includes(st.f)) return [radial(st.s, st.f, nearestRing(st.f))];
    const r = Math.random();
    if (r < 0.45) {
      const dir = Math.random() < 0.5 ? 1 : -1, out = [];
      let s = st.s;
      for (let k = 0, steps = 1 + Math.floor(Math.random() * 3); k < steps; k++) { out.push(ringStep(st.f, s, dir)); s += dir; }
      return Math.random() < 0.3 ? [...out, pause(rand(500, 1600))] : out;
    }
    if (r < 0.65) return [radial(st.s, st.f, pick(geom.rings.filter((x) => x !== st.f)))];
    if (r < 0.85) {
      const nodes = [...geom.nodes.values()];
      if (nodes.length) { const n = pick(nodes); return [...walkTo(n.i, n.f), pause(rand(1200, 2400), "inspect")]; }
    }
    return [radial(st.s, st.f, 0), pause(rand(1500, 3500), "home")];
  }

  function planNext(now) {
    const target = flagged.length && now > nextVisit ? geom.nodes.get(pick(flagged).mac) : null;
    if (target) {
      nextVisit = now + rand(14000, 22000);
      return [...walkTo(target.i, target.f), pause(4200, "check")];
    }
    return planIdle();
  }

  // ------------------------------------------------------------------ flags: vibrating threads
  let flagLayer = null, crawler = null, tick = null;
  const flagEls = new Map();

  function flagTitle(f) {
    const who = f.name || f.vendor || f.ip || "A device";
    const more = f.count > 1 ? ` and ${f.count - 1} more` : "";
    return `${who} contacted ${f.latest.site}${more} for the first time`;
  }

  function syncFlags() {
    if (!geom || !flagLayer) return;
    const want = new Map(flagged.filter((f) => f.mac && geom.nodes.has(f.mac)).map((f) => [f.mac, f]));
    for (const [mac, node] of flagEls) if (!want.has(mac)) { node.g.remove(); flagEls.delete(mac); }
    for (const [mac, f] of want) {
      const n = geom.nodes.get(mac);
      let item = flagEls.get(mac);
      if (!item) {
        const g = el("a", { class: "flag", href: `/traffic#device=${encodeURIComponent(mac)}`, "aria-label": `${flagTitle(f)}. Open its traffic.` }, flagLayer);
        item = {
          g, f,
          hit: el("line", { class: "flag-hit" }, g),
          wave: el("path", { class: "flag-wave" }, g),
          pulse: el("circle", { class: "flag-pulse", r: 3.2 }, g),
          ring: el("circle", { class: "flag-ring", r: 11 }, g),
        };
        flagEls.set(mac, item);
      }
      item.f = f; item.n = n;
      item.hit.setAttribute("x1", n.x); item.hit.setAttribute("y1", n.y);
      item.hit.setAttribute("x2", geom.cx); item.hit.setAttribute("y2", geom.cy);
      item.ring.setAttribute("cx", n.x); item.ring.setAttribute("cy", n.y);
      if (reduceMotion.matches) drawFlag(item, 0, true);
    }
    // Keep the spider on top of the threads.
    if (crawler) flagLayer.parentNode.appendChild(crawler.g);
  }

  function drawFlag(item, t, still = false) {
    const { n } = item, x0 = n.x, y0 = n.y, x1 = geom.cx, y1 = geom.cy;
    const L = Math.hypot(x1 - x0, y1 - y0), ux = (x1 - x0) / L, uy = (y1 - y0) / L, px = -uy, py = ux;
    const P = 2.6, phase = (t % P) / P;
    // A pluck travels from the device towards the hub, then rings down.
    const amp = still ? 0 : 3.4 * Math.exp(-phase * 3.2) + 0.5;
    let d = "";
    for (let k = 0; k <= 36; k++) {
      const s = (k / 36) * L;
      const off = amp * Math.sin(s * 0.12 - t * 11) * Math.sin((Math.PI * s) / L);
      d += `${k ? "L" : "M"}${(x0 + ux * s + px * off).toFixed(1)} ${(y0 + uy * s + py * off).toFixed(1)}`;
    }
    item.wave.setAttribute("d", d);
    const travel = Math.min(1, phase / 0.45);
    item.pulse.setAttribute("cx", x0 + (x1 - x0) * travel);
    item.pulse.setAttribute("cy", y0 + (y1 - y0) * travel);
    item.pulse.style.opacity = still ? 0 : travel < 1 ? 1 : 0;
  }

  async function loadFlags() {
    try { flagged = await api("/api/flags"); } catch { flagged = []; }
    syncFlags();
    const line = $("s-flags");
    if (line) {
      if (!flagged.length) { line.hidden = true; return; }
      const first = flagged[0];
      line.hidden = false;
      line.innerHTML = flagged.length === 1
        ? `<b>${esc(first.name || first.vendor || first.ip)}</b> is doing something unusual: it contacted <b class="mono">${esc(first.latest.site)}</b> for the first time ${ago(first.latest.ts)}. <a href="/traffic#device=${encodeURIComponent(first.key)}">Take a look</a>`
        : `<b>${flagged.length} devices</b> are doing something unusual. Their threads are shaking on the web. <a href="/traffic">Take a look</a>`;
    }
  }

  // Tooltip on a shaking thread.
  svg.addEventListener("pointerover", (e) => {
    const a = e.target.closest(".flag"); if (!a) return;
    const item = [...flagEls.values()].find((x) => x.g === a); if (!item) return;
    const tip = $("tip");
    tip.innerHTML = `<strong>${esc(flagTitle(item.f))}</strong><span>${esc(item.f.latest.category || "")}, ${ago(item.f.latest.ts)}</span><span>Click to see what it's been up to.</span>`;
    tip.hidden = false;
  });
  svg.addEventListener("pointerout", (e) => { if (e.target.closest(".flag")) $("tip").hidden = true; });

  // ------------------------------------------------------------------ animation loop
  let heading = 0, gait = 0, last = 0, raf = 0;

  function place(x, y) {
    crawler.g.setAttribute("transform", `translate(${x.toFixed(2)} ${y.toFixed(2)}) rotate(${((heading * 180) / Math.PI).toFixed(1)}) scale(1.4)`);
    const swing = Math.sin(gait) * 0.32;
    for (const l of crawler.legs) {
      const a = (l.set ? swing : -swing) * (180 / Math.PI);
      l.node.setAttribute("transform", `rotate(${a.toFixed(1)} ${l.ax} ${l.ay})`);
    }
  }

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - (last || now)) / 1000);
    last = now;
    if (!geom) return;
    const t = now / 1000;
    for (const item of flagEls.values()) drawFlag(item, t);

    if (pauseLeft > 0) {
      pauseLeft -= dt * 1000;
      gait += dt * 0.6; // fidget
      const [x, y] = current ? current.fn(1) : pos(st.s, st.f);
      place(x, y);
      if (pauseLeft <= 0) current = null;
      return;
    }
    if (!current) {
      if (!queue.length) queue = planNext(now);
      const next = queue.shift();
      if (next.pause) { pauseLeft = next.pause; return; }
      current = next; progress = 0;
    }
    progress += (SPEED * dt) / current.len;
    const t1 = Math.min(1, progress);
    const [x, y] = current.fn(t1);
    const [ax, ay] = current.fn(Math.min(1, t1 + 0.02)), [bx, by] = current.fn(Math.max(0, t1 - 0.02));
    if (Math.hypot(ax - bx, ay - by) > 0.05) {
      const want = Math.atan2(ay - by, ax - bx);
      let delta = ((want - heading + Math.PI * 3) % TAU) - Math.PI;
      heading += delta * Math.min(1, dt * 9);
    }
    gait += SPEED * dt * 0.42;
    place(x, y);
    if (progress >= 1) { Object.assign(st, current.end); current = null; }
  }

  function start() {
    if (raf || reduceMotion.matches || document.hidden) return;
    last = 0;
    raf = requestAnimationFrame(frame);
  }
  function stop() { cancelAnimationFrame(raf); raf = 0; }

  function restAtHome() {
    // Reduced motion: the spider sits at the hub, threads of flagged devices are highlighted but still.
    stop();
    Object.assign(st, { s: 0, f: 0 });
    queue = []; current = null; heading = -Math.PI / 2;
    if (crawler && geom) place(geom.cx, geom.cy);
    for (const item of flagEls.values()) drawFlag(item, 0, true);
  }

  SpiderWeb.onRender((g) => {
    const firstTime = !geom;
    geom = g;
    const fx = svg.querySelector("#web-fx");
    if (!flagLayer) { flagLayer = el("g", { class: "flags" }, fx); crawler = buildSpider(fx); }
    if (firstTime) { heading = -Math.PI / 2; place(geom.cx, geom.cy); }
    if (st.s >= geom.n) { Object.assign(st, { s: 0, f: 0 }); queue = []; current = null; }
    syncFlags();
    reduceMotion.matches ? restAtHome() : start();
  });

  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
  reduceMotion.addEventListener?.("change", () => (reduceMotion.matches ? restAtHome() : start()));

  loadFlags();
  tick = setInterval(loadFlags, 30_000);
  document.addEventListener("spider:refresh", loadFlags);
})();
