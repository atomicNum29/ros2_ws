/* LDR1: little-endian 32-byte header followed by (uint32 index, uint16 mm). */
(() => {
  "use strict";
  const canvas = document.getElementById("lidar-canvas");
  const status = document.getElementById("lidar-status");
  if (!canvas || !status) return;
  const ctx = canvas.getContext("2d");
  let socket, retry, stopped = false, scan = null, lastFrame = null;

  function draw() {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = rect.width, h = rect.height;
    const cx = w / 2, cy = h / 2;
    const scale = Math.max(1, Math.min(w, h) / 2 - 24) / 7;
    ctx.clearRect(0, 0, w, h);
    ctx.font = "11px system-ui";
    ctx.textAlign = "left";
    for (let metres = 1; metres <= 7; metres++) {
      ctx.strokeStyle = "#dce3e8";
      ctx.beginPath();
      ctx.arc(cx, cy, metres * scale, 0, Math.PI * 2);
      ctx.stroke();
      if (metres % 2 === 1) {
        ctx.fillStyle = "#637384";
        ctx.fillText(`${metres}m`, cx + 4, cy - metres * scale + 12);
      }
    }
    ctx.strokeStyle = "#bdcbd5";
    ctx.beginPath();
    ctx.moveTo(cx - 7 * scale, cy); ctx.lineTo(cx + 7 * scale, cy);
    ctx.moveTo(cx, cy - 7 * scale); ctx.lineTo(cx, cy + 7 * scale);
    ctx.stroke();
    ctx.fillStyle = "#566779";
    ctx.textAlign = "center";
    ctx.fillText("전방 +x", cx, 13);
    ctx.textAlign = "left";
    ctx.textAlign = "right";
    ctx.fillText("+y 오른쪽", w - 3, cy - 6);
    if (scan) {
      ctx.fillStyle = "#137755";
      const { view, start, step } = scan;
      for (let offset = 32; offset < view.byteLength; offset += 6) {
        const index = view.getUint32(offset, true);
        const distance = view.getUint16(offset + 4, true) / 1000;
        const angle = start + index * step;
        // Display +x upward and +y to the right.
        const x = cx + distance * Math.sin(angle) * scale;
        const y = cy - distance * Math.cos(angle) * scale;
        ctx.beginPath(); ctx.arc(x, y, 2, 0, 2 * Math.PI); ctx.fill();
      }
    }
    // Origin/direction marker only; this does not represent measured vehicle size.
    ctx.fillStyle = "#1d2b39";
    ctx.beginPath();
    ctx.moveTo(cx, cy - 8); ctx.lineTo(cx - 5, cy + 5);
    ctx.lineTo(cx + 5, cy + 5); ctx.closePath(); ctx.fill();
  }

  function clear(label) {
    scan = null;
    lastFrame = null;
    status.textContent = label;
    status.className = "warn";
    draw();
  }

  function connect() {
    if (stopped) return;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${location.host}/ws/scan`);
    socket.binaryType = "arraybuffer";
    socket.onopen = () => { status.textContent = "스캔 대기"; };
    socket.onmessage = (event) => {
      if (!(event.data instanceof ArrayBuffer) || event.data.byteLength < 32
          || (event.data.byteLength - 32) % 6 !== 0) return;
      const view = new DataView(event.data);
      if (view.getUint32(0, true) !== 0x3152444c) return;
      const total = view.getUint32(4, true);
      const start = view.getFloat32(8, true), step = view.getFloat32(12, true);
      const radius = view.getFloat32(16, true), age = view.getFloat32(28, true);
      if (!Number.isFinite(start) || !Number.isFinite(step) || radius !== 7
          || !Number.isFinite(age) || age < 0 || age > 1) return;
      for (let offset = 32; offset < view.byteLength; offset += 6) {
        if (view.getUint32(offset, true) >= total || view.getUint16(offset + 4, true) > 7000) return;
      }
      scan = { view, start, step };
      lastFrame = performance.now() - age * 1000;
      status.textContent = `${(view.byteLength - 32) / 6}점 · 수신 중`;
      status.className = "ok";
      draw();
    };
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      clear("연결 끊김");
      if (!stopped) retry = window.setTimeout(connect, 1000);
    };
  }
  const resize = new ResizeObserver(draw);
  resize.observe(canvas);
  const freshness = window.setInterval(() => {
    if (lastFrame !== null && performance.now() - lastFrame > 1000) clear("스캔 갱신 지연");
  }, 250);
  window.addEventListener("beforeunload", () => {
    stopped = true;
    window.clearTimeout(retry);
    window.clearInterval(freshness);
    resize.disconnect();
    if (socket) socket.close();
  });
  draw();
  connect();
})();
