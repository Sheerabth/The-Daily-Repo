// Theme toggle, filter pills and search. Pages read fine without any of it.
const root = document.documentElement;
const content = document.getElementById("content");
const results = document.getElementById("results");
const q = document.getElementById("q");

document.getElementById("theme").addEventListener("click", () => {
  const dark = (root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
});

// Filters: language must match exactly; signal "rec" also lets "must" through.
const filters = { lang: "", signal: "" };
document.querySelectorAll(".pill").forEach((pill) =>
  pill.addEventListener("click", () => {
    filters[pill.dataset.filter] = pill.dataset.value;
    document.querySelectorAll(`.pill[data-filter="${pill.dataset.filter}"]`).forEach((p) => p.classList.toggle("on", p === pill));
    content.querySelectorAll(".story").forEach((s) => {
      const lang = !filters.lang || s.dataset.lang === filters.lang;
      const signal = !filters.signal || s.dataset.signal === filters.signal || (filters.signal === "rec" && s.dataset.signal === "must");
      s.hidden = !(lang && signal);
    });
  }),
);

// Search over every edition (data/all.json, loaded on first keystroke).
let all;

function el(tag, props, ...kids) {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids);
  return e;
}

function hit(r) {
  const meta = el("p", { className: "meta" }, "Featured ", el("a", { href: `${r.date}.html`, textContent: r.date }), ` · ★ ${r.stars.toLocaleString()}`);
  if (r.language) meta.append(` · ${r.language}`);
  return el("article", { className: "story" },
    el("div", { className: "byline", textContent: `By ${r.owner}` }),
    el("h2", {}, el("a", { href: r.url, textContent: r.name })),
    el("p", { className: "dek", textContent: r.description }),
    meta);
}

q.addEventListener("input", async () => {
  const terms = q.value.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) {
    results.hidden = true;
    content.hidden = false;
    return;
  }
  all ??= await fetch("data/all.json").then((res) => res.json());
  const hits = all.filter((r) => {
    const hay = [r.name, r.description, r.language, ...r.also.map((x) => x.name)].join(" ").toLowerCase();
    return terms.every((t) => hay.includes(t));
  });
  results.replaceChildren(
    el("p", { className: "empty", textContent: `${hits.length} result${hits.length === 1 ? "" : "s"} for “${q.value.trim()}”` }),
    el("div", { className: "columns" }, ...hits.slice(0, 100).map(hit)),
  );
  results.hidden = false;
  content.hidden = true;
});
