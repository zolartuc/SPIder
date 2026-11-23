// Traffic page: what each device talks to, and the first contacts worth a look.
(() => {
  const { $, esc, ago, fmt, api, isAdmin, tidyVendor } = SP;
  const NS = "http://www.w3.org/2000/svg";
  let overview = null;
  let selected = decodeURIComponent((location.hash.match(/device=([^&]+)/) || [])[1] || "");

  const nameOf = (d) => {
    if (d.name) return d.name;
    if (d.vendor && !d.vendor.startsWith("Private")) return tidyVendor(d.vendor);
    if (!d.ip) return "Unknown device";
    return d.vendor ? `Phone or laptop .${d.ip.split(".").pop()}` : d.ip;
  };
  const when = (iso) => {
    const d = new Date(iso), today = new Date();
    const sameDay = d.toDateString() === today.toDateString();
    const t = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    return sameDay ? `today ${t}` : `${d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} ${t}`;
  };

  // ---------------------------------------------------------------- tiny bar sparkline (list rows)
  function spark(values) {
    const w = 96, h = 20, n = values.length, max = Math.max(1, ...values);
    const bw = w / n - 1;
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">${values
      .map((v, i) => { const bh = Math.max(v ? 1.5 : 0, (v / max) * h); return `<rect x="${i * (w / n)}" y="${h - bh}" width="${bw}" height="${bh}" rx="1"/>`; })
      .join("")}</svg>`;
  }

  // ---------------------------------------------------------------- 24h single-series column chart
  function niceMax(v) {
    const p = 10 ** Math.floor(Math.log10(v));
    for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function columns(box, hours, values) {
    const W = Math.max(300, box.clientWidth), H = 190;
    const m = { t: 10, r: 4, b: 26, l: 34 }, iw = W - m.l - m.r, ih = H - m.t - m.b, base = m.t + ih;
    let max = niceMax(Math.max(1, ...values));
    if (max < 20 && max % 2) max += 1; // whole-number midline: you can't have half a lookup
    const slot = iw / values.length, bw = Math.max(4, Math.min(22, slot * 0.62));
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", W); svg.setAttribute("height", H); svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `Lookups per hour, last 24 hours, peak ${fmt(Math.max(...values))}`);
    const label = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k` : String(n));
    let grid = "", cols = "", xAxis = "";
    [0, 0.5, 1].forEach((f) => {
      const y = base - f * ih;
      grid += `<line x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}" class="${f === 0 ? "baseline" : ""}"/>`;
      grid += `<text x="${m.l - 8}" y="${y + 4}" text-anchor="end" class="axis">${label(max * f)}</text>`;
    });
    values.forEach((v, i) => {
      const x = m.l + i * slot + (slot - bw) / 2, bh = (v / max) * ih, r = Math.min(4, bw / 2, bh);
      cols += `<g class="col" data-i="${i}">`;
      if (bh > 0.5) cols += `<path class="seg up" d="M${x} ${base}V${base - bh + r}Q${x} ${base - bh} ${x + r} ${base - bh}H${x + bw - r}Q${x + bw} ${base - bh} ${x + bw} ${base - bh + r}V${base}Z"/>`;
      cols += `<rect class="hit" x="${m.l + i * slot}" y="${m.t}" width="${slot}" height="${ih}"/></g>`;
      if (i % 3 === 0 || i === values.length - 1)
        xAxis += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" class="axis">${i === values.length - 1 ? "Now" : new Date(hours[i]).toLocaleTimeString(undefined, { hour: "2-digit" })}</text>`;
    });
    svg.innerHTML = `<g class="grid">${grid}${xAxis}</g><g class="bars">${cols}</g>`;
    box.replaceChildren(svg);

    const bars = svg.querySelector(".bars"), tip = $("tip");
    bars.addEventListener("pointerover", (e) => {
      const col = e.target.closest(".col"); if (!col) return;
      const i = +col.dataset.i, end = new Date(new Date(hours[i]).getTime() + 3600e3);
      bars.classList.add("dim"); bars.querySelectorAll(".col.on").forEach((c) => c.classList.remove("on")); col.classList.add("on");
      tip.innerHTML = `<strong>${new Date(hours[i]).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })} to ${end.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</strong><span>${fmt(values[i])} lookups</span>`;
      tip.hidden = false;
    });
    bars.addEventListener("pointermove", (e) => { tip.style.left = `${e.clientX + 14}px`; tip.style.top = `${e.clientY + 14}px`; });
    bars.addEventListener("pointerleave", () => { tip.hidden = true; bars.classList.remove("dim"); bars.querySelectorAll(".col.on").forEach((c) => c.classList.remove("on")); });
  }

  // ---------------------------------------------------------------- shared bits
  const rankBars = (items, label = (x) => esc(x.category)) => {
    if (!items.length) return `<p class="muted">Nothing in the last 24 hours.</p>`;
    const max = Math.max(...items.map((x) => x.count)), total = items.reduce((a, x) => a + x.count, 0);
    return `<ol class="rank">${items.map((x) => `
      <li><div class="rank-row"><span class="rank-name">${label(x)}</span>
        <b class="rank-val">${fmt(x.count)} <small>${Math.round((x.count / total) * 100)}%</small></b></div>
        <div class="rank-bar"><i class="up" style="width:${Math.max(2, (x.count / max) * 100)}%"></i></div></li>`).join("")}</ol>`;
  };

  const contactList = (items, showDevice) => items.length
    ? `<ol class="contacts">${items.map((c) => `
        <li class="${c.device && !c.device.known ? "from-stranger" : ""}">
          ${showDevice ? `<a class="contact-dev" href="#device=${encodeURIComponent(c.key)}" data-key="${esc(c.key)}">${esc(nameOf(c.device))}</a>` : ""}
          <span class="contact-site"><b class="mono">${esc(c.site)}</b><small>${esc(c.category)}</small></span>
          <time datetime="${esc(c.first_seen)}">${when(c.first_seen)}</time>
        </li>`).join("")}</ol>`
    : `<p class="muted">Nothing new. Every device stuck to places it already knew.</p>`;

  // ---------------------------------------------------------------- list
  function renderList() {
    const o = overview;
    $("tr-all-sub").textContent = `${fmt(o.totals.lookups)} lookups, ${o.totals.devices} devices`;
    $("tr-devices").innerHTML = o.devices.map((d) => `
      <button type="button" class="tr-item ${d.known ? "" : "stranger"}" data-key="${esc(d.key)}">
        <span class="tr-name"><b>${esc(nameOf(d))}</b>
          <small>${fmt(d.count)} lookups${d.learning ? ", learning" : ""}</small></span>
        ${spark(d.spark)}
        ${d.new_7d ? `<span class="tr-badge" title="First contacts this week">${d.new_7d} new</span>` : ""}
      </button>`).join("");
    markSelected();
  }

  function markSelected() {
    document.querySelectorAll(".tr-item").forEach((b) => b.setAttribute("aria-current", String(b.dataset.key === selected)));
  }

  // ---------------------------------------------------------------- detail: all devices
  function renderAll() {
    const o = overview;
    $("tr-detail").innerHTML = `
      <p class="tr-lead"><strong>${fmt(o.totals.lookups)}</strong> lookups to <strong>${fmt(o.totals.sites)}</strong> different sites from ${o.totals.devices} devices in the last 24 hours.</p>
      <section class="panel">
        <div class="panel-head"><h2>First contacts this week</h2><span class="muted small">places a device had never talked to before</span></div>
        ${contactList(o.first_contacts.map((c) => ({ ...c, device: c.device })), true)}
      </section>
      <section class="panel">
        <div class="panel-head"><h2>What your network does</h2><span class="muted small">last 24 hours</span></div>
        ${rankBars(o.categories)}
      </section>`;
  }

  // ---------------------------------------------------------------- detail: one device
  async function renderDevice(key) {
    $("tr-detail").innerHTML = `<p class="muted">Loading…</p>`;
    let d;
    try { d = await api(`/api/traffic/device/${encodeURIComponent(key)}`); }
    catch (e) { $("tr-detail").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    if (key !== selected) return; // user moved on

    const dev = d.device, busiest = d.spark.indexOf(Math.max(...d.spark));
    const learningUntil = dev.learning_since ? new Date(new Date(dev.learning_since).getTime() + 86400e3) : null;
    $("tr-detail").innerHTML = `
      <header class="tr-head">
        <div>
          <h2>${esc(nameOf(dev))}</h2>
          <p class="muted">${[dev.ip, dev.vendor && tidyVendor(dev.vendor), dev.known === false ? "not marked as yours" : null].filter(Boolean).map(esc).join(", ")}
          ${dev.mac ? ` <a href="/devices#${encodeURIComponent(dev.mac)}">Open in Devices</a>` : ""}</p>
        </div>
      </header>
      ${dev.learning ? `<p class="notice">Still learning what's normal for this device. First contacts are flagged from ${learningUntil ? when(learningUntil.toISOString()) : "tomorrow"}.</p>` : ""}
      <dl class="stats wide">
        <div><dt>Lookups, last 24 hours</dt><dd>${fmt(d.count)}</dd></div>
        <div><dt>Sites</dt><dd>${fmt(d.sites.filter((s) => s.today).length)}</dd></div>
        <div><dt>Busiest hour</dt><dd>${d.count ? new Date(d.hours[busiest]).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "–"}</dd></div>
        <div><dt>First contacts, 30 days</dt><dd>${d.first_contacts.length}</dd></div>
      </dl>
      <section class="panel">
        <div class="panel-head"><h2>Last 24 hours</h2><span class="muted small">lookups per hour</span></div>
        <div class="chart" id="tr-chart"></div>
      </section>
      <div class="tr-cols">
        <section class="panel">
          <div class="panel-head"><h2>What it does</h2><span class="muted small">last 24 hours</span></div>
          ${rankBars(d.categories)}
        </section>
        <section class="panel">
          <div class="panel-head"><h2>First contacts</h2><span class="muted small">last 30 days</span></div>
          ${contactList(d.first_contacts, false)}
        </section>
      </div>
      <section class="panel">
        <div class="panel-head"><h2>Sites it talks to</h2><span class="muted small">last 7 days</span></div>
        <div class="table-wrap"><table class="sites">
          <thead><tr><th scope="col">Site</th><th scope="col">Kind</th><th scope="col">Last 24 hours</th><th scope="col">7 days</th><th scope="col">First seen</th></tr></thead>
          <tbody>${d.sites.map((s) => `
            <tr class="${s.new ? "is-new" : ""}">
              <td class="data">${esc(s.site)}${s.new ? ` <span class="tr-badge">new</span>` : ""}</td>
              <td>${esc(s.category)}</td>
              <td class="data">${s.today ? fmt(s.today) : `<span class="dim">–</span>`}</td>
              <td class="data">${fmt(s.week)}</td>
              <td class="dim">${s.first_seen ? ago(s.first_seen) : "–"}</td>
            </tr>`).join("")}</tbody>
        </table></div>
      </section>`;
    columns($("tr-chart"), d.hours, d.spark);
  }

  function renderDetail() { selected ? renderDevice(selected) : renderAll(); }

  // ---------------------------------------------------------------- events
  function select(key) {
    selected = key;
    const target = key ? `#device=${encodeURIComponent(key)}` : location.pathname;
    if ((key ? location.hash : "") !== (key ? target : "")) history.pushState(null, "", target);
    markSelected();
    renderDetail();
    if (matchMedia("(max-width: 900px)").matches) $("tr-detail").scrollIntoView({ block: "start" });
  }

  $("tr-main").addEventListener("click", (e) => {
    const item = e.target.closest(".tr-item, .contact-dev");
    if (!item) return;
    e.preventDefault();
    select(item.dataset.key || "");
  });

  function showEmpty(status) {
    const fix = isAdmin ? ` <a href="/settings#pihole">Connect your Pi-hole</a>.` : " An admin can connect it in Settings.";
    const msg = {
      not_configured: "Traffic comes from the lookups your Pi-hole answers, and SPIder isn't connected to one yet.",
      offline: "SPIder can't reach your Pi-hole right now, so there's no new traffic to show.",
      auth_failed: "Your Pi-hole turned down SPIder's password, so traffic can't be read.",
      starting: "SPIder is reading your Pi-hole's recent lookups. This takes a minute the first time.",
    };
    $("tr-main").hidden = true;
    $("tr-empty").hidden = false;
    $("tr-empty").innerHTML = `<p class="lead-text">${msg[status] || esc(status)}${status === "starting" ? "" : fix}</p>`;
  }

  async function load() {
    overview = await api("/api/traffic");
    const hasData = overview.devices.some((d) => d.count) || overview.first_contacts.length;
    if (!hasData && overview.status !== "ok") { showEmpty(overview.status); setTimeout(load, 5000); return; }
    if (!hasData) { showEmpty("starting"); setTimeout(load, 5000); return; }
    $("tr-empty").hidden = true;
    $("tr-main").hidden = false;
    renderList();
    renderDetail();
  }

  // Links like #device=<key> (and the browser's back button) change the selection without reloading.
  window.addEventListener("hashchange", () => {
    const key = decodeURIComponent((location.hash.match(/device=([^&]+)/) || [])[1] || "");
    if (key !== selected && overview) select(key);
  });

  let rt;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => selected && renderDetail(), 150); });
  load().catch(console.error);
  setInterval(() => { if (!document.activeElement?.closest?.(".tr-detail")) load().catch(console.error); }, 60_000);
})();
