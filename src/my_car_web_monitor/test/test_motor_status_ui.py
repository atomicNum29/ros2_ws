"""Real Chromium UI tests with read-only mock status responses.

Start headless Chromium with --remote-debugging-port=9229, then set
MOTOR_UI_BROWSER_URL=http://127.0.0.1:9229 when running this file in the web venv.
"""

import base64
import json
import os
from pathlib import Path
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'my_car_web_monitor/web/static'
BROWSER = os.environ.get('MOTOR_UI_BROWSER_URL')
pytestmark = pytest.mark.skipif(not BROWSER, reason='Set MOTOR_UI_BROWSER_URL for real browser tests')


def snapshot():
    return {
        'version': 1, 'seq': 16, 'state': 1, 'system_error': 0, 'validity': 63,
        'drivers': {'a': {'voltage_mv': 24000, 'valid': True},
                    'b': {'voltage_mv': 23800, 'valid': True}},
        'wheels': {key: {'status': 0, 'rpm': rpm, 'current_deci_amp': 123,
                         'controller_output': -500, 'valid': True}
                   for key, rpm in [('lf', 100), ('rf', -100), ('lr', -80), ('rr', 80)]},
    }


class BrowserPage:
    def __init__(self, websocket, shared):
        self.websocket = websocket
        self.shared = shared
        self.sequence = 0

    def call(self, method, params=None):
        self.sequence += 1
        call_id = self.sequence
        self.websocket.send(json.dumps({'id': call_id, 'method': method, 'params': params or {}}))
        while True:
            result = json.loads(self.websocket.recv(timeout=5))
            if result.get('id') == call_id:
                assert 'error' not in result, result
                return result.get('result', {})

    def js(self, expression):
        expression = 'window.__uiFixture = ' + json.dumps(self.shared) + '; ' + expression
        result = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
        assert 'exceptionDetails' not in result, result
        return result['result'].get('value')

    def wait(self, expression):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.js(expression):
                return
            time.sleep(0.05)
        pytest.fail(f'Browser condition timed out: {expression}')

    def text(self, selector):
        return self.js(f'document.querySelector({json.dumps(selector)}).textContent')


@pytest.fixture
def page():
    from websockets.sync.client import connect
    shared = {'status': snapshot(), 'age': 0.0, 'requests': [], 'fail': False}

    with urlopen(Request(BROWSER + '/json/new?' + quote('about:blank', safe=''), method='PUT')) as response:
        target = json.load(response)
    ws = connect(target['webSocketDebuggerUrl'])
    browser = BrowserPage(ws, shared)
    try:
        browser.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1200, 'deviceScaleFactor': 1, 'mobile': False})
        browser.call('Page.enable')
        html = (STATIC / 'index.html').read_text()
        mock_fetch = """<script>
          window.__uiFixture = FIXTURE;
          window.__uiRequests = [];
          window.fetch = async (url) => {
            window.__uiRequests.push(url);
            let data = {};
            if (url === '/streams') data = {streams: []};
            if (url === '/control/config') data = {linear_speed: 1, angular_speed: 3};
            return new Response(JSON.stringify(data));
          };
          window.__statusSockets = [];
          window.__sentCommands = [];
          window.WebSocket = class extends EventTarget {
            static OPEN = 1;
            static CLOSED = 3;
            constructor(url) {
              super();
              this.url = url;
              this.readyState = 1;
              this.count = 0;
              window.__statusSockets.push(this);
              setTimeout(() => { this.dispatchEvent(new Event('open')); this.emitStatus(); }, 0);
              this.timer = setInterval(() => {
                if (!window.__uiFixture.manual) this.emitStatus();
              }, 100);
            }
            emitStatus() {
              if (this.readyState !== 1) return;
              const fixture = window.__uiFixture;
              if (fixture.fail) { this.close(); return; }
              const payload = {type: 'motor_status', motor_status: fixture.status,
                motor_status_age_sec: fixture.age, motor_status_raw: JSON.stringify(fixture.status),
                motor_status_received_count: ++this.count, stream_dropped_count: fixture.dropped || 0};
              this.dispatchEvent(new MessageEvent('message', {data: JSON.stringify(payload)}));
            }
            send(data) { window.__sentCommands.push(data); }
            close() {
              if (this.readyState === 3) return;
              this.readyState = 3;
              clearInterval(this.timer);
              this.dispatchEvent(new Event('close'));
            }
          };
        </script>""".replace('FIXTURE', json.dumps(shared))
        html = html.replace('<head>', '<head>' + mock_fetch)
        html = html.replace('<link rel="stylesheet" href="/static/motor-status.css" />',
                            '<style>' + (STATIC / 'motor-status.css').read_text() + '</style>')
        html = html.replace('<script src="/static/motor-status.js"></script>',
                            '<script>' + (STATIC / 'motor-status.js').read_text() + '</script>')
        # Load the actual page/assets without external network or a running ROS server.
        frame_id = browser.call('Page.getFrameTree')['frameTree']['frame']['id']
        browser.call('Page.setDocumentContent', {'frameId': frame_id, 'html': html})
        browser.wait('document.querySelector("[data-role=connection]")?.textContent === "상태 수신 중"')
        yield browser, shared
    finally:
        ws.close()
        with urlopen(BROWSER + '/json/close/' + target['id']):
            pass


def test_live_cards_signed_units_and_passive_monitoring(page):
    browser, shared = page
    assert browser.text('[data-role=mcu]') == '출력 허용'
    assert browser.text('[data-role=voltage-a]') == '24.00 V'
    assert browser.text('[data-role=voltage-b]') == '23.80 V'
    for key, rpm in [('lf', '100'), ('rf', '-100'), ('lr', '-80'), ('rr', '80')]:
        assert browser.text(f'[data-wheel={key}] [data-field=rpm]') == rpm
        assert browser.text(f'[data-wheel={key}] [data-field=current]') == '12.3'
        assert browser.text(f'[data-wheel={key}] [data-field=output]') == '-500'
        assert browser.text(f'[data-wheel={key}] [data-field=state]') == '모터 정상'
    assert browser.js('controlSocket === null')
    assert browser.js('window.__statusSockets.every(socket => socket.url.endsWith("/ws/status"))')
    assert browser.js('window.__sentCommands.length === 0 && !window.__uiRequests.includes("/control/status")')
    assert browser.js('document.querySelector("[data-wheel=lf]").getBoundingClientRect().top === document.querySelector("[data-wheel=rf]").getBoundingClientRect().top')
    assert browser.js('document.querySelector("[data-wheel=lr]").getBoundingClientRect().top > document.querySelector("[data-wheel=lf]").getBoundingClientRect().top')


def test_all_bit_mappings_and_fault_semantics(page):
    browser, shared = page
    codes = browser.js('MotorStatusUI.decodeBits(65535, MotorStatusUI.SYSTEM_BITS).map(x => x.code)')
    assert codes == ['SERIAL_CHECKSUM_ERROR', 'COMMAND_TIMEOUT', 'CAN_TX_FAILURE',
                     'EMERGENCY_STOP_ACTIVE', 'BATTERY_LOW', 'SERIAL_FRAMING_ERROR',
                     'COMMAND_OUT_OF_RANGE', 'WATCHDOG_RESET_DETECTED',
                     'DRIVER_STATUS_FAULT_PRESENT', 'MAIN_DATA_STALE', 'VOLTAGE_STALE',
                     'CAN_RX_OVERRUN', 'RESERVED', 'RESERVED', 'RESERVED', 'RESERVED']
    assert browser.js('MotorStatusUI.decodeBits(255, MotorStatusUI.MOTOR_BITS, 8).map(x => x.code)') == [
        'ALARM', 'CTRL_FAIL', 'OVER_VOLT', 'OVER_TEMP', 'OVER_LOAD', 'HALL_FAIL', 'INV_VEL', 'STALL']
    shared['status']['system_error'] = 4 | 4096
    shared['status']['wheels']['rf']['status'] = 0xA0
    browser.wait('document.querySelector("[data-role=errors]").textContent.includes("CAN_TX_FAILURE")')
    assert browser.text('[data-role=mcu]') == '출력 허용'
    assert '예약 비트' in browser.text('[data-role=errors]')
    assert 'BC_INITIALIZATION_FAILURE' not in browser.text('#motor-dashboard')
    browser.wait('document.querySelector("[data-wheel=rf] [data-field=state]").textContent === "모터 오류"')
    assert '홀 센서 오류' in browser.text('[data-wheel=rf] [data-field=errors]')
    assert '모터 구속' in browser.text('[data-wheel=rf] [data-field=errors]')
    assert browser.text('[data-wheel=lf] [data-field=state]') == '모터 정상'


def test_invalid_and_stale_values_never_look_live(page):
    browser, shared = page
    shared['status']['validity'] &= ~1
    # Even a contradictory valid:true must not override the MCU validity bit.
    shared['status']['drivers']['b']['valid'] = False
    browser.wait('document.querySelector("[data-wheel=lf] [data-field=rpm]").textContent === "—"')
    assert browser.text('[data-wheel=lf] [data-field=state]') == '유효하지 않음'
    assert browser.text('[data-role=voltage-b]') == '—'
    shared['age'] = 5
    browser.wait('document.querySelector("[data-role=connection]").textContent === "상태 갱신 지연"')
    assert browser.text('[data-role=mcu]').startswith('마지막:')
    assert browser.js('[...document.querySelectorAll(".wheel-metrics dd")].every(x => x.textContent === "—")')
    assert browser.js('[...document.querySelectorAll(".wheel-card")].every(x => x.dataset.tone === "neutral")')
    shared['age'] = 0
    shared['status'] = snapshot()
    browser.wait('document.querySelector("[data-wheel=lf] [data-field=state]").textContent === "모터 정상"')
    shared['fail'] = True
    browser.wait('document.querySelector("[data-role=connection]").textContent === "서버 연결 끊김"')
    assert browser.text('[data-wheel=rf] [data-field=output]') == '—'


def test_legacy_unknown_and_unreceived_states(page):
    browser, shared = page
    shared['status'] = {'seq': 2, 'state': 4, 'error': 4 | 256, 'battery_mv': 23800}
    browser.wait('document.querySelector("[data-role=errors]").textContent.includes("DRIVER_FAULT")')
    assert 'CAN_TX_FAILURE' not in browser.text('[data-role=errors]')
    assert 'OVER_CURRENT' in browser.text('[data-role=errors]')
    assert browser.text('[data-wheel=lf] [data-field=state]') == '구형 메시지 · 정보 없음'
    assert '23.80 V' in browser.text('[data-role=notice]')
    shared['status'] = {'version': 2, 'state': 1, 'system_error': 0}
    browser.wait('document.querySelector("[data-role=connection]").textContent === "지원하지 않는 상태 형식"')
    shared['status'] = None
    browser.wait('document.querySelector("[data-role=connection]").textContent === "상태 수신 대기"')
    assert browser.text('[data-role=mcu]') == '수신 대기'


def test_untrusted_content_is_text_and_mobile_layout(page):
    browser, shared = page
    browser.js('document.querySelector(".raw-status").open = true')
    shared['status']['extra'] = '<img src=x onerror="window.injected=true">'
    shared['status']['state'] = '<script>window.injected=true</script>'
    browser.wait('document.querySelector("[data-role=raw]").textContent.includes("onerror")')
    assert browser.js('window.injected === undefined')
    assert browser.js('document.querySelectorAll("#motor-dashboard img, #motor-dashboard script").length') == 0
    shared['status'] = snapshot()
    shared['status']['system_error'] = 0x0300
    shared['status']['state'] = 4
    shared['status']['wheels']['rf']['status'] = 0x28
    browser.wait('document.querySelector("[data-role=mcu]").textContent === "시스템 고장"')
    for width, height, name in [(1440, 1200, 'desktop'), (390, 844, 'mobile')]:
        browser.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': width < 500})
        assert browser.js('document.documentElement.scrollWidth <= window.innerWidth')
        image = browser.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True})
        Path(f'/tmp/car-motor-status-{name}.png').write_bytes(base64.b64decode(image['data']))


def test_client_age_watchdog_and_invalid_schema(page):
    browser, _ = page
    status = json.dumps(snapshot())
    result = browser.js(f'''(() => {{
      const root = document.createElement('section');
      const ui = MotorStatusUI.mount(root);
      ui.accept({{motor_status: {status}, motor_status_age_sec: 0}});
      ui.render(performance.now() + 2000);
      return {{label: root.querySelector('[data-role=connection]').textContent,
        values: [...root.querySelectorAll('.wheel-metrics dd')].map(node => node.textContent)}};
    }})()''')
    assert result['label'] == '상태 갱신 지연'
    assert result['values'] == ['—'] * 12
    assert browser.js('MotorStatusUI.decodeBits(-1, MotorStatusUI.SYSTEM_BITS)') is None
    assert browser.js('MotorStatusUI.decodeBits("4", MotorStatusUI.SYSTEM_BITS)') is None


def test_10hz_delivery_has_no_skipped_sequences_and_reuses_diagnostics(page):
    browser, shared = page
    shared['manual'] = True
    browser.js(r"""(() => {
      const root = document.querySelector('#motor-dashboard');
      window.__beforeCount = Number(root.dataset.receivedCount);
      window.__bitNode = document.querySelector('[data-role=bits]').firstChild;
      window.__motorErrorNode = document.querySelector('[data-wheel=lf] [data-field=errors]').firstChild;
      window.__observedSeq = [];
      let lastSeq = -1;
      window.__observer = new MutationObserver(() => {
        const match = document.querySelector('[data-role=mcu-detail]').textContent.match(/seq (\d+)/);
        if (match && Number(match[1]) !== lastSeq) {
          lastSeq = Number(match[1]);
          window.__observedSeq.push(lastSeq);
        }
      });
      window.__observer.observe(document.querySelector('[data-role=mcu-detail]'), {childList: true});
      let sent = 0;
      window.__tenHzTimer = setInterval(() => {
        window.__uiFixture.status.seq = (250 + sent) % 256;
        window.__statusSockets.at(-1).emitStatus();
        if (++sent === 20) clearInterval(window.__tenHzTimer);
      }, 100);
    })()""")
    browser.wait('Number(document.querySelector("#motor-dashboard").dataset.receivedCount) - window.__beforeCount === 20')
    assert browser.js('window.__observedSeq') == [(250 + i) % 256 for i in range(20)]
    assert browser.js('window.__bitNode === document.querySelector("[data-role=bits]").firstChild')
    assert browser.js('window.__motorErrorNode === document.querySelector("[data-wheel=lf] [data-field=errors]").firstChild')
    browser.js('window.__observer.disconnect()')


def test_stream_overflow_notice_and_reconnect(page):
    browser, shared = page
    shared['dropped'] = 4
    browser.wait('document.querySelector("[data-role=notice]").textContent.includes("4개 상태가 누락")')
    shared['fail'] = True
    browser.wait('document.querySelector("[data-role=connection]").textContent === "서버 연결 끊김"')
    shared['fail'] = False
    shared['dropped'] = 0
    browser.wait('document.querySelector("[data-role=connection]").textContent === "상태 수신 중"')
    assert browser.js('window.__sentCommands.length === 0')
