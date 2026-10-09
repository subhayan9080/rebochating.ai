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
