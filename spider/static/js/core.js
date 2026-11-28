// SPIder: shared helpers, theme, scan status and the user menu. Loaded on every page.

const SP = (() => {
  const $ = (id) => document.getElementById(id);
  const body = document.body;
  const signedIn = body.dataset.signedIn === "true";
  const isAdmin = body.dataset.role === "admin";

  // Anything that came off the network (hostnames, vendors) is untrusted: escape it.
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function ago(iso) {
    if (!iso) return "never";
    const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (secs < 60) return "just now";
    if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
    if (secs < 86400) return `${Math.round(secs / 3600)} h ago`;
    return `${Math.round(secs / 86400)} d ago`;
  }

  const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString());

  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const e = new Error(data.detail || `Request failed (${r.status})`);
      e.status = r.status; e.field = data.field;
      throw e;
    }
    return data;
  }

  let toastTimer;
  function toast(msg) {
    const t = $("toast"); if (!t) return;
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3500);
  }

  // ---------- device naming helpers
  const shortName = (d) =>
    d.name || (d.hostname ? d.hostname.split(".")[0] : null) ||
    (d.vendor && !d.vendor.startsWith("Private") ? d.vendor.split(/[\s,]/)[0] : null) ||
    `.${(d.ip || "").split(".").pop()}`;

  const clip = (s, n = 16) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

  // "Xiaomi Communications Co Ltd" -> "Xiaomi Communications". Strips stacked company suffixes.
  const tidyVendor = (v) => {
    let out = v, prev;
    do {
      prev = out;
      out = out.replace(/[,.\s]*\b(Inc|Ltd|Co|LLC|GmbH|SAS|Limited|Corporation|Corp|International|Intl|Trading)\.?,?\s*$/i, "").trim();
    } while (out !== prev && out.length > 2);
    return out || v;
  };

  function eventWho(e) {
    if (e.name) return e.name;
    const parts = (e.detail || "").split(" ");
    const ip = e.type === "new_contact" ? parts[1] : parts[0];
    if (!e.vendor || e.vendor.startsWith("Private")) return `A ${e.vendor ? "phone or laptop" : "device"} at ${ip}`;
    return tidyVendor(e.vendor);
  }

  // One line of HTML describing an event, e.g. "<b>Smart plug</b> contacted <b>tuyacn.com</b> for the first time".
  function eventLine(e) {
    const who = `<b>${esc(eventWho(e))}</b>`;
    if (e.type === "new_contact") {
      const [site, , ...cat] = (e.detail || "").split(" ");
      return `${who} contacted <b class="mono">${esc(site)}</b> for the first time${cat.length ? ` <span class="dim">(${esc(cat.join(" "))})</span>` : ""}`;
    }
    if (e.type === "honeypot") {
      const [ip, port, service] = (e.detail || "").split(" ");
      const label = { ftp: "FTP", ssh: "SSH", telnet: "Telnet", web: "web login", smb: "file sharing", rdp: "Remote Desktop", vnc: "VNC", sql: "database" }[service] || "port";
      return `${e.name ? who : `<b>${esc(ip)}</b>`} knocked on the trapdoor's fake ${esc(label)} <span class="dim mono">:${esc(port)}</span>`;
    }
    if (e.type === "ip_changed") return `${who} changed address, ${esc(e.detail)}`;
    if (e.type === "blocked") return `${who} was ${esc(e.detail)}`;
    if (e.type === "unblocked") return `${who} can use the internet again <span class="dim">(${esc(e.detail)})</span>`;
    return `${who} ${EVENT_TEXT[e.type] || esc(e.type)}`;
  }

  const EVENT_TEXT = {
    new_device: "joined for the first time",
    online: "came back online",
    offline: "went offline",
    ip_changed: "changed address",
    new_contact: "contacted somewhere new",
  };

  // ---------- theme
  function setLayout(name, { save = true } = {}) {
    document.documentElement.dataset.layout = name;
    try { localStorage.setItem("spider-layout", name); } catch (e) {}
    document.querySelectorAll("[data-layout-choice]").forEach((b) =>
      b.setAttribute("aria-checked", String(b.dataset.layoutChoice === name)));
    if (save && signedIn) api("/api/me/layout", { method: "PUT", body: JSON.stringify({ layout: name }) }).catch(() => {});
    window.dispatchEvent(new Event("resize"));  // charts and the web re-fit
  }

  function setTheme(name, { save = true } = {}) {
    document.documentElement.dataset.theme = name;
    try { localStorage.setItem("spider-theme", name); } catch (e) {}
    document.querySelectorAll("[data-theme-choice]").forEach((b) =>
      b.setAttribute("aria-checked", String(b.dataset.themeChoice === name)));
    if (save && signedIn) api("/api/me/theme", { method: "PUT", body: JSON.stringify({ theme: name }) }).catch(() => {});
  }

  // ---------- scan status (every page)
  async function loadStatus() {
    const el = $("scan-status");
    try {
      const s = await api("/api/status");
      const l = s.last_scan;
      const btn = $("scan-btn");
      if (btn) { btn.disabled = s.scanning; btn.textContent = s.scanning ? "Scanning…" : "Scan now"; }
      if (el) el.textContent = s.scanning ? `Scanning ${l.subnet || "your network"}`
        : l.error ? `Last scan failed: ${l.error}`
        : l.at ? `Scanned ${ago(l.at)}, found ${l.found}` : "Waiting for the first scan";
      return s;
    } catch (e) {
      if (el) el.textContent = "";
      return { scanning: false };
    }
  }

  $("scan-btn")?.addEventListener("click", async () => {
    try { await api("/api/scan", { method: "POST" }); } catch (e) { toast(e.message); return; }
    const btn = $("scan-btn"); btn.disabled = true; btn.textContent = "Scanning…";
    const poll = setInterval(async () => {
      const s = await loadStatus();
      if (!s.scanning) { clearInterval(poll); document.dispatchEvent(new Event("spider:refresh")); toast("Scan finished"); }
    }, 1500);
  });

  // ---------- user menu
  $("sign-out")?.addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    location.href = "/";
  });
  document.addEventListener("click", (e) => {
    const m = document.querySelector(".user-menu[open]");
    if (m && !m.contains(e.target)) m.open = false;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { const m = document.querySelector(".user-menu[open]"); if (m) { m.open = false; m.querySelector("summary").focus(); } }
  });

  loadStatus();
  setInterval(loadStatus, 10_000);

  // ---------- version footer: "Update available" badge, like Pi-hole's
  let versionInfo = null, updating = false;
  async function refreshVersion() {
    let v;
    try { v = await api("/api/version"); } catch (e) { return; }
    versionInfo = v;
    const u = v.update, pill = $("update-pill");
    if (!pill) return;
    pill.hidden = !(u && u.available);
    if (u && u.available) pill.textContent = `Update available: ${u.latest}`;
  }
  function renderPop() {
    const v = versionInfo, u = v && v.update, pop = $("update-pop");
    if (!u) return;
    const how = isAdmin && v.can_update
      ? `<button type="button" id="update-now">Update now</button><span class="muted small">Takes about a minute. SPIder restarts and this page reloads.</span>`
      : isAdmin
        ? `<p class="muted small">${esc(v.cant_update_because || "")} In Portainer, open the spider stack and click <b>Pull and redeploy</b>.</p>`
        : `<p class="muted small">Ask an admin to update SPIder.</p>`;
    pop.innerHTML = `<h3>SPIder ${esc(u.latest_label || u.latest)} is available</h3>
      <p class="muted small">You're on ${esc(v.label || v.version)}.</p>
      <div class="pop-foot">${how}</div>`;
  }
  $("update-pill")?.addEventListener("click", () => {
    const pop = $("update-pop"), open = pop.hidden;
    if (open) renderPop();
    pop.hidden = !open;
    $("update-pill").setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", async (e) => {
    const pop = $("update-pop");
    if (pop && !pop.hidden && !e.target.closest(".app-foot")) { pop.hidden = true; $("update-pill").setAttribute("aria-expanded", "false"); }
    if (!e.target.closest("#update-now") || updating) return;
    if (!confirm(`Update SPIder to ${versionInfo.update.latest} now? It restarts, which takes about a minute.`)) return;
    updating = true;
    const btn = e.target.closest("#update-now"); btn.disabled = true;
    const before = versionInfo && `${versionInfo.version} ${versionInfo.commit}`;
    try { await api("/api/version/update", { method: "POST" }); }
    catch (err) { toast(err.message); updating = false; btn.disabled = false; return; }
    $("update-pop").hidden = true;
    updateStage(before, versionInfo.update.latest_label || versionInfo.update.latest);
  });

  // ---------- update progress: the spider spins one ring of its web per step
  const STEP_NAMES = ["Checking", "Downloading", "Verifying", "Testing", "Libraries", "Switching over", "Restarting"];
  function webSvg(rings) {
    const spokes = 8, pt = (r, k) => {
      const a = (k / spokes) * 2 * Math.PI - Math.PI / 2;
      return [+(r * Math.cos(a)).toFixed(2), +(r * Math.sin(a)).toFixed(2)];
    };
    const radii = Array.from({ length: rings }, (_, i) => 22 + i * 12);
    const spokeLines = Array.from({ length: spokes }, (_, k) => {
      const [x, y] = pt(104, k);
      return `<line class="us-spoke" x1="0" y1="0" x2="${x}" y2="${y}"/>`;
    }).join("");
    // Each ring starts at the top spoke and runs clockwise, sagging a little between spokes.
    const ringPaths = radii.map((r, i) => {
      let d = `M${pt(r, 0).join(" ")}`;
      for (let k = 1; k <= spokes; k++) {
        const [cx, cy] = pt(r * 0.8, k - 0.5), [x, y] = pt(r, k);
        d += `Q${cx} ${cy} ${x} ${y}`;
      }
      return `<path class="us-ghost" d="${d}"/><path class="us-ring" data-ring="${i}" pathLength="100" d="${d}"/>`;
    }).join("");
    return { svg: `<svg class="us-web" viewBox="-112 -112 224 224" aria-hidden="true">${spokeLines}${ringPaths}<g class="us-spider"></g></svg>`, radii };
  }
  function updateStage(before, to) {
    const { svg, radii } = webSvg(STEP_NAMES.length);
    const stage = document.createElement("div");
    stage.className = "update-stage";
    stage.setAttribute("role", "dialog"); stage.setAttribute("aria-modal", "true"); stage.setAttribute("aria-label", "Updating SPIder");
    stage.innerHTML = `<div class="us-card">
        ${svg}
        <h2 class="us-title">Updating to SPIder ${esc(to || "")}</h2>
        <p class="us-step" aria-live="polite"></p>
        <div class="us-meta"><span class="us-detail"></span><span class="us-time">0:00</span></div>
        <p class="us-note muted small">Keep this page open. SPIder restarts by itself and this page reloads when it's back.</p>
        <div class="us-foot" hidden><button type="button" class="us-close">Close</button></div>
      </div>`;
    document.body.appendChild(stage);
    // The spider: a copy of the logo, without its own thread.
    const logo = document.querySelector(".brand svg.logo");
    if (logo) {
      const spider = logo.cloneNode(true);
      spider.removeAttribute("class");
      spider.querySelector(".lg-thread")?.remove();
      spider.setAttribute("x", "-15"); spider.setAttribute("y", "-15"); spider.setAttribute("width", "30"); spider.setAttribute("height", "30");
      stage.querySelector(".us-spider").appendChild(spider);
    }
    const q = (sel) => stage.querySelector(sel);
    const rings = [...stage.querySelectorAll(".us-ring")];
    const kb = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
    const t0 = Date.now();
    let step = -1, finished = false, downSince = null;

    const show = (i, st = {}) => {
      step = Math.max(step, i);
      rings.forEach((r, k) => {
        r.classList.toggle("done", k < step);
        r.classList.toggle("active", k === step);
        r.style.strokeDashoffset = "";
      });
      q(".us-spider").style.transform = `translateY(${-radii[Math.min(step, radii.length - 1)]}px)`;
      q(".us-step").textContent = `Step ${step + 1} of ${STEP_NAMES.length}: ${st.message || STEP_NAMES[step]}`;
      let detail = "";
      if (step === 1 && st.bytes) {
        detail = st.total ? `${kb(st.bytes)} of ${kb(st.total)}` : `${kb(st.bytes)} so far`;
        if (st.total) {  // draw this ring exactly as far as the download has got
          const r = rings[1]; r.classList.add("measured");
          r.style.strokeDashoffset = String(100 - (100 * st.bytes) / st.total);
        }
      }
      q(".us-detail").textContent = detail;
      stage.classList.toggle("waiting", step === STEP_NAMES.length - 1);
    };
    const finish = (ok, title, line, reload) => {
      finished = true; clearInterval(poll); clearInterval(clock);
      stage.classList.remove("waiting");
      stage.classList.add(ok ? "ready" : "failed");
      if (ok) { rings.forEach((r) => { r.classList.remove("active"); r.classList.add("done"); r.style.strokeDashoffset = ""; }); q(".us-spider").style.transform = ""; }
      q(".us-title").textContent = title;
      q(".us-step").textContent = line;
      q(".us-detail").textContent = "";
      q(".us-note").hidden = true;
      if (reload) setTimeout(() => location.reload(), 2200);
      else { q(".us-foot").hidden = false; q(".us-close").focus(); }
    };
    q(".us-close").addEventListener("click", () => { stage.remove(); updating = false; refreshVersion(); });
    const clock = setInterval(() => {
      const s2 = Math.round((Date.now() - t0) / 1000);
      q(".us-time").textContent = `${Math.floor(s2 / 60)}:${String(s2 % 60).padStart(2, "0")}`;
    }, 1000);
    show(0);
    const poll = setInterval(async () => {
      if (finished) return;
      let v;
      try { v = await api("/api/version"); }
      catch (e) {  // SPIder is restarting
        downSince ??= Date.now();
        show(STEP_NAMES.length - 1, { message: "Restarting, waiting for SPIder to answer" });
        if (Date.now() - downSince > 180_000) q(".us-note").textContent = "This is taking longer than usual. If SPIder doesn't come back, check the stack in Portainer.";
        return;
      }
      const st = v.updating || {};
      if (`${v.version} ${v.commit}` !== before && !st.demo) {
        finish(true, `SPIder ${v.label || v.version} is ready`, "Reloading…", true);
      } else if (st.state === "failed") {
        finish(false, "The update didn't finish", `${st.message || "Something went wrong."} SPIder is still on ${versionInfo.label || versionInfo.version}.`, false);
      } else if (st.state === "done") {
        finish(true, `SPIder ${to} is ready`, st.message || "", false);
      } else if (st.step != null) {
        show(st.step, st);
      }
    }, 1000);
  }
  let refreshVitals = () => {};
  // ---------- status panel under the logo (like Pi-hole's)
  async function loadVitals() {
    const box = $("vitals");
    if (!box) return;
    let v;
    try { v = await api("/api/vitals"); } catch (e) { return; }
    const row = (id, text, level) => {
      const li = $(id); if (!li) return;
      li.hidden = text == null;
      if (text == null) return;
      li.querySelector("span").textContent = text;
      if (level) li.dataset.level = level;
    };
    row("v-status", v.status_text, v.status);
    row("v-honey", v.honeypot_unseen ? `Trapdoor: ${v.honeypot_unseen} new` : null, "bad");
    row("v-temp", v.temp_c != null ? `Temp ${v.temp_c.toFixed(1)} \u00b0C` : null, v.temp_level === "hot" ? "bad" : v.temp_level === "warm" ? "warn" : "ok");
    row("v-load", v.load ? `Load ${v.load.map((x) => x.toFixed(2)).join("/")}` : null);
    row("v-mem", v.memory_pct != null ? `Memory ${v.memory_pct.toFixed(1)} %` : null, v.memory_pct >= 90 ? "warn" : null);
    row("v-disk", v.disk_pct != null ? `Storage ${Math.round(v.disk_pct)} % used` : null, v.disk_pct >= 95 ? "bad" : v.disk_pct >= 85 ? "warn" : null);
    box.hidden = false;
  }
  // Cheap to read (a few small files), so refresh every 3 s, but only while the tab is visible.
  loadVitals();
  refreshVitals = loadVitals;
  setInterval(() => { if (!document.hidden) loadVitals(); }, 3_000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadVitals(); });

  refreshVersion();
  setInterval(refreshVersion, 10 * 60_000);

  return { refreshVitals: () => refreshVitals(), setLayout, refreshVersion: () => refreshVersion(), $, esc, ago, fmt, api, toast, shortName, clip, eventWho, eventLine, tidyVendor, EVENT_TEXT, setTheme, signedIn, isAdmin };
})();
