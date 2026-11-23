// Devices page: the full list, naming and marking (admins only).
(() => {
  const { $, esc, ago, api, toast, isAdmin } = SP;
  const dateOf = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "–";

  async function load() {
    const devices = await api("/api/devices");
    const showOffline = $("show-offline").checked, onlyStrangers = $("only-strangers").checked;
    const rows = devices.filter((d) => (showOffline || d.online) && (!onlyStrangers || !d.known));

    $("devices-body").innerHTML = rows.length ? rows.map((d) => `
      <tr id="${esc(d.mac)}" data-mac="${esc(d.mac)}" class="${d.online ? "live" : "gone"} ${d.known ? "mine" : "stranger"}">
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
        <td class="act">${!isAdmin ? (d.known ? `<span class="dim">Yours</span>` : "")
          : d.known
            ? `<button type="button" class="quiet" data-known="0" data-mac="${esc(d.mac)}">Yours</button>`
            : `<button type="button" class="claim" data-known="1" data-mac="${esc(d.mac)}">Mark as mine</button>`}</td>
      </tr>`).join("")
      : `<tr><td colspan="8" class="empty">No devices match. Turn the filters off to see everything.</td></tr>`;

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

  $("show-offline").addEventListener("change", load);
  $("only-strangers").addEventListener("change", load);
  document.addEventListener("spider:refresh", load);

  const refresh = () => { if (!document.activeElement?.classList.contains("name")) load().catch(console.error); };
  load().catch(console.error);
  setInterval(refresh, 15_000);
})();
