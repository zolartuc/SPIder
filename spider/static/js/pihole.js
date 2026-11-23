// Pi-hole page: today's blocking, where lookups went, the last 24 hours, and who's asking.
(() => {
  const { $, esc, fmt, api, isAdmin } = SP;
  const NS = "http://www.w3.org/2000/svg";
  const GAP = 2; // surface gap between stacked segments
  let last = null; // most recent {summary, details} for re-rendering on resize

  const pct = (n, of) => (of ? Math.round((n / of) * 1000) / 10 : 0);
  const compact = (n) => (n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k` : String(n));
  const hourLabel = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

  function seriesFor(split) {
    // Bottom to top. Blocked sits on top so it reads as "the part that got stopped".
    return split
      ? [{ key: "forwarded", label: "Asked upstream", cls: "up" },
         { key: "cached", label: "Answered from memory", cls: "cache" },
         { key: "blocked", label: "Blocked", cls: "block" }]
      : [{ key: "forwarded", label: "Allowed", cls: "up" },
         { key: "blocked", label: "Blocked", cls: "block" }];
  }

  // ---------------------------------------------------------------- problem state
  function showProblem(status) {
    const fix = isAdmin ? ` <a href="/settings#pihole">Open Pi-hole settings</a>.` : " An admin can fix this in Settings.";
    const msg = {
      not_configured: "SPIder isn't connected to a Pi-hole yet.",
      offline: "SPIder can't reach your Pi-hole. Check it's switched on and the address is right.",
      auth_failed: "Your Pi-hole turned down the password. Use an app password (v6) or the API token (v5).",
    };
    $("pihole-ok").hidden = true;
    $("pihole-problem").hidden = false;
    $("pihole-problem").innerHTML = `<p class="lead-text">${msg[status] || esc(status)}${fix}</p>`;
  }

  // ---------------------------------------------------------------- headline + split
  function renderTop(s, split) {
    const p = Math.max(0, Math.min(100, Number(s.percent_blocked) || 0));
    $("ph-hero").innerHTML = `<strong>${p}%</strong> of today's ${fmt(s.queries_today)} lookups were blocked.`;

    const total = s.queries_today || 0;
    const parts = split
      ? [{ cls: "up", label: "Asked upstream", n: s.forwarded_today ?? Math.max(0, total - s.blocked_today - (s.cached_today || 0)) },
         { cls: "cache", label: "Answered from memory", n: s.cached_today || 0 },
         { cls: "block", label: "Blocked", n: s.blocked_today || 0 }]
      : [{ cls: "up", label: "Allowed", n: Math.max(0, total - (s.blocked_today || 0)) },
         { cls: "block", label: "Blocked", n: s.blocked_today || 0 }];

    $("ph-split").setAttribute("aria-label", parts.map((x) => `${x.label} ${pct(x.n, total)}%`).join(", "));
    $("ph-split").innerHTML = parts.filter((x) => x.n > 0)
      .map((x) => `<span class="seg ${x.cls}" style="flex-grow:${x.n}"></span>`).join("");
    $("ph-split-legend").innerHTML = parts.map((x) => `
      <li><i class="sw ${x.cls}"></i><span>${x.label}</span><b>${fmt(x.n)}</b><small>${pct(x.n, total)}%</small></li>`).join("");
  }

  function renderStats(s, d) {
    const busiest = d && d.hours && d.hours.length ? d.hours.reduce((a, b) => (b.total > a.total ? b : a)) : null;
    const quietest = d && d.hours && d.hours.length ? d.hours.reduce((a, b) => (b.total < a.total ? b : a)) : null;
    const rows = [
      ["Lookups today", fmt(s.queries_today)],
      ["On the blocklist", fmt(s.domains_on_blocklist)],
      ["Devices using it", fmt(s.active_clients)],
      busiest ? ["Busiest hour", hourLabel(busiest.t)] : null,
      quietest ? ["Quietest hour", hourLabel(quietest.t)] : null,
      ["Pi-hole version", `v${esc(s.version)}`],
    ].filter(Boolean);
    $("ph-stats").innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  }

  // ---------------------------------------------------------------- 24h stacked columns
  function niceMax(v) {
    const p = 10 ** Math.floor(Math.log10(v));
    for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    parent?.appendChild(n);
    return n;
  }

  // Rect with only the top corners rounded (the data end); the baseline end stays square.
  const roundTop = (x, y, w, h, r) => {
    r = Math.min(r, w / 2, h);
    return `M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h}Z`;
  };

  function renderChart(hours, series) {
    const box = $("ph-chart");
    const W = Math.max(320, box.clientWidth), H = 250;
    const m = { t: 14, r: 6, b: 28, l: 40 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b, base = m.t + ih;
    const max = niceMax(Math.max(1, ...hours.map((h) => h.total)));
    const slot = iw / hours.length;
    const bw = Math.max(4, Math.min(26, slot * 0.62));

    const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": `Lookups per hour over the last 24 hours, peaking at ${fmt(Math.max(...hours.map((h) => h.total)))}` });

    const grid = el("g", { class: "grid" }, svg);
    [0, 0.5, 1].forEach((f) => {
      const y = base - f * ih;
      el("line", { x1: m.l, x2: W - m.r, y1: y, y2: y, class: f === 0 ? "baseline" : "" }, grid);
      el("text", { x: m.l - 8, y: y + 4, "text-anchor": "end", class: "axis" }, grid).textContent = compact(max * f);
    });

    const bars = el("g", { class: "bars" }, svg);
    hours.forEach((h, i) => {
      const x = m.l + i * slot + (slot - bw) / 2;
      const g = el("g", { class: "col", "data-i": i }, bars);
      let y = base;
      const visible = series.filter((s) => h[s.key] > 0);
      visible.forEach((s, k) => {
        const hgt = (h[s.key] / max) * ih;
        const top = y - hgt;
        const drawH = Math.max(0, hgt - (k === 0 ? 0 : GAP));
        if (drawH > 0.5) {
          const isTop = k === visible.length - 1;
          el("path", { d: isTop ? roundTop(x, top, bw, drawH, 4) : `M${x} ${top}h${bw}v${drawH}h${-bw}Z`, class: `seg ${s.cls}` }, g);
        }
        y = top;
      });
      // Hit target: the whole slot, taller than the bar.
      el("rect", { x: m.l + i * slot, y: m.t, width: slot, height: ih, class: "hit" }, g);
      if (i % 3 === 0 || i === hours.length - 1) {
        el("text", { x: x + bw / 2, y: H - 8, "text-anchor": "middle", class: "axis" }, svg).textContent =
          i === hours.length - 1 ? "Now" : new Date(h.t).toLocaleTimeString(undefined, { hour: "2-digit" });
      }
    });

    box.replaceChildren(svg);

    const tip = $("tip");
    bars.addEventListener("pointerover", (e) => {
      const col = e.target.closest(".col"); if (!col) return;
      const h = hours[+col.dataset.i];
      bars.classList.add("dim"); bars.querySelectorAll(".col.on").forEach((c) => c.classList.remove("on")); col.classList.add("on");
      const end = new Date(new Date(h.t).getTime() + 3600e3);
      tip.innerHTML = `<strong>${hourLabel(h.t)} to ${end.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</strong>
        <span>${fmt(h.total)} lookups</span>
        ${[...series].reverse().map((s) => `<span class="tip-row"><i class="sw ${s.cls}"></i>${s.label}: ${fmt(h[s.key])}</span>`).join("")}`;
      tip.hidden = false;
    });
    bars.addEventListener("pointermove", (e) => { tip.style.left = `${e.clientX + 14}px`; tip.style.top = `${e.clientY + 14}px`; });
    bars.addEventListener("pointerleave", () => { tip.hidden = true; bars.classList.remove("dim"); bars.querySelectorAll(".col.on").forEach((c) => c.classList.remove("on")); });

    $("ph-legend").innerHTML = [...series].reverse().map((s) => `<li><i class="sw ${s.cls}"></i>${s.label}</li>`).join("");
    $("ph-table").innerHTML = `
      <thead><tr><th scope="col">Hour</th><th scope="col">Lookups</th>${series.map((s) => `<th scope="col">${s.label}</th>`).join("")}</tr></thead>
      <tbody>${hours.map((h) => `<tr><td>${hourLabel(h.t)}</td><td class="data">${fmt(h.total)}</td>${series.map((s) => `<td class="data">${fmt(h[s.key])}</td>`).join("")}</tr>`).join("")}</tbody>`;
  }

  // ---------------------------------------------------------------- ranked lists
  function renderRank(id, items, cls, render) {
    if (!items || !items.length) { $(id).innerHTML = `<li class="muted">Nothing yet today.</li>`; return; }
    const max = Math.max(...items.map((x) => x.count));
    $(id).innerHTML = items.map((x) => `
      <li>
        <div class="rank-row">${render(x)}<b class="rank-val">${fmt(x.count)}</b></div>
        <div class="rank-bar"><i class="${cls}" style="width:${Math.max(2, (x.count / max) * 100)}%"></i></div>
      </li>`).join("");
  }

  const clientLabel = (c) => {
    const name = c.device?.name || (c.name ? c.name.split(".")[0] : null);
    const main = name ? esc(name) : `<span class="muted">Unnamed</span>`;
    const sub = esc(c.ip || "") + (c.device?.vendor && !c.device.vendor.startsWith("Private") ? `, ${esc(SP.tidyVendor(c.device.vendor))}` : "");
    const inner = `<span class="rank-name">${main}<small>${sub}</small></span>`;
    return c.device ? `<a class="rank-link" href="/devices#${encodeURIComponent(c.device.mac)}">${inner}</a>` : inner;
  };

  // ---------------------------------------------------------------- load
  function render() {
    if (!last) return;
    const { s, d } = last;
    const split = d.status === "ok" ? d.split : (s.cached_today != null);
    $("pihole-problem").hidden = true;
    $("pihole-ok").hidden = false;
    renderTop(s, split);
    renderStats(s, d.status === "ok" ? d : null);
    if (d.status === "ok") {
      renderChart(d.hours, seriesFor(d.split));
      renderRank("ph-domains", d.top_blocked, "block", (x) => `<span class="rank-name mono">${esc(x.domain)}</span>`);
      renderRank("ph-clients", d.top_clients, "up", clientLabel);
    }
  }

  async function load() {
    const [s, d] = await Promise.all([api("/api/pihole"), api("/api/pihole/details").catch(() => ({ status: "offline" }))]);
    if (s.status !== "ok") { showProblem(s.status); return; }
    last = { s, d };
    render();
  }

  let resizeTimer;
  new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(render, 120); }).observe($("ph-chart"));

  load().catch(console.error);
  setInterval(() => load().catch(console.error), 60_000);
})();
