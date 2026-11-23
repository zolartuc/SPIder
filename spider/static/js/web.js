// The spider-web map of the network (Overview page).

const SpiderWeb = (() => {
  const { $, esc, ago, shortName, clip } = SP;
  const NS = "http://www.w3.org/2000/svg";
  let drawn = false;
  let index = new Map();

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    parent?.appendChild(n);
    return n;
  }

  function render(devices) {
    const svg = $("web");
    if (!svg) return;
    index = new Map(devices.map((d) => [d.mac, d]));
    svg.replaceChildren();
    const cx = 320, cy = 270, R = 215;

    const hub = devices.find((d) => /\.1$/.test(d.ip)) || null;
    const others = devices.filter((d) => d !== hub);
    const n = Math.max(others.length, 9);
    const angle = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
    const pt = (a, r) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];

    const threads = el("g", { class: "threads" + (drawn ? "" : " draw") }, svg);
    for (let i = 0; i < n; i++) {
      const [x, y] = pt(angle(i), R * 1.08);
      el("line", { x1: cx, y1: cy, x2: x, y2: y, class: "spoke", pathLength: 1 }, threads);
    }
    // Rings sag a little between spokes, like real silk.
    [0.16, 0.3, 0.44, 0.58, 0.72, 0.86, 1].forEach((f, ri) => {
      const r = R * f;
      let d = "";
      for (let i = 0; i <= n; i++) {
        const [x, y] = pt(angle(i % n), r);
        if (i === 0) { d += `M${x.toFixed(1)} ${y.toFixed(1)}`; continue; }
        const [qx, qy] = pt((angle(i - 1) + angle(i)) / 2, r * 0.9);
        d += ` Q${qx.toFixed(1)} ${qy.toFixed(1)} ${x.toFixed(1)} ${y.toFixed(1)}`;
      }
      el("path", { d, class: "ring", pathLength: 1, style: `--i:${ri}` }, threads);
    });

    const nodes = el("g", { class: "nodes" }, svg);
    const add = (d, x, y, a, isHub) => {
      const cls = ["node", d.online ? "live" : "gone", d.known ? "mine" : "stranger", isHub ? "hub" : ""].join(" ");
      const g = el("a", { class: cls, href: `/devices#${encodeURIComponent(d.mac)}`, "data-mac": d.mac,
        "aria-label": `${shortName(d)}, ${d.ip}, ${d.online ? "online" : "offline"}${d.known ? "" : ", not marked as yours"}` }, nodes);
      if (!d.known) el("circle", { cx: x, cy: y, r: isHub ? 15 : 10.5, class: "cocoon" }, g);
      el("circle", { cx: x, cy: y, r: isHub ? 10 : 6, class: "dot" }, g);
      const t = el("text", { class: "label" }, g);
      if (isHub) {
        t.setAttribute("x", x); t.setAttribute("y", y + 32); t.setAttribute("text-anchor", "middle");
      } else {
        const c = Math.cos(a), s = Math.sin(a);
        t.setAttribute("x", x + c * 16); t.setAttribute("y", y + s * 16 + 4);
        t.setAttribute("text-anchor", c > 0.25 ? "start" : c < -0.25 ? "end" : "middle");
        if (Math.abs(c) <= 0.25) t.setAttribute("y", y + (s > 0 ? 24 : -14));
      }
      t.textContent = clip(isHub && !d.name ? "Router" : shortName(d));
    };

    if (hub) add(hub, cx, cy, 0, true); else el("circle", { cx, cy, r: 4, class: "hub-empty" }, nodes);
    others.forEach((d, i) => {
      const a = angle(i);
      const [x, y] = pt(a, R * (d.online ? (i % 2 ? 0.58 : 0.79) : 1.0));
      add(d, x, y, a, false);
    });
    untangleLabels(svg, cy);
    drawn = true;
  }

  // Nudge labels that overlap a neighbour up or down (away from the hub) until they're clear.
  function untangleLabels(svg, cy) {
    const placed = [];
    const hits = (b) => placed.some((p) => b.x < p.x + p.width && b.x + b.width > p.x && b.y < p.y + p.height && b.y + b.height > p.y);
    for (const t of svg.querySelectorAll(".node .label")) {
      let box;
      try { box = t.getBBox(); } catch (e) { return; } // not rendered (hidden tab)
      const step = Number(t.getAttribute("y")) >= cy ? 14 : -14;
      for (let tries = 0; tries < 6 && hits(box); tries++) {
        t.setAttribute("y", Number(t.getAttribute("y")) + step);
        box = t.getBBox();
      }
      placed.push({ x: box.x - 4, y: box.y - 1, width: box.width + 8, height: box.height + 2 });
    }
  }

  // Tooltip
  const svg = $("web");
  svg?.addEventListener("pointerover", (e) => {
    const g = e.target.closest(".node"), tip = $("tip");
    if (!g) { tip.hidden = true; return; }
    const d = index.get(g.dataset.mac); if (!d) return;
    tip.innerHTML = `<strong>${esc(d.name || d.hostname || "Unnamed device")}</strong>
      <span>${esc(d.ip)}</span><span>${esc(d.vendor || "Unknown maker")}</span>
      <span>${d.online ? "Online" : `Offline, last seen ${ago(d.last_seen)}`}</span>`;
    tip.hidden = false;
  });
  svg?.addEventListener("pointermove", (e) => {
    const tip = $("tip"); tip.style.left = `${e.clientX + 14}px`; tip.style.top = `${e.clientY + 14}px`;
  });
  svg?.addEventListener("pointerleave", () => { $("tip").hidden = true; });

  return { render };
})();
