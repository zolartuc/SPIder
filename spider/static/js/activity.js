// Activity page: everything that happened, grouped by day, with filters.
(() => {
  const { $, esc, api, eventLine } = SP;
  let filter = "";
  let events = [];

  const dayLabel = (d) => {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const that = new Date(d); that.setHours(0, 0, 0, 0);
    const diff = Math.round((today - that) / 86400000);
    if (diff === 0) return "Today";
    if (diff === 1) return "Yesterday";
    return that.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  };
  const time = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

  function render() {
    const shown = events.filter((e) => !filter || e.type === filter);
    if (!shown.length) {
      $("timeline").innerHTML = `<p class="muted empty">${filter ? "Nothing of that kind yet." : "Nothing has happened yet. Changes on your network appear here."}</p>`;
      return;
    }
    const groups = new Map();
    for (const e of shown) {
      const k = dayLabel(e.ts);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(e);
    }
    $("timeline").innerHTML = [...groups].map(([day, list]) => `
      <section class="day">
        <h2>${esc(day)}</h2>
        <ol class="events">${list.map((e) => `
          <li class="ev ${esc(e.type)}">
            <time datetime="${esc(e.ts)}">${time(e.ts)}</time>
            <span class="ev-what">${eventLine(e)}</span>
          </li>`).join("")}
        </ol>
      </section>`).join("");
  }

  document.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
    filter = b.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render();
  }));

  async function load() { events = await api("/api/events?limit=300"); render(); }
  document.addEventListener("spider:refresh", load);
  load().catch(console.error);
  setInterval(() => load().catch(console.error), 20_000);
})();
