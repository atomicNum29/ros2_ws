/* Wheel-Dragoon status presentation. Monitoring never opens the control socket. */
(() => {
  "use strict";
  const STALE_SECONDS = 1.0;
  const STATES = [
    ["출력 비활성", "DISABLED", "neutral"], ["출력 허용", "ENABLED", "ok"],
    ["명령 시간 초과 정지", "TIMEOUT_STOP", "warn"], ["비상 정지", "ESTOP", "bad"],
    ["시스템 고장", "FAULT", "bad"], ["부팅 중", "BOOTING", "neutral"],
    ["보정 중", "CALIBRATION", "neutral"],
  ];
  // [wire code, operator label, explanation, presentation severity]
  const SYSTEM_BITS = [
    ["SERIAL_CHECKSUM_ERROR", "시리얼 체크섬 오류", "수신 패킷의 체크섬이 일치하지 않습니다. 진단 이벤트입니다.", "warn"],
    ["COMMAND_TIMEOUT", "주행 명령 시간 초과", "AUTO 모드에서 정상 명령이 500 ms 넘게 수신되지 않았습니다.", "warn"],
    ["CAN_TX_FAILURE", "CAN 송신 실패", "구동·안전 명령 실패 또는 일시적인 telemetry 요청 실패입니다. 이 비트만으로 고장을 단정할 수 없으며 MCU 상태를 함께 확인하세요.", "warn"],
    ["EMERGENCY_STOP_ACTIVE", "비상 정지 활성", "명령의 emergency stop 플래그가 활성화되어 있습니다.", "bad"],
    ["BATTERY_LOW", "배터리 저전압", "두 전압이 유효할 때 하나 이상이 21.0 V 미만으로 감지되었습니다. MCU 재부팅 전까지 유지됩니다.", "bad"],
    ["SERIAL_FRAMING_ERROR", "시리얼 프레임 오류", "패킷 헤더·길이·종류가 올바르지 않습니다. 진단 이벤트입니다.", "warn"],
    ["COMMAND_OUT_OF_RANGE", "속도 명령 범위 초과", "허용 범위를 벗어난 속도 명령을 MCU가 거부했습니다.", "warn"],
    ["WATCHDOG_RESET_DETECTED", "워치독 리셋 감지", "워치독 리셋 진단 비트입니다. 현재 MCU의 감지 source는 미구현입니다.", "warn"],
    ["DRIVER_STATUS_FAULT_PRESENT", "모터 드라이버 오류 감지", "하나 이상의 모터에서 오류 비트가 감지되었습니다. 모터별 상태를 확인하세요. MCU 재부팅 전까지 유지됩니다.", "bad"],
    ["MAIN_DATA_STALE", "모터 telemetry 시간 초과", "초기 대기 이후 하나 이상의 모터 정보가 500 ms 넘게 갱신되지 않았습니다. MCU 재부팅 전까지 유지됩니다.", "bad"],
    ["VOLTAGE_STALE", "드라이버 전압 시간 초과", "초기 대기 이후 전압이 3 s 넘게 갱신되지 않았습니다. 두 전압이 다시 유효해지면 해제됩니다.", "warn"],
    ["CAN_RX_OVERRUN", "CAN 수신 버퍼 초과", "CAN 수신 mailbox에서 overrun이 감지되었습니다. 즉시 정지를 뜻하지 않는 진단 비트입니다.", "warn"],
  ];
  const LEGACY_BITS = [
    ["CHECKSUM_ERROR", "체크섬 오류", "수신 패킷 체크섬 오류입니다.", "warn"],
    SYSTEM_BITS[1],
    ["DRIVER_FAULT", "드라이버 고장 요약", "구동 CAN 송신 실패, 모터 오류 또는 telemetry 시간 초과의 요약입니다. 구형 메시지로는 모터별 원인을 알 수 없습니다.", "bad"],
    ...SYSTEM_BITS.slice(3, 8),
    ["OVER_CURRENT", "과전류 · 구형 예약 비트", "구형 과전류 비트입니다. 현재 MCU에서는 예약되어 항상 0입니다.", "warn"],
    ["OVER_TEMPERATURE", "과열 · 구형 예약 비트", "구형 과열 비트입니다. 현재 MCU에서는 예약되어 항상 0입니다.", "warn"],
    ["PARAMETER_ERROR", "설정 오류 · 구형 예약 비트", "구형 설정 오류 비트입니다. 현재 MCU에서는 예약되어 항상 0입니다.", "warn"],
  ];
  const MOTOR_BITS = [
    ["ALARM", "제어기 알람", "모터 제어기에 알람이 있습니다.", "bad"],
    ["CTRL_FAIL", "속도 제어 실패", "회전속도가 기준 속도에 충분히 도달하지 못했습니다.", "bad"],
    ["OVER_VOLT", "과전압", "드라이버 입력 전압이 규정 범위를 초과했습니다.", "bad"],
    ["OVER_TEMP", "과열", "지원 제어기에서 65 °C 이상이 감지되었습니다.", "bad"],
    ["OVER_LOAD", "과부하 / 과전류", "설정 전류를 4초 이상 초과했거나 순간 최대 과전류가 감지되었습니다.", "bad"],
    ["HALL_FAIL", "홀 센서 오류", "Hall sensor 감지 실패입니다.", "bad"],
    ["INV_VEL", "회전 방향 불일치", "측정 회전 방향이 제어출력 방향과 반대입니다. 음수 RPM 자체는 오류가 아닙니다.", "bad"],
    ["STALL", "모터 구속", "출력 중 모터가 2초 이상 회전하지 못했습니다.", "bad"],
  ];
  const WHEELS = [
    ["lf", "왼쪽 앞", "A · MOT1"], ["rf", "오른쪽 앞", "B · MOT1"],
    ["lr", "왼쪽 뒤", "B · MOT2"], ["rr", "오른쪽 뒤", "A · MOT2"],
  ];
  const integer = (value, min, max) => Number.isInteger(value) && value >= min && value <= max;
  const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const hex = (value, width = 4) => Number.isInteger(value) ? `0x${value.toString(16).toUpperCase().padStart(width, "0")}` : "—";
  const numberFormats = [0, 1, 2].map(decimals => new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  }));
  const number = (value, decimals = 0) => numberFormats[decimals].format(value);


  function decodeBits(value, definitions, width = 16) {
    if (!integer(value, 0, 2 ** width - 1)) return null;
    return Array.from({ length: width }, (_, bit) => {
      const definition = definitions[bit] || ["RESERVED", "예약 비트", "현재 정의되지 않은 비트입니다. 기존 오류 이름으로 해석하지 않습니다.", "warn"];
      return { bit, mask: hex(1 << bit, width / 4), active: Boolean(value & (1 << bit)),
        code: definition[0], label: definition[1], description: definition[2], tone: definition[3] };
    });
  }

  function viewModel(status, age, serverAvailable = true) {
    let schema = "waiting";
    if (status !== null && status !== undefined) {
      schema = object(status) && status.version === 1 && "system_error" in status ? "v2"
        : object(status) && !("version" in status) && "error" in status ? "legacy" : "unknown";
    }
    const supported = schema === "v2" || schema === "legacy";
    const fresh = supported && serverAvailable && Number.isFinite(age) && age >= 0 && age <= STALE_SECONDS;
    const state = supported && integer(status.state, 0, 255) ? STATES[status.state] : null;
    const validity = schema === "v2" && integer(status.validity, 0, 255) ? status.validity : 0;
    const errors = supported ? decodeBits(schema === "v2" ? status.system_error : status.error,
      schema === "v2" ? SYSTEM_BITS : LEGACY_BITS) : null;
    return {
      schema, fresh, age, serverAvailable, state,
      stateLabel: state ? state[0] : supported ? "알 수 없는 상태" : schema === "unknown" ? "해석 불가" : "수신 대기",
      errors, rawError: supported ? (schema === "v2" ? status.system_error : status.error) : null,
      seq: supported && integer(status.seq, 0, 255) ? status.seq : "—",
      wheels: WHEELS.map(([key, label, driver], bit) => {
        const wheel = schema === "v2" && object(status.wheels?.[key]) ? status.wheels[key] : null;
        const usable = fresh && wheel?.valid === true && Boolean(validity & (1 << bit));
        const bits = wheel ? decodeBits(wheel.status, MOTOR_BITS, 8) : null;
        const valid = usable && bits !== null && integer(wheel.rpm, -32768, 32767)
          && integer(wheel.current_deci_amp, 0, 65535) && integer(wheel.controller_output, -32768, 32767);
        return { key, label, driver, wheel, bits, valid,
          tone: valid ? (wheel.status ? "bad" : "ok") : "neutral",
          labelText: schema === "legacy" ? "구형 메시지 · 정보 없음" : !wheel ? "데이터 없음"
            : !fresh ? "마지막 수신값" : !usable ? "유효하지 않음" : !valid ? "데이터 형식 오류"
              : wheel.status ? "모터 오류" : "모터 정상",
          rpm: valid ? number(wheel.rpm) : "—",
          current: valid ? number(wheel.current_deci_amp / 10, 1) : "—",
          output: valid ? number(wheel.controller_output) : "—" };
      }),
      drivers: ["a", "b"].map((key, index) => {
        const driver = schema === "v2" ? status.drivers?.[key] : null;
        const valid = fresh && driver?.valid === true && Boolean(validity & (1 << (index + 4)))
          && integer(driver.voltage_mv, 0, 65535);
        return { key, valid, voltage: valid ? `${number(driver.voltage_mv / 1000, 2)} V` : "—",
          label: schema === "legacy" ? "개별 전압 미지원" : valid ? "유효" : "현재 전압 확인 불가" };
      }),
    };
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function tone(node, value) { if (node.dataset.tone !== value) node.dataset.tone = value; }
  function text(node, value) { if (node.textContent !== value) node.textContent = value; }

  function mount(root) {
    root.innerHTML = `
      <div class="panel-head"><div><h2>차량 상태</h2><p class="muted motor-subtitle">MCU · 드라이버 · 네 바퀴</p></div>
        <span class="status-badge" data-role="connection" role="status">상태 수신 대기</span></div>
      <p class="motor-notice" data-role="notice"></p>
      <div class="motor-summary">
        <div class="motor-summary-item"><span class="muted">MCU 운용 상태</span><strong data-role="mcu">수신 대기</strong><small data-role="mcu-detail"></small></div>
        <div class="motor-summary-item"><span class="muted">드라이버 A <small>LF · RR</small></span><strong data-role="voltage-a">—</strong><small data-role="voltage-a-valid"></small></div>
        <div class="motor-summary-item"><span class="muted">드라이버 B <small>RF · LR</small></span><strong data-role="voltage-b">—</strong><small data-role="voltage-b-valid"></small></div>
      </div>
      <div class="motor-columns"><div><div class="vehicle-front">↑ 차량 앞쪽</div><div class="wheel-grid" data-role="wheels"></div></div>
        <section class="system-diagnostics" aria-label="시스템 오류"><div class="panel-head"><h3>시스템 오류 / 진단</h3><span data-role="error-count" class="status-badge"></span></div>
          <p class="muted motor-subtitle" data-role="error-context"></p><ul class="error-list" data-role="errors"></ul>
          <details><summary>시스템 비트 전체 보기 <span data-role="error-raw"></span></summary><div class="bit-table" data-role="bits"></div></details>
        </section>
      </div>
      <details class="raw-status"><summary>수신 원문 / 프로토콜 정보</summary><pre data-role="raw"></pre></details>`;
    const refs = Object.fromEntries([...root.querySelectorAll("[data-role]")].map(node => [node.dataset.role, node]));
    const cards = new Map();
    for (const [key, label, driver] of WHEELS) {
      const card = element("article", "wheel-card");
      card.dataset.wheel = key;
      card.setAttribute("aria-label", `${key.toUpperCase()} ${label} 모터`);
      card.innerHTML = `<div class="wheel-head"><h3>${key.toUpperCase()} <span>${label}</span></h3><span class="status-badge" data-field="state"></span></div>
        <p class="muted motor-subtitle">Driver ${driver}</p>
        <dl class="wheel-metrics"><div><dt>회전속도 <span>rpm</span></dt><dd data-field="rpm">—</dd></div>
          <div><dt>전류 <span>A</span></dt><dd data-field="current">—</dd></div><div><dt>제어출력</dt><dd data-field="output">—</dd></div></dl>
        <div class="wheel-errors" data-field="errors"></div><small class="muted" data-field="raw"></small>`;
      refs.wheels.append(card);
      cards.set(key, { card, ...Object.fromEntries([...card.querySelectorAll("[data-field]")].map(node => [node.dataset.field, node])) });
    }

    let latest = null, receivedAt = 0, baseAge = null, serverAvailable = true, raw = "";
    let revision = 0, previousRevision = -1, previousFresh, previousAvailable;
    let errorSignature = "", receivedPackets = 0, serverCount = null, dropped = 0;
    function updateRaw() {
      text(refs.raw, raw || (latest === null ? "아직 수신된 메시지가 없습니다." : JSON.stringify(latest, null, 2)));
    }
    const rawDetails = refs.raw.closest("details");
    rawDetails.addEventListener("toggle", () => { if (rawDetails.open) updateRaw(); });
    function render(now = performance.now()) {
      const age = Number.isFinite(baseAge) ? baseAge + Math.max(0, now - receivedAt) / 1000 : null;
      const view = viewModel(latest, age, serverAvailable);
      refs.connection.textContent = !serverAvailable ? "서버 연결 끊김" : view.schema === "waiting" ? "상태 수신 대기"
        : view.schema === "unknown" ? "지원하지 않는 상태 형식" : view.fresh ? "상태 수신 중" : "상태 갱신 지연";
      tone(refs.connection, view.fresh ? "ok" : "warn");
      refs.notice.textContent = !serverAvailable ? "서버에서 상태를 조회할 수 없습니다. 표시된 상태와 오류는 마지막 수신 기록입니다."
        : view.schema === "waiting" ? "MCU 상태 메시지를 기다리고 있습니다. 조종 연결 없이도 자동으로 갱신됩니다."
        : view.schema === "unknown" ? "상태 메시지를 해석할 수 없습니다. 수신 원문에서 형식을 확인하세요."
        : !view.fresh ? "최근 상태 메시지가 없습니다. 마지막 상태와 오류를 표시하며 측정값은 숨깁니다."
        : view.schema === "legacy" ? "구형 상태 메시지입니다. 개별 모터 정보와 드라이버별 전압은 제공되지 않습니다."
        : "측정값은 MCU validity와 메시지 수신 시각이 모두 유효할 때 표시됩니다.";
      refs["mcu-detail"].textContent = `${view.state?.[1] || "—"} · ${view.schema === "v2" ? "System Status v2" : view.schema === "legacy" ? "Legacy" : "—"} · seq ${view.seq}${serverCount === null ? "" : ` · ROS 수신 #${serverCount}`} · ${Number.isFinite(age) ? `${age.toFixed(1)} s 전 수신` : "수신 시각 없음"}`;
      if (view.schema === "legacy") {
        const voltage = view.fresh && integer(latest.battery_mv, 1, 65535) ? `${number(latest.battery_mv / 1000, 2)} V` : "확인 불가";
        refs.notice.textContent += ` 대표 배터리 전압: ${voltage}.`;
      }
      if (dropped) refs.notice.textContent += ` 웹 전송 대기열에서 ${dropped}개 상태가 누락되었습니다.`;
      if (revision === previousRevision && view.fresh === previousFresh && serverAvailable === previousAvailable) return;
      previousRevision = revision;
      previousFresh = view.fresh;
      previousAvailable = serverAvailable;
      refs.mcu.textContent = (view.fresh ? "" : view.state ? "마지막: " : "") + view.stateLabel;
      tone(refs.mcu, view.fresh ? (view.state?.[2] || "warn") : "neutral");
      for (const driver of view.drivers) {
        refs[`voltage-${driver.key}`].textContent = driver.voltage;
        refs[`voltage-${driver.key}-valid`].textContent = driver.label;
        tone(refs[`voltage-${driver.key}`], driver.valid ? "ok" : "neutral");
      }
      const nextErrorSignature = JSON.stringify([view.schema, view.fresh, view.rawError]);
      if (nextErrorSignature !== errorSignature) {
        errorSignature = nextErrorSignature;
        const active = view.errors?.filter(bit => bit.active) || [];
        refs["error-count"].textContent = view.errors ? `${view.fresh ? "" : "마지막 "}${active.length}개 비트` : "확인 불가";
        tone(refs["error-count"], !view.fresh || !view.errors ? "neutral" : active.length ? "warn" : "ok");
        refs["error-context"].textContent = "오류 비트는 원인 정보입니다. 최종 운용 상태는 MCU 상태를 기준으로 확인하세요.";
        refs.errors.replaceChildren();
        for (const bit of active) {
          const item = element("li", "error-item");
          tone(item, view.fresh ? bit.tone : "neutral");
          item.append(element("strong", "", `${view.fresh ? "" : "마지막: "}${bit.label}`),
            element("small", "error-code", `bit ${bit.bit} · ${bit.mask} · ${bit.code}`),
            element("p", "", bit.description));
          refs.errors.append(item);
        }
        if (!active.length) refs.errors.append(element("li", "muted", !view.errors ? "오류 정보를 확인할 수 없습니다."
          : view.fresh ? "활성 오류 비트가 없습니다." : "마지막 수신 기록에 활성 오류 비트가 없습니다."));
        refs["error-raw"].textContent = view.errors ? hex(view.rawError) : "—";
        refs.bits.replaceChildren();
        for (const bit of view.errors || []) {
          const row = element("div", "bit-row");
          row.append(element("span", "", `${bit.bit} · ${bit.mask}`), element("span", "", bit.label), element("b", "", bit.active ? "1" : "0"));
          row.title = `${bit.code}: ${bit.description}`;
          tone(row, bit.active && view.fresh ? bit.tone : "neutral");
          refs.bits.append(row);
        }
      }
      for (const wheel of view.wheels) {
        const card = cards.get(wheel.key);
        tone(card.card, wheel.tone);
        card.state.textContent = wheel.labelText;
        tone(card.state, wheel.tone);
        for (const field of ["rpm", "current", "output"]) text(card[field], wheel[field]);
        const motorSignature = `${wheel.valid}/${wheel.bits ? wheel.wheel.status : "unknown"}`;
        if (card.errorSignature !== motorSignature) {
          card.errorSignature = motorSignature;
          card.errors.replaceChildren();
          const motorErrors = wheel.bits?.filter(bit => bit.active) || [];
          for (const bit of motorErrors) {
            const badge = element("span", "motor-error", `${wheel.valid ? "" : "마지막: "}${bit.label}`);
            badge.title = `bit ${bit.bit} · ${bit.mask} · ${bit.code}: ${bit.description}`;
            card.errors.append(badge);
          }
          if (!motorErrors.length) card.errors.append(element("span", "muted", wheel.valid ? "오류 비트 없음" : "현재 모터 상태 확인 불가"));
          card.raw.textContent = wheel.bits ? `${wheel.valid ? "" : "마지막 "}상태 ${hex(wheel.wheel.status, 2)} · ${motorErrors.map(bit => bit.code).join(", ") || "오류 비트 없음"}` : "상태 비트: —";
        }
      }
      if (rawDetails.open) updateRaw();
    }
    return {
      accept(payload, requestAt = performance.now()) {
        latest = payload.motor_status ?? null;
        raw = typeof payload.motor_status_raw === "string" ? payload.motor_status_raw : "";
        if (latest === null && raw) {
          try { latest = JSON.parse(raw); } catch { latest = raw; }
        }
        baseAge = typeof payload.motor_status_age_sec === "number" && payload.motor_status_age_sec >= 0 ? payload.motor_status_age_sec : null;
        receivedAt = requestAt;
        serverAvailable = true;
        revision += 1;
        if (latest !== null) receivedPackets += 1;
        root.dataset.receivedCount = String(receivedPackets);
        serverCount = integer(payload.motor_status_received_count, 0, Number.MAX_SAFE_INTEGER) ? payload.motor_status_received_count : null;
        dropped = integer(payload.stream_dropped_count, 0, Number.MAX_SAFE_INTEGER) ? payload.stream_dropped_count : 0;
        render();
      },
      fail() { serverAvailable = false; render(); },
      render,
    };
  }

  function start(root) {
    const ui = mount(root);
    ui.render();
    let stopped = false, socket = null, reconnectTimer, lastMessageAt = performance.now();
    const ticker = window.setInterval(() => {
      ui.render();
      if (socket?.readyState === WebSocket.OPEN && performance.now() - lastMessageAt > 3000) {
        ui.fail();
        socket.close();
      }
    }, 250);
    function connect() {
      if (stopped) return;
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const current = new WebSocket(`${protocol}://${window.location.host}/ws/status`);
      socket = current;
      lastMessageAt = performance.now();
      current.addEventListener("open", () => { lastMessageAt = performance.now(); });
      current.addEventListener("message", event => {
        if (stopped || socket !== current) return;
        try {
          const payload = JSON.parse(event.data);
          if (!object(payload)) throw new Error("invalid status response");
          lastMessageAt = performance.now();
          if (payload.type === "status_heartbeat") return;
          if (payload.type !== "motor_status" || !("motor_status" in payload)) throw new Error("invalid status response");
          // Consume every server frame. No latest-only polling or seq filtering.
          ui.accept(payload);
        } catch { ui.fail(); current.close(); }
      });
      current.addEventListener("error", () => { ui.fail(); current.close(); });
      current.addEventListener("close", () => {
        if (stopped || socket !== current) return;
        ui.fail();
        socket = null;
        reconnectTimer = window.setTimeout(connect, 1000);
      });
    }
    connect();
    return () => {
      stopped = true;
      window.clearInterval(ticker);
      window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }
  window.MotorStatusUI = { start, mount, viewModel, decodeBits, SYSTEM_BITS, LEGACY_BITS, MOTOR_BITS, STALE_SECONDS };
})();
