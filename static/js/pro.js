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
  window.drawInsights = function (canvasId, daily, progress) {
    const c = document.getElementById(canvasId);
    if (!c || !daily || !daily.length) return;
    const p = (progress === undefined) ? 1 : progress;
    const DPR = 2, W = (c.offsetWidth || 600) * DPR, H = 190 * DPR;
    c.width = W; c.height = H;
    const x = c.getContext("2d");
    x.clearRect(0, 0, W, H);
    const max = Math.max.apply(null, daily.map((d) => d.messages).concat(daily.map((d) => d.conversations || 0)).concat([1]));
    const n = daily.length, slot = W / n, pad = 30 * DPR;
    daily.forEach((d, i) => {
      const grow = Math.min(1, Math.max(0, (p * n) - i));
      const mh = ((d.messages || 0) / max) * (H - 60 * DPR) * grow;
      const ch = ((d.conversations || 0) / max) * (H - 60 * DPR) * grow;
      const bx = i * slot + slot * 0.22, bw = slot * 0.56;
      x.fillStyle = "#4f46e5";
      if (x.roundRect) { x.beginPath(); x.roundRect(bx, H - pad - mh, bw * 0.55, mh, 3 * DPR); x.fill(); }
      else x.fillRect(bx, H - pad - mh, bw * 0.55, mh);
      x.fillStyle = "#a5b4fc";
      if (x.roundRect) { x.beginPath(); x.roundRect(bx + bw * 0.6, H - pad - ch, bw * 0.4, ch, 3 * DPR); x.fill(); }
      else x.fillRect(bx + bw * 0.6, H - pad - ch, bw * 0.4, ch);
      if (p >= 1) {
        x.fillStyle = "#94a3b8"; x.font = (10 * DPR) + "px Inter,sans-serif"; x.textAlign = "center";
        x.fillText(d.label, i * slot + slot / 2, H - 8 * DPR);
      }
    });
  };

  // ---- animated wrapper: ease a canvas redraw from 0 -> 1 ----
  function animateDraw(fn, duration) {
    const start = performance.now();
    function frame(t) {
      const p = Math.min(1, (t - start) / (duration || 700));
      const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
      fn(eased);
      if (p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }
  window.animateInsights = function (canvasId, daily) {
    if (!document.getElementById(canvasId)) return;
    animateDraw((p) => window.drawInsights(canvasId, daily, p), 800);
  };

  // ---- KPI sparklines (mini area chart per stat tile) ----
  function drawSpark(c, vals, progress) {
    const p = (progress === undefined) ? 1 : progress;
    const DPR = 2, W = (c.offsetWidth || 160) * DPR, H = 34 * DPR;
    c.width = W; c.height = H;
    const x = c.getContext("2d");
    x.clearRect(0, 0, W, H);
    if (!vals.length) return;
    const max = Math.max.apply(null, vals.concat([1]));
    const n = vals.length, step = W / (n - 1 || 1);
    const pts = vals.map((v, i) => [i * step, H - 3 * DPR - (v / max) * (H - 8 * DPR)]);
    const upto = Math.max(1, Math.round(pts.length * p));
    const line = pts.slice(0, upto);
    const grad = x.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, "rgba(79,70,229,.22)");
    grad.addColorStop(1, "rgba(79,70,229,0)");
    x.beginPath();
    x.moveTo(line[0][0], line[0][1]);
    for (let i = 1; i < line.length; i++) x.lineTo(line[i][0], line[i][1]);
    x.lineTo(line[line.length - 1][0], H); x.lineTo(line[0][0], H); x.closePath();
    x.fillStyle = grad; x.fill();
    x.beginPath();
    x.moveTo(line[0][0], line[0][1]);
    for (let i = 1; i < line.length; i++) x.lineTo(line[i][0], line[i][1]);
    x.strokeStyle = "#4f46e5"; x.lineWidth = 2 * DPR; x.lineJoin = "round"; x.lineCap = "round"; x.stroke();
  }
  window.drawSparks = function (daily) {
    const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.querySelectorAll("canvas.spark").forEach((c) => {
      const vals = (daily || []).map((d) => d[c.dataset.k] || 0);
      if (reduced) { drawSpark(c, vals, 1); return; }
      animateDraw((p) => drawSpark(c, vals, p), 900);
    });
  };

  // ---- count-up animation for KPI numbers ----
  window.countUp = function () {
    const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.querySelectorAll(".cnt").forEach((el) => {
      const to = parseFloat(el.dataset.to) || 0;
      if (reduced || to === 0) { el.textContent = to.toLocaleString(); return; }
      const start = performance.now(), dur = 900;
      function step(t) {
        const p = Math.min(1, (t - start) / dur);
        const eased = 1 - Math.pow(1 - p, 3);
        el.textContent = Math.round(to * eased).toLocaleString();
        if (p < 1) requestAnimationFrame(step);
        else el.textContent = to.toLocaleString();
      }
      requestAnimationFrame(step);
    });
  };
})();
