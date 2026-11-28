// Honeypot page: who knocked on the fake doors, and what they tried.
(() => {
  const { $, esc, ago, api, toast, tidyVendor, isAdmin, signedIn } = SP;
  let data = null;

  const when = (iso) => new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const whoOf = (s) => s.test ? "SPIder itself (a test)"
    : s.name || (s.vendor && !s.vendor.startsWith("Private") ? tidyVendor(s.vendor) : s.vendor ? `A phone or laptop at ${s.ip}` : `A device at ${s.ip}`);

  function verdict(s) {
    if (s.test) return { cls: "test", text: "Test knock. Everything's working." };
    if (s.known) return { cls: "known", text: "This is one of your devices. It shouldn't do this: it may be infected, or running a network scan app." };
    if (s.on_list) return { cls: "stranger", text: "Not marked as known. Find out what it is, and block it if you don't recognise it." };
    return { cls: "stranger", text: "SPIder has never seen this device in a scan. It may have just joined, or be hiding." };
  }

  function tried(v) {
    const bits = [];
    for (const t of v.tries || []) {
      bits.push(`<span class="hp-try">${t.username ? `<b class="mono">${esc(t.username)}</b>` : "<i>no name</i>"}${t.password ? ` / <span class="mono">${esc(t.password)}</span>` : ""}</span>`);
    }
    if (v.client) bits.push(`<span class="dim">using <span class="mono">${esc(v.client)}</span></span>`);
    if (v.request) bits.push(`<span class="dim">asked for <span class="mono">${esc(v.request)}</span></span>`);
    if (!bits.length && v.first_bytes) bits.push(`<span class="dim">opened with <span class="mono">${esc(v.first_bytes)}</span></span>`);
    if (v.note) bits.push(`<span class="dim">${esc(v.note)}</span>`);
    return bits.length ? bits.join(" ") : `<span class="dim">connected, then left without saying anything</span>`;
  }

  function sourceCard(s) {
    const v = verdict(s);
    const canBlock = isAdmin && s.mac && !s.test && !s.protected && !s.block;
    return `<article class="hp-src ${v.cls}${s.unseen ? " unseen" : ""}">
      <header>
        <div>
          <h2>${esc(whoOf(s))}${s.scan && !s.test ? ` <span class="hp-badge">scanning</span>` : ""}</h2>
          <p class="muted small mono">${esc(s.ip)}${s.mac ? ` · ${esc(s.mac)}` : ""}${s.kind ? ` · ${esc(s.kind)}` : ""}</p>
        </div>
        <p class="hp-count"><b>${s.knocks}</b> knock${s.knocks === 1 ? "" : "s"}<br><span class="muted small">last ${esc(ago(s.last))}</span></p>
      </header>
      <p class="hp-verdict">${esc(v.text)}</p>
      <ol class="hp-visits">${s.visits.map((x) => `
        <li${x.seen ? "" : ' class="new"'}><time datetime="${esc(x.last)}">${esc(when(x.last))}</time>
          <span class="hp-door">${esc(x.label)} <span class="mono dim">:${x.port}</span>${x.count > 1 ? ` <span class="dim">×${x.count}</span>` : ""}</span>
          <span class="hp-what">${tried(x)}</span></li>`).join("")}
      </ol>
      <footer>
        ${s.block ? `<span class="muted small">Blocked in Pi-hole</span>` : ""}
        ${canBlock ? `<button type="button" class="secondary" data-block="${esc(s.mac)}" data-who="${esc(whoOf(s))}">Block in Pi-hole</button>` : ""}
        ${s.mac && !s.test ? `<a class="small" href="/traffic#device=${encodeURIComponent(s.mac)}">See its traffic</a>` : ""}
        ${signedIn && s.unseen ? `<button type="button" class="quiet" data-seen="${esc(s.key)}">Mark as seen</button>` : ""}
      </footer>
    </article>`;
  }

  function render() {
    const open = data.listeners.filter((l) => l.status === "listening");
    $("hp-doors").innerHTML = data.listeners.map((l) =>
      `<li class="door ${esc(l.status)}" title="${esc(l.error || (l.status === "off" ? "Turned off" : "Listening"))}">${esc(l.label)} <span class="mono">${l.port}</span>${l.error ? ` <span class="small">(${esc(l.error)})</span>` : ""}</li>`).join("");
    const real = data.sources.filter((s) => !s.test);
    const hero = $("hp-hero");
    if (!data.enabled) hero.textContent = "The honeypot is turned off.";
    else if (!open.length) hero.textContent = "The honeypot couldn't open any of its doors.";
    else if (data.unseen) hero.innerHTML = `<b class="hp-alarm">${data.unseen} new knock${data.unseen === 1 ? "" : "s"}</b> on the fake doors. Have a look below.`;
    else if (real.length) hero.textContent = `Quiet now. ${data.knocks_week} knock${data.knocks_week === 1 ? "" : "s"} in the last week, all seen.`;
    else hero.textContent = `All quiet. ${open.length} fake door${open.length === 1 ? " is" : "s are"} open and nothing has knocked.`;
    const seenBtn = $("hp-seen");
    if (seenBtn) seenBtn.hidden = !data.unseen;
    $("hp-list").innerHTML = data.sources.length ? data.sources.map(sourceCard).join("")
      : `<p class="muted empty">Nothing has knocked in the last 30 days. That's what you want.</p>`;
  }

  async function load() { data = await api("/api/honeypot"); render(); }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    try {
      if (b.id === "hp-seen" || b.dataset.seen) {
        await api("/api/honeypot/seen", { method: "POST", body: JSON.stringify({ key: b.dataset.seen || null }) });
        SP.refreshVitals?.();
      } else if (b.id === "hp-test") {
        b.disabled = true;
        const r = await api("/api/honeypot/test", { method: "POST" });
        toast(`Knocked on port ${r.port}. It should appear below.`);
        b.disabled = false;
      } else if (b.dataset.block) {
        if (!confirm(`Block ${b.dataset.who} until you unblock it? Its apps and websites will stop working.`)) return;
        await api(`/api/devices/${encodeURIComponent(b.dataset.block)}/block`, { method: "PUT", body: JSON.stringify({ minutes: null }) });
        toast(`${b.dataset.who} is blocked`);
      } else return;
      await load();
    } catch (err) { toast(err.message); b.disabled = false; }
  });

  document.addEventListener("spider:refresh", load);
  load().catch(console.error);
  setInterval(() => { if (!document.hidden) load().catch(console.error); }, 10_000);
})();
