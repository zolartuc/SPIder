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
    if (u && u.available) pill.textContent = `Update available: ${u.behind_by} new change${u.behind_by === 1 ? "" : "s"}`;
  }
  function renderPop() {
    const v = versionInfo, u = v && v.update, pop = $("update-pop");
    if (!u) return;
    const how = isAdmin && v.can_update
      ? `<button type="button" id="update-now">Update now</button><span class="muted small">Takes about a minute. SPIder restarts and this page reloads.</span>`
      : isAdmin
        ? `<p class="muted small">${esc(v.cant_update_because || "")} In Portainer, open the spider stack and click <b>Pull and redeploy</b>.</p>`
        : `<p class="muted small">Ask an admin to update SPIder.</p>`;
    pop.innerHTML = `<h3>${u.behind_by} new change${u.behind_by === 1 ? "" : "s"} on GitHub</h3>
      <ol class="changes">${u.changes.map((c) => `<li><code>${esc(c.short)}</code> ${esc(c.title)}</li>`).join("")}</ol>
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
    if (!confirm("Update SPIder now? It restarts while Portainer rebuilds it, usually a few minutes.")) return;
    updating = true;
    const btn = e.target.closest("#update-now"); btn.disabled = true; btn.textContent = "Updating…";
    const before = versionInfo && versionInfo.commit;
    try { await api("/api/version/update", { method: "POST" }); }
    catch (err) { toast(err.message); updating = false; btn.disabled = false; btn.textContent = "Update now"; return; }
    toast("Updating SPIder. This page reloads when it's back.");
    // Follow progress, then reload once the new build answers.
    const poll = setInterval(async () => {
      try {
        const v = await api("/api/version");
        const st = v.updating || {};
        if (st.state === "failed") {
          clearInterval(poll); updating = false;
          btn.disabled = false; btn.textContent = "Update now";
          toast(st.message || "The update didn't work. Nothing was changed.");
          return;
        }
        if (st.message && st.state !== "idle") btn.textContent = st.message + "…";
        if (v.commit && v.commit !== before) { clearInterval(poll); location.reload(); }
      } catch (e2) { btn.textContent = "Restarting…"; }
    }, 3_000);
  });
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
    row("v-temp", v.temp_c != null ? `Temp ${v.temp_c.toFixed(1)} \u00b0C` : null, v.temp_level === "hot" ? "bad" : v.temp_level === "warm" ? "warn" : "ok");
    row("v-load", v.load ? `Load ${v.load.map((x) => x.toFixed(2)).join("/")}` : null);
    row("v-mem", v.memory_pct != null ? `Memory ${v.memory_pct.toFixed(1)} %` : null, v.memory_pct >= 90 ? "warn" : null);
    row("v-disk", v.disk_pct != null ? `Storage ${Math.round(v.disk_pct)} % used` : null, v.disk_pct >= 95 ? "bad" : v.disk_pct >= 85 ? "warn" : null);
    box.hidden = false;
  }
  loadVitals();
  setInterval(loadVitals, 10_000);

  refreshVersion();
  setInterval(refreshVersion, 10 * 60_000);

  return { setLayout, refreshVersion: () => refreshVersion(), $, esc, ago, fmt, api, toast, shortName, clip, eventWho, eventLine, tidyVendor, EVENT_TEXT, setTheme, signedIn, isAdmin };
})();
