// Devices page: the full list, naming and marking (admins only).
(() => {
  const { $, esc, ago, api, toast, isAdmin } = SP;
  const dateOf = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "–";

  let access = { available: false, reason: null };

  // "Until 7am" = the next 07:00 in local time.
  function minutesUntilMorning() {
    const now = new Date(), m = new Date(now);
    m.setHours(7, 0, 0, 0);
    if (m <= now) m.setDate(m.getDate() + 1);
    return Math.max(5, Math.round((m - now) / 60000));
  }

  const left = (until) => {
    const secs = Math.max(0, Math.round((new Date(until) - Date.now()) / 1000));
    if (secs < 120) return `${secs} s left`;
    const mins = Math.round(secs / 60);
    if (mins >= 120) return `${Math.round(mins / 60)} h left`;
    return mins >= 60 ? `1 h ${mins - 60} min left` : `${mins} min left`;
  };

  function accessCell(d) {
    const b = d.block;
    const state = b ? (b.until ? `Paused, ${left(b.until)}` : "Blocked") : "Allowed";
    if (d.protected && !b) return `<span class="dim" title="${esc(d.protected)}">Always on</span>`;
    if (!isAdmin) return `<span class="access-state ${b ? "is-off" : ""}">${state}</span>`;
    const disabled = !access.available && !b;
    return `
      <select class="access" data-mac="${esc(d.mac)}" aria-label="Internet for ${esc(d.name || d.ip)}" ${disabled ? `disabled title="${esc(access.reason || "")}"` : ""}>
        <option value="" selected>${b ? state : "Allowed"}</option>
        ${b ? `<option value="allow">Allow again now</option>` : ""}
        <option value="s30">Pause for 30 seconds (test)</option>
        <option value="30">Pause for 30 minutes</option>
        <option value="60">Pause for 1 hour</option>
        <option value="120">Pause for 2 hours</option>
        <option value="morning">Pause until 7am</option>
        <option value="block">Block until I unblock it</option>
      </select>`;
  }

  async function load() {
    const [devices, blocks] = await Promise.all([api("/api/devices"), api("/api/blocks").catch(() => null)]);
    if (blocks) access = blocks;
    $("access-note").hidden = access.available || !isAdmin;
    $("access-note").textContent = access.reason || "";
    const showOffline = $("show-offline").checked, onlyStrangers = $("only-strangers").checked;
    const rows = devices.filter((d) => (showOffline || d.online) && (!onlyStrangers || !d.known));

    $("devices-body").innerHTML = rows.length ? rows.map((d) => `
      <tr id="${esc(d.mac)}" data-mac="${esc(d.mac)}" class="${d.online ? "live" : "gone"} ${d.known ? "mine" : "stranger"} ${d.block ? "paused" : ""}">
        <td><span class="dot" title="${d.online ? "Online" : "Offline"}"></span></td>
        <td>${isAdmin
          ? `<input class="name" data-mac="${esc(d.mac)}" value="${esc(d.name)}" maxlength="40"
                    placeholder="${esc(d.hostname || "Add a name")}" aria-label="Name for ${esc(d.ip)}">`
          : `<span class="${d.name ? "" : "muted"}">${esc(d.name || d.hostname || "Unnamed")}</span>`}</td>
        <td class="data">${esc(d.ip)}</td>
        <td>${esc(d.vendor || "Unknown")}</td>
        <td class="data dim">${esc(d.mac)}</td>
        <td class="dim">${dateOf(d.first_seen)}</td>
        <td class="dim">${ago(d.last_seen)}</td>
        <td class="access-cell">${accessCell(d)}</td>
        <td class="act">${!isAdmin ? (d.known ? `<span class="dim">Yours</span>` : "")
          : d.known
            ? `<button type="button" class="quiet" data-known="0" data-mac="${esc(d.mac)}">Yours</button>`
            : `<button type="button" class="claim" data-known="1" data-mac="${esc(d.mac)}">Mark as mine</button>`}</td>
      </tr>`).join("")
      : `<tr><td colspan="9" class="empty">No devices match. Turn the filters off to see everything.</td></tr>`;

    // Arriving from a click on the web: highlight that device.
    if (location.hash && !load.jumped) {
      load.jumped = true;
      const row = document.getElementById(decodeURIComponent(location.hash.slice(1)));
      if (row) {
        row.scrollIntoView({ block: "center" });
        row.classList.add("flash");
        row.querySelector("input.name")?.focus({ preventScroll: true });
      }
    }
  }

  $("devices-body").addEventListener("change", async (e) => {
    if (e.target.classList.contains("access")) return changeAccess(e.target);
    if (!e.target.classList.contains("name")) return;
    try {
      await api(`/api/devices/${encodeURIComponent(e.target.dataset.mac)}`, { method: "PATCH", body: JSON.stringify({ name: e.target.value }) });
      toast("Name saved");
    } catch (err) { toast(err.message); }
  });

  $("devices-body").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-known]");
    if (!btn) return;
    try {
      await api(`/api/devices/${encodeURIComponent(btn.dataset.mac)}`, { method: "PATCH", body: JSON.stringify({ known: btn.dataset.known === "1" }) });
      load();
    } catch (err) { toast(err.message); }
  });

  async function changeAccess(sel) {
    const v = sel.value, mac = sel.dataset.mac;
    const row = sel.closest("tr"), who = row.querySelector("input.name")?.value || row.querySelector("input.name")?.placeholder || "this device";
    if (!v) return;
    if (v === "block" && !confirm(`Block ${who} until you unblock it? Its apps and websites will stop working.`)) { sel.value = ""; return; }
    sel.disabled = true;
    try {
      if (v === "allow") {
        await api(`/api/devices/${encodeURIComponent(mac)}/block`, { method: "DELETE" });
        toast(`${who} can use the internet again`);
      } else {
        const body = v === "block" ? { minutes: null } : v === "s30" ? { seconds: 30 } : { minutes: v === "morning" ? minutesUntilMorning() : Number(v) };
        await api(`/api/devices/${encodeURIComponent(mac)}/block`, { method: "PUT", body: JSON.stringify(body) });
        toast(v === "block" ? `${who} is blocked` : `${who} is paused`);
      }
    } catch (err) { toast(err.message); }
    load();
  }

  $("show-offline").addEventListener("change", load);
  $("only-strangers").addEventListener("change", load);
  document.addEventListener("spider:refresh", load);

  const refresh = () => { const a = document.activeElement; if (!a?.classList.contains("name") && !a?.classList.contains("access")) load().catch(console.error); };
  load().catch(console.error);
  setInterval(refresh, 15_000);
})();
