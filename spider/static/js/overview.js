// Overview page: the web, a plain-English summary, Pi-hole at a glance, latest activity.
(() => {
  const { $, esc, ago, fmt, api, eventLine } = SP;

  async function loadDevices() {
    const devices = await api("/api/devices");
    SpiderWeb.render(devices);
    const online = devices.filter((d) => d.online).length;
    const strangers = devices.filter((d) => !d.known).length;
    $("s-online").textContent = online;
    $("s-online-text").textContent = online === 1 ? "device is on your web." : "devices are on your web.";
    $("s-unknown").innerHTML = !devices.length
      ? "Nothing found yet. The first scan runs when SPIder starts."
      : strangers
        ? `${strangers} of ${devices.length} ${strangers === 1 ? "hasn't" : "haven't"} been marked as yours. <a href="/devices">Go through them</a> so anything new stands out.`
        : `All ${devices.length} are marked as yours. New arrivals will show up wrapped in silk.`;
  }

  async function loadEvents() {
    const list = $("events");
    const events = await api(`/api/events?limit=${list.dataset.limit || 5}`);
    list.innerHTML = events.length
      ? events.map((e) => `
          <li class="ev ${esc(e.type)}">
            <span class="ev-what">${eventLine(e)}</span>
            <span class="ev-when">${ago(e.ts)}</span>
          </li>`).join("")
      : `<li class="muted">Nothing yet. Changes on your network appear here.</li>`;
  }

  async function loadPihole() {
    const p = await api("/api/pihole");
    const box = $("pihole-body");
    if (p.status !== "ok") {
      const msg = { not_configured: "Not connected yet. An admin can add it in Settings.",
        offline: "Can't reach your Pi-hole right now.", auth_failed: "Your Pi-hole turned down the password." };
      box.innerHTML = `<p class="muted">${msg[p.status] || esc(p.status)}</p>`;
      return;
    }
    const pct = Math.max(0, Math.min(100, Number(p.percent_blocked) || 0));
    box.innerHTML = `
      <p class="pi-lead"><strong>${pct}%</strong> of today's ${fmt(p.queries_today)} lookups were blocked.</p>
      <div class="meter" role="img" aria-label="${pct}% blocked"><span style="width:${pct}%"></span></div>`;
  }

  const refresh = () => { loadDevices().catch(console.error); loadEvents().catch(console.error); };
  document.addEventListener("spider:refresh", refresh);
  refresh(); loadPihole().catch(console.error);
  setInterval(refresh, 10_000);
  setInterval(() => loadPihole().catch(console.error), 30_000);
})();
