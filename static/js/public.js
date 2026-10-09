// Public chat page (/chat/<slug>) client.
(function () {
  const msgs = document.getElementById("msgs");
  const sugBox = document.getElementById("suggs");
  const inp = document.getElementById("inp");
  let conv = null, visitor = "v" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  let email = "", uname = "", gotEmail = false;

  function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
  function addMsg(who, text) {
    const m = document.createElement("div");
    m.className = "msg " + (who === "u" ? "user" : "bot");
    m.innerHTML = esc(text).replace(/\n/g, "<br>");
    msgs.appendChild(m); msgs.scrollTop = msgs.scrollHeight;
  }
  async function init() {
    try {
      const r = await fetch("/api/public/config?bot=" + encodeURIComponent(SLUG));
      const j = await r.json();
      if (j.error) { addMsg("b", "This assistant is unavailable."); return; }
      addMsg("b", j.greeting || "Hi! How can I help?");
      (j.suggestions || []).forEach((s) => {
        const b = document.createElement("button"); b.textContent = s;
        b.onclick = () => { inp.value = s; send(); }; sugBox.appendChild(b);
      });
      if (j.need_email) setTimeout(() => addMsg("b", "First, could you share your email so we can follow up?"), 600);
    } catch (e) { addMsg("b", "Could not load assistant. Please refresh."); }
  }
  async function send() {
    const text = inp.value.trim(); if (!text) return; inp.value = "";
    if (NEED_EMAIL && !gotEmail && !email) {
      if (!/^\S+@\S+\.\S+$/.test(text)) { addMsg("b", "Could you share your email first so we can follow up?"); return; }
      email = text; gotEmail = true; addMsg("u", text);
      addMsg("b", "Thanks! What can I help you with today?"); return;
    }
    addMsg("u", text);
    try {
      const r = await fetch("/api/public/chat", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bot: SLUG, message: text, conversation_id: conv, visitor_key: visitor, name: uname, email }) });
      const j = await r.json(); conv = j.conversation_id || conv;
      addMsg("b", j.reply || j.error || "Sorry, something went wrong.");
    } catch (e) { addMsg("b", "Network error — please try again."); }
  }
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  window.send = send;
  init();
})();
