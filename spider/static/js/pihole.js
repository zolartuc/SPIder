// Pi-hole page: today's numbers in detail.
(() => {
  const { $, esc, fmt, api, isAdmin } = SP;

  async function load() {
    const p = await api("/api/pihole");
    const box = $("pihole-big");
    if (p.status !== "ok") {
      const fix = isAdmin ? ` <a href="/settings#pihole">Open Pi-hole settings</a>.` : " An admin can fix this in Settings.";
      const msg = {
        not_configured: "SPIder isn't connected to a Pi-hole yet.",
        offline: "SPIder can't reach your Pi-hole. Check it's switched on and the address is right.",
        auth_failed: "Your Pi-hole turned down the password. Use an app password (v6) or the API token (v5).",
      };
      box.className = "pihole-big muted";
      box.innerHTML = `<p class="lead-text">${msg[p.status] || esc(p.status)}${fix}</p>`;
      return;
    }
    const pct = Math.max(0, Math.min(100, Number(p.percent_blocked) || 0));
    box.className = "pihole-big";
    box.innerHTML = `
      <p class="pi-hero"><strong>${pct}%</strong> of today's lookups were blocked.</p>
      <div class="meter big" role="img" aria-label="${pct}% blocked"><span style="width:${pct}%"></span></div>
      <dl class="stats wide">
        <div><dt>Lookups today</dt><dd>${fmt(p.queries_today)}</dd></div>
        <div><dt>Blocked</dt><dd>${fmt(p.blocked_today)}</dd></div>
        <div><dt>On the blocklist</dt><dd>${fmt(p.domains_on_blocklist)}</dd></div>
        <div><dt>Devices using it</dt><dd>${fmt(p.active_clients)}</dd></div>
        <div><dt>Pi-hole version</dt><dd>v${esc(p.version)}</dd></div>
      </dl>`;
  }

  load().catch(console.error);
  setInterval(() => load().catch(console.error), 30_000);
})();
