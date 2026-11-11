// "Next to jump" bar shown at the top of every page.
// Loads today's races, shows the soonest open ones with a live countdown,
// and follows the race-type / region filters chosen on the home page.
(function () {
  const list = document.getElementById("jumpbar-list");
  if (!list) return;

  const TYPE_NAMES = { horse: "Horses", greyhound: "Greyhounds", harness: "Harness" };
  const MAX_RACES = 12;
  let races = [];      // [{ meeting, race }] for today's open races
  let loaded = false;

  function todayISO() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  // Same saved filters as the home page (per browser; defaults if storage is blocked).
  function filters() {
    try {
      return { type: localStorage.getItem("home.type") || "all",
               region: localStorage.getItem("home.region") || "all" };
    } catch {
      return { type: "all", region: "all" };
    }
  }

  function countdown(epoch) {
    const mins = Math.round((epoch * 1000 - Date.now()) / 60000);
    if (mins <= 0) return "Now";
    if (mins < 60) return mins + "m";
    return Math.floor(mins / 60) + "h " + (mins % 60) + "m";
  }

  // The race being viewed on the race page (its link ends in the event id).
  function currentRaceId() {
    const link = new URLSearchParams(location.search).get("race") || "";
    const m = link.match(/-(\d{6,})\/?(?:[?#].*)?$/);
    return m ? Number(m[1]) : null;
  }

  // The bar's own dropdowns. They share the home page's saved filters, so changing
  // either one changes both (the home page listens for "home:filters").
  const typeSelect = document.getElementById("jumpbar-type");
  const regionSelect = document.getElementById("jumpbar-region");
  function onFilterChange() {
    try {
      localStorage.setItem("home.type", typeSelect.value);
      localStorage.setItem("home.region", regionSelect.value);
    } catch {}
    render();
    window.dispatchEvent(new Event("home:filters"));
  }
  typeSelect?.addEventListener("change", onFilterChange);
  regionSelect?.addEventListener("change", onFilterChange);

  function render() {
    const { type, region } = filters();
    if (typeSelect) typeSelect.value = type;
    if (regionSelect) regionSelect.value = region;
    const current = currentRaceId();
    const shown = races
      .filter(({ meeting }) =>
        (type === "all" || meeting.type === type) &&
        (region === "all" || (region === "intl") === meeting.international))
      .sort((a, b) => a.race.start - b.race.start)
      .slice(0, MAX_RACES);

    if (!shown.length) {
      const span = document.createElement("span");
      span.className = "jumpbar-empty";
      span.textContent = loaded ? "No more open races today." : "Loading…";
      list.replaceChildren(span);
      return;
    }

    list.replaceChildren(...shown.map(({ meeting, race }) => {
      const a = document.createElement("a");
      const mins = (race.start * 1000 - Date.now()) / 60000;
      a.className = "jb" + (mins < 2 ? " soon" : "") + (race.id === current ? " current" : "");
      a.href = "/race?race=" + encodeURIComponent(race.link);
      a.title = `${TYPE_NAMES[meeting.type] || meeting.type} · ${meeting.region} · ` +
        new Date(race.start * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      const when = document.createElement("span");
      when.className = "when";
      when.textContent = countdown(race.start);
      a.append(when, ` ${meeting.name} R${race.number}`);
      return a;
    }));
  }

  async function refresh() {
    try {
      const resp = await fetch("/api/races?date=" + todayISO());
      if (!resp.ok) return;  // keep showing the last list
      const data = await resp.json();
      races = data.meetings.flatMap(meeting =>
        meeting.races.filter(r => r.status === "open").map(race => ({ meeting, race })));
      loaded = true;
      render();
    } catch {
      // Network hiccup: try again on the next refresh.
    }
  }

  // The home page fires this when its filters change.
  window.addEventListener("jumpbar:filters", render);
  setInterval(render, 15000);   // tick the countdowns
  setInterval(refresh, 60000);  // pick up races that have jumped
  refresh();
})();
