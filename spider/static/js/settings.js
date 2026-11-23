// Settings page.
(() => {
  const { $, esc, api, toast, setTheme, isAdmin } = SP;
  let values = {};

  // ---------- theme cards (anyone)
  document.querySelectorAll("[data-theme-choice]").forEach((b) =>
    b.addEventListener("click", () => { setTheme(b.dataset.themeChoice); toast(`Theme set to ${b.querySelector("b").textContent}`); }));
  setTheme(document.documentElement.dataset.theme || "silk", { save: false });

  // ---------- fill forms from saved values
  function fill() {
    document.querySelectorAll("[data-settings-form] [name]").forEach((input) => {
      const v = values[input.name];
      if (input.type === "checkbox") input.checked = !!v;
      else if (input.type === "radio") input.checked = input.value === v;
      else if (input.type === "password") input.value = "";
      else if (v !== undefined) input.value = v ?? "";
    });
    document.querySelectorAll('[data-settings-form] input[type="password"]').forEach((i) => {
      i.placeholder = values[`${i.name}_set`] ? "Saved. Type a new one to replace it." : "";
    });
    toggleStatic();
  }

  function toggleStatic() {
    const mode = document.querySelector('[name="lan_mode"]:checked')?.value;
    document.querySelector(".static-only").hidden = mode !== "static";
  }
  document.querySelectorAll('[name="lan_mode"]').forEach((r) => r.addEventListener("change", toggleStatic));

  function renderSystem(s) {
    const up = s.uptime_s, d = Math.floor(up / 86400), h = Math.floor((up % 86400) / 3600), m = Math.floor((up % 3600) / 60);
    const rows = [
      ["Hardware", s.model],
      ["System", s.os],
      ["Hostname", s.hostname],
      ["Address", s.ip],
      ["Running for", d ? `${d} d ${h} h` : `${h} h ${m} min`],
      s.cpu_temp_c != null ? ["CPU temperature", `${s.cpu_temp_c} °C`] : null,
    ].filter(Boolean);
    $("sysinfo").innerHTML = rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
  }

  async function load() {
    const data = await api("/api/settings");
    values = data.values;
    fill();
    renderSystem(data.system);
    $("subnet-hint").textContent = `Leave blank to detect it. Right now that's ${data.detected_subnet}.`;
    $("scan-subnet").placeholder = data.detected_subnet;
    $("tz-list").innerHTML = data.timezones.map((z) => `<option value="${esc(z)}">`).join("");
    testPihole();
  }

  // ---------- save a section
  function collect(form) {
    const out = {};
    form.querySelectorAll("[name]").forEach((i) => {
      if (i.type === "checkbox") out[i.name] = i.checked;
      else if (i.type === "radio") { if (i.checked) out[i.name] = i.value; }
      else if (i.type === "number") out[i.name] = i.value === "" ? undefined : Number(i.value);
      else out[i.name] = i.value;
    });
    return out;
  }

  function showApply(form, apply) {
    form.querySelector(".apply-box")?.remove();
    if (!apply) return;
    const box = document.createElement("div");
    box.className = "apply-box";
    if (apply.executed) {
      const failed = apply.results.find((r) => !r.ok);
      box.innerHTML = failed
        ? `<p><b>Saved, but the Pi couldn't apply it.</b> ${esc(failed.output || "")}</p>`
        : `<p><b>Applied to the Pi.</b></p>`;
    } else {
      box.innerHTML = `<p><b>Saved.</b> SPIder isn't allowed to change the Pi's system settings itself, so run these on the Pi to apply them:</p>
        <pre><code>${apply.commands.map(esc).join("\n")}</code></pre>
        <p class="muted small">To let SPIder do this for you, run it directly on the Pi (not in Docker) as root with <code>SPIDER_ALLOW_SYSTEM_CHANGES=true</code>.</p>`;
    }
    form.querySelector("fieldset").appendChild(box);
  }

  document.querySelectorAll("[data-settings-form]").forEach((form) => form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const result = form.querySelector(".result"), btn = form.querySelector('button[type="submit"]');
    form.querySelectorAll("[data-error-for]").forEach((x) => (x.textContent = ""));
    form.querySelectorAll("[aria-invalid]").forEach((x) => x.removeAttribute("aria-invalid"));
    result.textContent = ""; btn.disabled = true;
    try {
      const r = await api("/api/settings", { method: "PUT", body: JSON.stringify(collect(form)) });
      values = r.values; fill();
      result.textContent = r.changed.length ? "Saved" : "Nothing changed";
      if (form.querySelector('[name="pihole_url"]')) testPihole();
      if (form.querySelector('[name="update_repo"]')) loadVersion();
      showApply(form, r.apply);
    } catch (err) {
      const slot = err.field && form.querySelector(`[data-error-for="${err.field}"]`);
      if (slot) { slot.textContent = err.message; form.querySelector(`[name="${err.field}"]`)?.setAttribute("aria-invalid", "true"); }
      else result.textContent = err.message;
    } finally { btn.disabled = false; }
  }));

  // ---------- version and updates
  function renderUpdate(v, u) {
    const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "not yet");
    $("build-info").innerHTML = [
      ["Version", v.version],
      ["Build", v.short ? `${v.short}${v.branch ? ` (${v.branch})` : ""}` : "unknown"],
      ["Latest on GitHub", u && !u.error ? (u.available ? `${u.latest}, ${u.behind_by} newer` : "You're up to date") : "–"],
      ["Last checked", when(u && u.checked_at)],
    ].map(([k, x]) => `<div><dt>${esc(k)}</dt><dd>${esc(x)}</dd></div>`).join("");
    const box = $("update-result");
    if (!u) { box.innerHTML = ""; return; }
    if (u.error) { box.innerHTML = `<p class="ph-status bad">${esc(u.error)}</p>`; return; }
    box.innerHTML = u.available
      ? `<p class="ph-status ok"><b>${u.behind_by} newer change${u.behind_by === 1 ? "" : "s"} on GitHub.</b> ${
          v.can_update ? "Use <b>Update now</b> at the bottom of the page." : "In Portainer, open the spider stack and click <b>Pull and redeploy</b>, or add the webhook below to update from here."}</p>
         <ol class="changes">${u.changes.map((c) => `<li><code>${esc(c.short)}</code> ${esc(c.title)}</li>`).join("")}</ol>`
      : `<p class="ph-status ok">You're running the latest build.</p>`;
  }
  async function loadVersion() {
    try { const v = await api("/api/version"); renderUpdate(v, v.update); } catch (e) {}
  }
  $("update-check")?.addEventListener("click", async () => {
    const b = $("update-check"); b.disabled = true; b.textContent = "Checking…";
    try { const u = await api("/api/version/check", { method: "POST" }); const v = await api("/api/version"); renderUpdate(v, u); SP.refreshVersion?.(); }
    catch (e) { toast(e.message); }
    b.disabled = false; b.textContent = "Check for updates";
  });
  loadVersion();

  // ---------- Pi-hole connection status
  async function testPihole() {
    const box = $("pihole-status");
    if (!values.pihole_url) { box.hidden = true; return; }
    box.hidden = false; box.className = "ph-status"; box.textContent = "Testing the connection…";
    try {
      const p = await api("/api/pihole");
      if (p.status === "ok") {
        box.classList.add("ok");
        box.textContent = `Connected to Pi-hole v${p.version}. ${p.percent_blocked}% of today's lookups blocked.`;
      } else {
        box.classList.add("bad");
        const lead = { auth_failed: "Pi-hole turned down the password.", offline: "Can't connect.", not_configured: "No Pi-hole address saved yet." }[p.status] || "Not connected.";
        box.innerHTML = `<b>${esc(lead)}</b> ${esc(p.error || "")}`;
      }
    } catch (e) { box.classList.add("bad"); box.textContent = e.message; }
  }
  $("pihole-test")?.addEventListener("click", testPihole);

  $("test-alert")?.addEventListener("click", async () => {
    const result = $("test-alert").parentElement.querySelector(".result");
    result.textContent = "Sending…";
    try { await api("/api/alerts/test", { method: "POST" }); result.textContent = "Sent. Check your phone."; }
    catch (err) { result.textContent = err.message; }
  });

  // ---------- your account
  $("password-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, result = f.querySelector(".result");
    try {
      await api("/api/me/password", { method: "PUT", body: JSON.stringify({ current: f.current.value, new: f.new.value }) });
      f.reset(); result.textContent = "Password changed";
    } catch (err) { result.textContent = err.message; }
  });

  // ---------- users (admin)
  async function loadUsers() {
    if (!isAdmin || !$("users-body")) return;
    const users = await api("/api/users");
    const me = document.querySelector(".who b")?.textContent;
    $("users-body").innerHTML = users.map((u) => `
      <tr>
        <td><span class="avatar small" aria-hidden="true">${esc(u.username[0].toUpperCase())}</span> ${esc(u.username)}${u.username === me ? ' <span class="muted">(you)</span>' : ""}</td>
        <td class="dim">${u.role === "admin" ? "Admin" : "Viewer"}</td>
        <td class="act">${u.username === me ? "" : `<button type="button" class="quiet danger" data-remove="${u.id}" data-name="${esc(u.username)}">Remove</button>`}</td>
      </tr>`).join("");
  }

  $("users-body")?.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-remove]"); if (!b) return;
    if (!confirm(`Remove ${b.dataset.name}? They won't be able to sign in any more.`)) return;
    try { await api(`/api/users/${b.dataset.remove}`, { method: "DELETE" }); toast(`Removed ${b.dataset.name}`); loadUsers(); }
    catch (err) { toast(err.message); }
  });

  $("add-user-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, result = f.querySelector(".result");
    try {
      const u = await api("/api/users", { method: "POST", body: JSON.stringify({ username: f.username.value, password: f.password.value, role: f.role.value }) });
      f.reset(); result.textContent = `Added ${u.username}`; loadUsers();
    } catch (err) { result.textContent = err.message; }
  });

  // ---------- highlight the section you're reading in the sub-nav
  const links = [...document.querySelectorAll(".settings-nav a")];
  const obs = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (en.isIntersecting) links.forEach((a) => a.toggleAttribute("aria-current", a.hash === `#${en.target.id}`));
    });
  }, { rootMargin: "-20% 0px -70% 0px" });
  document.querySelectorAll(".set").forEach((s) => obs.observe(s));

  load()
    .then(() => {
      // Arrived via a link like /settings#pihole: jump there once the forms have filled in.
      const id = location.hash.slice(1);
      const target = id ? document.getElementById(id) : null;
      if (target) target.scrollIntoView({ block: "start" });
    })
    .catch((e) => toast(e.message));
  loadUsers().catch(console.error);
})();
