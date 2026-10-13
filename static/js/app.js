// Shared dashboard helpers (modal close, tab switches, toasts).
// Full library: chat rendering, insights chart (loaded via pro.js additions below).
document.addEventListener("click", (e) => {
  const bg = e.target.classList && e.target.classList.contains("modal-bg") ? e.target : null;
  if (bg) bg.classList.remove("open");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.querySelectorAll(".modal-bg.open").forEach((m) => m.classList.remove("open"));
});
function toast(msg) {
  const d = document.createElement("div");
  d.className = "flash ok";
  d.textContent = msg;
  document.querySelector(".content")?.prepend(d);
  setTimeout(() => d.remove(), 3500);
}

// ---- Theme (light / dark / system) ----
const Theme = {
  get() { try { return localStorage.getItem("relay-theme") || document.documentElement.getAttribute("data-theme") || "light"; } catch (e) { return "light"; } },
  resolve(pref) {
    if (pref === "system") return (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
    return pref === "dark" ? "dark" : "light";
  },
  apply(pref, persist) {
    document.documentElement.setAttribute("data-theme", Theme.resolve(pref));
    try { localStorage.setItem("relay-theme", pref); } catch (e) {}
    document.querySelectorAll("[data-theme-choice]").forEach((b) => {
      b.classList.toggle("on", b.getAttribute("data-theme-choice") === pref);
    });
    if (persist !== false) Theme.save(pref);
  },
  toggle() { Theme.apply(Theme.resolve(Theme.get()) === "dark" ? "light" : "dark"); },
  save(pref) {
    // best-effort persist to the server; ignore failures (e.g. logged out)
    try {
      fetch("/settings/theme", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme: pref }),
      }).catch(() => {});
    } catch (e) {}
  },
};
window.Theme = Theme;
document.addEventListener("click", (e) => {
  const t = e.target.closest && e.target.closest("#themeToggle");
  if (t) { Theme.toggle(); return; }
  const choice = e.target.closest && e.target.closest("[data-theme-choice]");
  if (choice) { Theme.apply(choice.getAttribute("data-theme-choice")); }
});
// follow OS changes when preference is "system"
if (window.matchMedia) {
  const mq = matchMedia("(prefers-color-scheme: dark)");
  const onSys = () => { if (Theme.get() === "system") Theme.apply("system", false); };
  mq.addEventListener ? mq.addEventListener("change", onSys) : mq.addListener && mq.addListener(onSys);
}
