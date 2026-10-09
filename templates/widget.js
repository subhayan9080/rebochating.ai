/* Relay embeddable widget v2. Usage: <script src="BASE/widget.js" data-bot="SLUG"></script> */
(function () {
  var el = document.currentScript;
  var base = (el && el.src ? el.src.replace(/\/widget\.js.*$/, "") : window.location.origin);
  var slug = (el && el.getAttribute("data-bot")) || window.RELAY_SLUG || "";
  var position = (el && el.getAttribute("data-position")) || "right";
  if (!slug) { console.error("[relay] missing data-bot slug"); return; }
  var color = "#4f46e5", botName = "Assistant", greeting = "Hi! How can I help?";
  var avatar = "", suggs = [], needEmail = false, conv = null, open = false, unread = 0;
  var visitor = "v" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  var side = position === "left" ? "left" : "right";

  var css = "#rw-fab{position:fixed;" + side + ":20px;bottom:20px;width:58px;height:58px;border-radius:50%;border:0;cursor:pointer;color:#fff;font-size:24px;box-shadow:0 10px 28px rgba(0,0,0,.28);z-index:999998;font-family:system-ui,sans-serif}"
    + "#rw-badge{position:fixed;" + side + ":14px;bottom:64px;min-width:22px;height:22px;border-radius:11px;background:#ef4444;color:#fff;font-size:12px;font-weight:700;display:none;align-items:center;justify-content:center;padding:0 6px;z-index:999999;font-family:system-ui,sans-serif}"
    + "#rw-panel{position:fixed;" + side + ":20px;bottom:90px;width:380px;max-width:calc(100vw - 40px);height:540px;max-height:calc(100vh - 120px);background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.28);display:none;flex-direction:column;overflow:hidden;z-index:999999;font-family:system-ui,-apple-system,sans-serif}"
    + "#rw-panel.open{display:flex}"
    + "#rw-head{padding:12px 14px;color:#fff;display:flex;gap:10px;align-items:center}"
    + "#rw-av{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.22);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:17px;flex:0 0 34px}"
    + "#rw-msgs{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:#f8fafc}"
    + ".rw-m{max-width:82%;padding:9px 13px;border-radius:13px;font-size:14px;line-height:1.5;white-space:pre-wrap;word-break:break-word}"
    + ".rw-b{background:#fff;border:1px solid #e2e8f0;align-self:flex-start;border-bottom-left-radius:4px}"
    + ".rw-u{align-self:flex-end;border-bottom-right-radius:4px}"
    + ".rw-t{align-self:flex-start;background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:10px 14px;font-size:14px;color:#94a3b8}"
    + "#rw-sug{display:flex;gap:6px;flex-wrap:wrap;padding:8px 12px 0;background:#f8fafc}"
    + "#rw-sug button{border:1px solid #e2e8f0;background:#fff;border-radius:999px;padding:5px 11px;font-size:12px;cursor:pointer}"
    + "#rw-bar{display:flex;gap:8px;padding:10px 12px;border-top:1px solid #e2e8f0;background:#fff}"
    + "#rw-in{flex:1;border:1px solid #e2e8f0;border-radius:9px;padding:9px 11px;font-size:14px;font-family:inherit}"
    + "#rw-send{border:0;border-radius:9px;color:#fff;padding:9px 16px;cursor:pointer;font-weight:600}";
  var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  var fab = document.createElement("button"); fab.id = "rw-fab"; fab.textContent = "💬"; fab.setAttribute("aria-label", "Chat");
  var badge = document.createElement("div"); badge.id = "rw-badge"; badge.textContent = "1";
  var panel = document.createElement("div"); panel.id = "rw-panel";
  panel.innerHTML = '<div id="rw-head"><span id="rw-av">?</span><div style="flex:1"><b id="rw-name">...</b><div style="font-size:11px;opacity:.85">Typically replies instantly</div></div><button id="rw-x" style="background:none;border:0;color:#fff;font-size:18px;cursor:pointer">×</button></div>'
    + '<div id="rw-msgs"></div><div id="rw-sug"></div>'
    + '<div id="rw-bar"><input id="rw-in" placeholder="Type your message..." autocomplete="off"><button id="rw-send">Send</button></div>';
  document.body.appendChild(fab); document.body.appendChild(badge); document.body.appendChild(panel);
  function esc(s) { var d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
  function fmt(s) { return esc(s).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>"); }
  function addMsg(who, text) {
    var box = document.getElementById("rw-msgs");
    var old = document.getElementById("rw-t"); if (old) old.remove();
    var m = document.createElement("div");
    m.className = "rw-m " + (who === "u" ? "rw-u" : "rw-b");
    if (who === "u") { m.style.background = color; m.style.color = "#fff"; }
    m.innerHTML = fmt(text);
    box.appendChild(m); box.scrollTop = box.scrollHeight;
    if (!open) { unread++; badge.textContent = unread; badge.style.display = "flex"; }
  }
  function typing() {
    var box = document.getElementById("rw-msgs");
    var t = document.createElement("div"); t.className = "rw-t"; t.id = "rw-t"; t.textContent = "Typing…";
    box.appendChild(t); box.scrollTop = box.scrollHeight;
  }
  function paint() {
    document.getElementById("rw-head").style.background = color;
    fab.style.background = color;
    document.getElementById("rw-name").textContent = botName;
    document.getElementById("rw-av").textContent = (avatar || botName.charAt(0) || "?").toUpperCase();
    document.getElementById("rw-send").style.background = color;
  }
  async function init() {
    try {
      var r = await fetch(base + "/api/public/config?bot=" + encodeURIComponent(slug));
      var j = await r.json();
      if (j.error) { addMsg("b", "This assistant is unavailable."); return; }
      botName = j.name; color = j.color || color; greeting = j.greeting; avatar = j.avatar || "";
      suggs = j.suggestions || []; needEmail = j.need_email;
      paint(); addMsg("b", greeting);
      var sg = document.getElementById("rw-sug"); sg.innerHTML = "";
      suggs.forEach(function (s) { var b = document.createElement("button"); b.textContent = s;
        b.onclick = function () { document.getElementById("rw-in").value = s; send(); }; sg.appendChild(b); });
    } catch (e) { addMsg("b", "Could not load assistant."); }
  }
  var email = "", uname = "", gotEmail = false;
  async function send() {
    var inp = document.getElementById("rw-in"); var text = inp.value.trim(); if (!text) return; inp.value = "";
    if (needEmail && !gotEmail && !email) {
      if (!/^\S+@\S+\.\S+$/.test(text)) { addMsg("b", "Could you share your email first so we can follow up?"); return; }
      email = text; gotEmail = true; addMsg("u", text);
      addMsg("b", "Thanks! What can I help you with today?"); return;
    }
    addMsg("u", text); typing();
    try {
      var r = await fetch(base + "/api/public/chat", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bot: slug, message: text, conversation_id: conv,
          visitor_key: visitor, name: uname, email: email }) });
      var j = await r.json(); conv = j.conversation_id || conv;
      addMsg("b", j.reply || j.error || "Sorry, something went wrong.");
    } catch (e) { addMsg("b", "Network error - please try again."); }
  }
  document.getElementById("rw-send").onclick = send;
  document.getElementById("rw-in").addEventListener("keydown", function (e) { if (e.key === "Enter") send(); });
  document.getElementById("rw-x").onclick = function () { open = false; panel.classList.remove("open"); };
  fab.onclick = function () { open = !open; panel.classList.toggle("open", open);
    if (open) { unread = 0; badge.style.display = "none"; } };
  init();
})();
