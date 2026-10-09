/* Relay shared dashboard JS: modals, chat rendering, insights chart. */
(function () {
  "use strict";

  window.openModal = (id) => document.getElementById(id)?.classList.add("open");
  window.closeModal = (id) => document.getElementById(id)?.classList.remove("open");

  // ---- markdown-lite renderer for bot replies (**bold**, bullets, line breaks)
  window.renderReply = function (text) {
    const d = document.createElement("div");
    d.textContent = text || "";
    return d.innerHTML
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/^-\s+(.+)$/gm, "• $1")
      .replace(/\n/g, "<br>");
  };

  // ---- chat message DOM builder (shared by playground)
  window.buildMsg = function (host, role, text, cites, mid) {
    const el = document.createElement("div");
    el.className = "msg " + (role === "user" ? "user" : role === "agent" ? "agent" : "bot");
    el.innerHTML = window.renderReply(text);
    if (cites && cites.length) {
      const c = document.createElement("span");
      c.className = "cite";
      c.textContent = "Sources: " + cites.map((x) => x.source).join(" · ");
      el.appendChild(c);
    }
    if (mid) {
      const r = document.createElement("div");
      r.className = "rate";
      r.innerHTML = '<button data-v="1">👍 helpful</button><button data-v="-1">👎 not helpful</button>';
      r.querySelectorAll("button").forEach((b) => {
        b.onclick = async () => {
          await fetch("/api/messages/" + mid + "/rate", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rating: Number(b.dataset.v) }),
          });
          r.querySelectorAll("button").forEach((x) => x.classList.remove("used"));
          b.classList.add("used");
        };
      });
      el.appendChild(r);
    }
    host.appendChild(el);
    host.scrollTop = host.scrollHeight;
    return el;
  };

  window.showTyping = function (host) {
    const t = document.createElement("div");
    t.className = "typing"; t.id = "typing";
    t.innerHTML = "<span></span><span></span><span></span>";
    host.appendChild(t); host.scrollTop = host.scrollHeight;
  };
  window.hideTyping = function () { const t = document.getElementById("typing"); if (t) t.remove(); };

  // ---- insights canvas chart (messages + conversations)
  window.drawInsights = function (canvasId, daily) {
    const c = document.getElementById(canvasId);
    if (!c || !daily || !daily.length) return;
    const DPR = 2, W = (c.offsetWidth || 600) * DPR, H = 180 * DPR;
    c.width = W; c.height = H;
    const x = c.getContext("2d");
    x.clearRect(0, 0, W, H);
    const max = Math.max.apply(null, daily.map((d) => d.messages).concat(daily.map((d) => d.conversations || 0)).concat([1]));
    const n = daily.length, slot = W / n, pad = 30 * DPR;
    daily.forEach((d, i) => {
      const mh = ((d.messages || 0) / max) * (H - 60 * DPR);
      const ch = ((d.conversations || 0) / max) * (H - 60 * DPR);
      const bx = i * slot + slot * 0.22, bw = slot * 0.56;
      x.fillStyle = "#4f46e5";
      if (x.roundRect) { x.beginPath(); x.roundRect(bx, H - pad - mh, bw * 0.55, mh, 3 * DPR); x.fill(); }
      else x.fillRect(bx, H - pad - mh, bw * 0.55, mh);
      x.fillStyle = "#a5b4fc";
      if (x.roundRect) { x.beginPath(); x.roundRect(bx + bw * 0.6, H - pad - ch, bw * 0.4, ch, 3 * DPR); x.fill(); }
      else x.fillRect(bx + bw * 0.6, H - pad - ch, bw * 0.4, ch);
      x.fillStyle = "#94a3b8"; x.font = (10 * DPR) + "px Inter,sans-serif"; x.textAlign = "center";
      x.fillText(d.label, i * slot + slot / 2, H - 8 * DPR);
    });
  };
})();
