/* Capture-to-expected-display estimate. Timestamps are carried in decoded pixels,
   so dropped/reordered metadata cannot associate a timestamp with another frame. */
(() => {
  "use strict";
  const active = new Set();
  let samples = [], syncing = false;

  async function syncClock() {
    if (syncing || !active.size) return;
    syncing = true;
    try {
      for (let attempt = 0; attempt < 5 && active.size; attempt++) {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 2000);
        try {
          const t0 = performance.now();
          const response = await fetch("/time", { cache:"no-store", signal:controller.signal });
          if (!response.ok) continue;
          const data = await response.json();
          const t3 = performance.now();
          const t1 = data.received_ms, t2 = data.sent_ms;
          const rtt = t3 - t0 - (t2 - t1);
          if (![t1,t2,rtt].every(Number.isFinite) || t2 < t1 || rtt < 0) continue;
          samples.push({ offset:(t1 + t2 - t0 - t3) / 2, rtt, at:t3 });
        } catch (_) {
          // A failed clock sample must not affect video playback.
        } finally {
          window.clearTimeout(timeout);
        }
      }
    } finally {
      syncing = false;
    }
  }

  function clockSample() {
    const now = performance.now();
    samples = samples.filter(sample => now - sample.at < 30000);
    return samples.reduce((best, sample) => !best || sample.rtt < best.rtt ? sample : best, null);
  }

  function decode(ctx) {
    const pixels = ctx.getImageData(0, 0, 256, 32).data;
    const bytes = new Uint8Array(16);
    for (let bit = 0; bit < 128; bit++) {
      const x = (bit % 32) * 8 + 3, y = Math.floor(bit / 32) * 8 + 3;
      let brightness = 0;
      for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
        const offset = ((y + dy) * 256 + x + dx) * 4;
        brightness += (pixels[offset] + pixels[offset + 1] + pixels[offset + 2]) / 3;
      }
      brightness /= 4;
      if (brightness > 80 && brightness < 170) return null;
      bytes[bit >> 3] |= (brightness >= 170 ? 1 : 0) << (7 - bit % 8);
    }
    if (bytes[0] !== 76 || bytes[1] !== 84 || bytes[2] !== 49 || ![80,82,83,85].includes(bytes[3])) return null;
    let crc = 0xffffffff;
    for (let index = 0; index < 12; index++) {
      crc ^= bytes[index];
      for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    const view = new DataView(bytes.buffer);
    if (((crc ^ 0xffffffff) >>> 0) !== view.getUint32(12)) return null;
    const capturedMs = Number(view.getBigUint64(4)) / 1000;
    return Number.isFinite(capturedMs) ? { capturedMs, kind:bytes[3] } : null;
  }

  window.VideoLatencyUI = {
    attach(video, output) {
      const buffer = document.createElement("canvas");
      buffer.width = 256; buffer.height = 32;
      const ctx = buffer.getContext("2d", { willReadFrequently:true });
      let running = false, callback = null, lastRead = -Infinity, measuredAt = null;
      const unavailable = (reason) => {
        output.textContent = "지연 —";
        output.title = reason;
        measuredAt = null;
      };
      const watcher = {
        expire() {
          if (!clockSample() || (measuredAt !== null && performance.now() - measuredAt > 1500)) {
            unavailable("시계 보정 또는 최근 표시 프레임을 기다리는 중");
          }
        },
      };
      function frame(now, metadata) {
        if (!running) return;
        callback = video.requestVideoFrameCallback(frame);
        if (document.hidden || now - lastRead < 250) return;
        lastRead = now;
        if (!ctx || video.videoWidth < 256 || video.videoHeight <= 32) {
          video.style.clipPath = "";
          unavailable("영상 크기가 시간 코드 측정을 지원하지 않습니다");
          return;
        }
        try {
          ctx.drawImage(video, 0, video.videoHeight - 32, 256, 32, 0, 0, 256, 32);
          const stamp = decode(ctx);
          if (!stamp) {
            unavailable("유효한 촬영 시각이 없거나 시간 코드 해독 불가");
            return;
          }
          // Clip only the appended strip; no source camera pixels are hidden.
          const rect = video.getBoundingClientRect();
          const scale = Math.min(rect.width / video.videoWidth, rect.height / video.videoHeight);
          const bottomLetterbox = (rect.height - video.videoHeight * scale) / 2;
          video.style.clipPath = `inset(0 0 ${bottomLetterbox + 32 * scale}px 0)`;
          if (stamp.kind === 85 || stamp.capturedMs <= 0) {
            unavailable("촬영 시각을 제공하지 않는 영상입니다");
            return;
          }
          const clock = clockSample();
          if (!clock || !Number.isFinite(metadata.expectedDisplayTime)) {
            unavailable("시계 보정 또는 브라우저 표시 시각을 기다리는 중");
            return;
          }
          const latency = metadata.expectedDisplayTime + clock.offset - stamp.capturedMs;
          if (latency < 0) {
            unavailable("촬영 시각과 시계 보정 결과가 맞지 않습니다");
            return;
          }
          const source = stamp.kind === 80 ? "센서 첫 행 노출 시작 기준"
            : stamp.kind === 82 ? "ROS 영상 헤더 촬영 시각 기준 (발행자 시계 동기화 필요)" : "합성 프레임 생성 기준";
          output.textContent = `${stamp.kind === 83 ? "생성" : "지연"} ≈${Math.round(latency)}ms`;
          output.title = `${source} → 브라우저 예상 표시 시각. 시계 보정 불확실성 약 ±${Math.ceil(clock.rtt / 2)}ms, 센서·브라우저 표시 오차 별도. 물리적 화면 발광 시각은 측정하지 않습니다.`;
          measuredAt = performance.now();
        } catch (_) {
          unavailable("이 브라우저에서 영상 시간 코드를 읽을 수 없습니다");
        }
      }
      return {
        start() {
          if (running) return;
          unavailable("촬영 시각과 시계 보정을 기다리는 중");
          if (!video.requestVideoFrameCallback) {
            unavailable("이 브라우저는 프레임 표시 시각을 지원하지 않습니다");
            return;
          }
          running = true;
          lastRead = -Infinity;
          active.add(watcher);
          void syncClock();
          callback = video.requestVideoFrameCallback(frame);
        },
        stop() {
          running = false;
          active.delete(watcher);
          if (callback !== null) video.cancelVideoFrameCallback(callback);
          callback = null;
          video.style.clipPath = "";
          unavailable("영상 미연결");
        },
      };
    },
  };
  const syncTimer = window.setInterval(() => { void syncClock(); }, 10000);
  const ageTimer = window.setInterval(() => active.forEach(watcher => watcher.expire()), 250);
  document.addEventListener("visibilitychange", () => {
    samples = [];
    active.forEach(watcher => watcher.expire());
    if (!document.hidden) void syncClock();
  });
  window.addEventListener("beforeunload", () => {
    window.clearInterval(syncTimer); window.clearInterval(ageTimer);
  });
})();
