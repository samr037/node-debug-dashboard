"""Non-regression tests for app/static/app.js.

The frontend is plain browser JS with no build step, so these run it under
node with a minimal DOM stub and a fake clock. Skipped when node is absent.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Loads app.js in a vm context with just enough DOM for the code paths under
# test. `scenario` is an async function body that can use the helpers below
# and must leave its verdict in `result`.
HARNESS = r"""
const vm = require('vm');
const fs = require('fs');

// ── fake clock ──
let now = 0, nextId = 1;
const timers = new Map();
function addTimer(cb, ms, repeat) {
    const id = nextId++;
    timers.set(id, { cb, at: now + Math.max(ms || 0, 0), ms, repeat });
    return id;
}
const flushMicro = () => new Promise(r => setImmediate(r));
async function advance(ms) {
    const end = now + ms;
    for (;;) {
        await flushMicro(); await flushMicro();
        let due = null;
        for (const [id, t] of timers) if (t.at <= end && (!due || t.at < due[1].at)) due = [id, t];
        if (!due) break;
        const [id, t] = due;
        now = t.at;
        if (t.repeat) t.at = now + t.ms; else timers.delete(id);
        t.cb();
    }
    now = end;
    await flushMicro(); await flushMicro();
}

// ── fake DOM ──
const els = {};
function classList() {
    const s = new Set();
    return { add: c => s.add(c), remove: (...c) => c.forEach(x => s.delete(x)),
             toggle: (c, f) => (f ?? !s.has(c)) ? s.add(c) : s.delete(c), contains: c => s.has(c) };
}
function makeEl(id) {
    const el = {
        id, innerHTML: '', value: '', style: {}, dataset: {}, classList: classList(),
        scrollHeight: 0, scrollTop: 0, clientHeight: 0, children: [],
        addEventListener() {}, focus() {}, select() {}, setAttribute() {},
        prepend(c) { if (c.id) els[c.id] = c; },
        appendChild(c) { this.children.push(c); },
        remove() { if (els[this.id] === this) delete els[this.id]; },
        // text node list, used by the log viewer
        nodes: [],
        get textContent() { return this.nodes.length ? this.nodes.map(n => n.textContent).join('') : (this._text || ''); },
        set textContent(v) { this.nodes = []; this._text = v; },
        append(t) {
            if (this._text) { this.nodes.push(textNode(this, this._text)); this._text = ''; }
            this.nodes.push(textNode(this, t));
        },
        get firstChild() { return this.nodes[0] || null; },
        get lastChild() { return this.nodes[this.nodes.length - 1] || null; },
    };
    return el;
}
function textNode(parent, text) {
    const n = { textContent: text, remove() { parent.nodes.splice(parent.nodes.indexOf(n), 1); } };
    return n;
}
const ALWAYS = ['header-node', 'cluster-bar', 'cluster-current', 'cluster-count', 'cluster-list',
    'cluster-search', 'cluster-dropdown', 'cluster-toggle', 'node-bar', 'status', 'countdown',
    'warnings-section', 'hardware-section', 'storage-section', 'system-section', 'network-section',
    'kubernetes-section', 'talos-section', 'containers-section', 'processes-section',
    'log-modal', 'log-modal-content', 'log-modal-title', 'log-modal-close', 'refresh-btn', 'theme-toggle'];
ALWAYS.forEach(id => { els[id] = makeEl(id); });
const listeners = {};
let detailsList = [];
let execCommandCalls = 0, execCommandResult = true;
const document = {
    body: makeEl('body'),
    getElementById: id => els[id] || null,
    querySelector: sel => sel === '.main' ? els.main || (els.main = makeEl('main')) : null,
    querySelectorAll: sel => sel === 'details[data-id]' ? detailsList : [],
    createElement: () => makeEl(''),
    addEventListener: (ev, cb) => { (listeners[ev] = listeners[ev] || []).push(cb); },
    execCommand: () => { execCommandCalls++; return execCommandResult; },
};

// ── fake network ──
let fetchLog = [], inFlight = 0, maxInFlight = 0;
let respond = (name) => ({ delay: 10, ok: true, status: 200, body: {} });
function fetch(url) {
    const name = url.split('/').pop();
    fetchLog.push({ name, at: now });
    inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
    const r = respond(name);
    return new Promise(res => addTimer(() => {
        inFlight--;
        res({ ok: r.ok, status: r.status, json: async () => r.body });
    }, r.delay, false));
}

const sockets = [];
class WebSocket {
    constructor(url) { this.url = url; this.closed = false; sockets.push(this); }
    close() { this.closed = true; }
}

const location = { protocol: 'http:', host: '10.0.0.11', port: '', search: '' };
const ctx = {
    console, document, fetch, WebSocket, location, AbortController, URL, URLSearchParams,
    setTimeout: (cb, ms) => addTimer(cb, ms, false), setInterval: (cb, ms) => addTimer(cb, ms, true),
    clearTimeout: id => timers.delete(id), clearInterval: id => timers.delete(id),
    localStorage: { getItem: () => null, setItem() {} },
    history: { replaceState() {} },
    navigator: {}, isSecureContext: false, scrollY: 0, scrollTo() {},
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx, { filename: 'app.js' });
const run = code => vm.runInContext(code, ctx);
const start = () => listeners.DOMContentLoaded.forEach(cb => cb());

let result = {};
(async () => {
__SCENARIO__
})().then(() => console.log(JSON.stringify(result)),
          e => { console.error(e && e.stack || e); process.exit(1); });
"""


def run_scenario(tmp_path, scenario):
    script = tmp_path / "harness.js"
    script.write_text(HARNESS.replace("__SCENARIO__", scenario))
    proc = subprocess.run(
        [NODE, str(script), str(APP_JS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


CLUSTER = [
    {
        "name": "cp-1",
        "ip": "10.0.0.11",
        "role": "control-plane",
        "ready": True,
        "current": True,
    },
    {
        "name": "gpu-1",
        "ip": "10.0.0.12",
        "role": "worker",
        "ready": True,
        "current": False,
    },
    {
        "name": "gpu-2",
        "ip": "10.0.0.13",
        "role": "worker",
        "ready": True,
        "current": False,
    },
    {
        "name": "wk-1",
        "ip": "fd00::14",
        "role": "worker",
        "ready": True,
        "current": False,
    },
]

NODE_DATA = {
    "hostname": "cp-1",
    "os_name": "Talos (v1.9)",
    "kernel_version": "6.6",
    "uptime_human": "1d",
    "cpu_model": "EPYC",
    "cpu_cores": 8,
    "cpu_threads": 16,
    "load_1m": 1.0,
    "ram_total_gb": 64,
    "ip_addresses": [],
    "talos_version": None,
}


def test_app_js_parses():
    proc = subprocess.run(
        [NODE, "--check", str(APP_JS)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


def test_slow_refresh_does_not_overlap(tmp_path):
    # Slow sections take 4s: refreshes must not stack up, and the countdown
    # must never go negative.
    res = run_scenario(
        tmp_path,
        """
        respond = name => ({ delay: ['storage', 'network', 'processes'].includes(name) ? 4000 : 50,
                             ok: false, status: 503, body: null });
        start();
        let minCountdown = 99;
        for (let i = 0; i < 400; i++) {
            await advance(100);
            const t = parseInt(document.getElementById('countdown').textContent || '10', 10);
            minCountdown = Math.min(minCountdown, t);
        }
        // a click on Refresh during a refresh must be ignored
        run('fetchOverview(); fetchOverview();');
        result = { runs: fetchLog.filter(f => f.name === 'node').length,
                   maxInFlight, minCountdown };
    """,
    )
    # 40s with a ~4s refresh + 10s countdown is ~3 runs; the bug gave 11.
    assert res["runs"] <= 4
    assert res["maxInFlight"] <= 11  # one refresh worth of section requests
    assert res["minCountdown"] >= 0


def test_hung_request_times_out(tmp_path):
    res = run_scenario(
        tmp_path,
        """
        let aborted = 0;
        fetch = undefined;
        ctx.fetch = (url, opts) => new Promise((_, rej) => {
            opts.signal.addEventListener('abort', () => { aborted++; const e = new Error('aborted'); e.name = 'AbortError'; rej(e); });
        });
        start();
        await advance(65000);
        result = { aborted, error: document.getElementById('error-bar')?.textContent || '' };
    """,
    )
    assert res["aborted"] >= 11
    assert "timeout" in res["error"]


def test_all_sections_failing_shows_error_not_fresh_timestamp(tmp_path):
    res = run_scenario(
        tmp_path,
        """
        respond = () => ({ delay: 10, ok: false, status: 500, body: null });
        start();
        await advance(500);
        result = { status: document.getElementById('status').textContent,
                   error: document.getElementById('error-bar')?.textContent || null };
    """,
    )
    assert "Last updated" not in res["status"]
    assert res["error"] and "all sections failed" in res["error"]


def test_partial_failure_and_401_are_reported(tmp_path):
    res = run_scenario(
        tmp_path,
        f"""
        respond = name => name === 'storage' ? {{ delay: 10, ok: false, status: 500 }}
                        : name === 'node' ? {{ delay: 10, ok: true, status: 200, body: {json.dumps(NODE_DATA)} }}
                        : {{ delay: 10, ok: false, status: 404 }};
        start();
        await advance(500);
        const partial = {{ status: document.getElementById('status').textContent,
                           error: document.getElementById('error-bar')?.textContent || '' }};
        respond = () => ({{ delay: 10, ok: false, status: 401 }});
        await advance(11000);
        result = {{ partial, auth: document.getElementById('error-bar')?.textContent || '' }};
    """,
    )
    assert "Last updated" in res["partial"]["status"]
    assert "storage (HTTP 500)" in res["partial"]["error"]
    assert "401" in res["auth"]


def test_header_role_and_node_bar_without_kubernetes_or_hardware(tmp_path):
    res = run_scenario(
        tmp_path,
        f"""
        respond = name => name === 'node' ? {{ delay: 10, ok: true, body: {json.dumps(NODE_DATA)} }}
                        : name === 'cluster_nodes' ? {{ delay: 10, ok: true, body: {json.dumps(CLUSTER)} }}
                        : {{ delay: 10, ok: false, status: 500 }};
        start();
        await advance(500);
        result = {{ header: document.getElementById('header-node').innerHTML,
                   nodeBar: document.getElementById('node-bar').innerHTML }};
    """,
    )
    assert "control-plane" in res["header"]
    assert "worker" not in res["header"]
    assert "Talos (v1.9)" in res["nodeBar"]
    assert "NaN" not in res["nodeBar"]


def test_open_state_toggled_during_refresh_is_kept(tmp_path):
    res = run_scenario(
        tmp_path,
        f"""
        respond = name => ({{ delay: name === 'storage' ? 4000 : 10, ok: name === 'node',
                              status: name === 'node' ? 200 : 500, body: {json.dumps(NODE_DATA)} }});
        const hw = {{ dataset: {{ id: 'hardware' }}, open: false }};
        detailsList = [hw];
        start();
        await advance(5000);          // first render done
        await advance(10000 + 100);   // second refresh: fast phase rendered
        hw.open = true;               // user expands during the slow wait
        await advance(4400);          // slow phase of that refresh completes
        result = {{ open: hw.open }};
    """,
    )
    assert res["open"] is True


def test_node_links_keep_port_scheme_and_bracket_ipv6(tmp_path):
    res = run_scenario(
        tmp_path,
        f"""
        location.port = '8080'; location.protocol = 'https:';
        run(`renderClusterBar({json.dumps(CLUSTER)}, null)`);
        result = {{ html: document.getElementById('cluster-list').innerHTML }};
    """,
    )
    assert 'href="https://10.0.0.12:8080/?theme=auto"' in res["html"]
    assert 'href="https://[fd00::14]:8080/?theme=auto"' in res["html"]
    assert "http://10.0.0.12/" not in res["html"]


def test_refresh_keeps_cluster_search_filter(tmp_path):
    res = run_scenario(
        tmp_path,
        f"""
        document.getElementById('cluster-search').value = 'GPU';
        run(`renderClusterBar({json.dumps(CLUSTER)}, null)`);
        result = {{ html: document.getElementById('cluster-list').innerHTML }};
    """,
    )
    assert "gpu-1" in res["html"] and "gpu-2" in res["html"]
    assert "wk-1" not in res["html"]


def test_node_strings_are_escaped(tmp_path):
    payload = "<img/src=x/onerror=alert(1)>"
    net = {
        "connectivity": {
            "default_gateway": None,
            "dns_servers": [payload],
            "dns_ok": True,
            "dns_result": None,
            "internet_ok": True,
            "kubernetes_api_ok": True,
        },
        "interfaces": [],
    }
    warnings = [
        {"severity": f'x">{payload}', "source": payload, "message": "m", "device": None}
    ]
    system = {
        "efi": {
            "entries": [{"number": "0001", "active": True, "label": "l", "path": "p"}],
            "boot_order": [payload],
            "boot_current": None,
            "timeout": None,
        }
    }
    res = run_scenario(
        tmp_path,
        f"""
        run('renderNetwork(' + JSON.stringify({json.dumps(net)}) + ')');
        run('renderWarnings(' + JSON.stringify({json.dumps(warnings)}) + ')');
        run('renderSystem(' + JSON.stringify({json.dumps(system)}) + ')');
        result = {{ html: ['network-section', 'warnings-section', 'system-section']
                         .map(id => document.getElementById(id).innerHTML).join('\\n') }};
    """,
    )
    assert "<img" not in res["html"]


def test_copy_works_without_secure_context(tmp_path):
    # Plain http://<node-ip>: navigator.clipboard is undefined.
    res = run_scenario(
        tmp_path,
        """
        const ok = await run('copyText("ssh -p 2022 debug@10.0.0.11")');
        execCommandResult = false;
        const failed = await run('copyText("x")');
        result = { ok, failed, execCommandCalls };
    """,
    )
    assert res == {"ok": True, "failed": False, "execCommandCalls": 2}


def test_log_viewer_ignores_previous_socket_and_caps_size(tmp_path):
    res = run_scenario(
        tmp_path,
        """
        run("openLogViewer('aaa', 'A')");
        const a = sockets[0];
        a.onopen();
        run("openLogViewer('bbb', 'B')");
        const b = sockets[1];
        b.onopen();
        // A's late close must not reach B's viewer
        if (a.onclose) a.onclose();
        b.onmessage({ data: 'hello\\n' });
        await advance(200);
        const afterSwitch = document.getElementById('log-modal-content').textContent;
        const line = 'x'.repeat(999) + '\\n';
        for (let i = 0; i < 5000; i++) {
            b.onmessage({ data: line });
            if (i % 500 === 0) await advance(200);
        }
        await advance(200);
        const len = document.getElementById('log-modal-content').textContent.length;
        result = { aClosed: a.closed, afterSwitch, len, max: run('LOG_MAX_CHARS') };
    """,
    )
    assert res["aClosed"] is True
    assert res["afterSwitch"] == "hello\n"
    assert res["len"] <= res["max"] + 600 * 1000  # at most one flush over the cap


def test_degraded_sections_render_and_report_failed_collectors(tmp_path):
    # The backend nulls a failed collector's field and names it in `errors`
    # (hardware.cpu/memory, network.connectivity). That must not stop the
    # rest of the page from rendering, and must be shown to the operator.
    hw = {
        "cpu": None,
        "memory": None,
        "pci_devices": [],
        "usb_devices": [],
        "nics": [],
        "sensors": [],
        "gpus": [],
        "errors": ["cpu", "memory"],
    }
    net = {"interfaces": [], "connectivity": None, "errors": ["connectivity"]}
    system = {
        "efi": {"entries": [], "boot_order": [], "boot_current": None, "timeout": None}
    }
    res = run_scenario(
        tmp_path,
        f"""
        const bodies = {{ node: {json.dumps(NODE_DATA)}, hardware: {json.dumps(hw)},
                          network: {json.dumps(net)}, system: {json.dumps(system)} }};
        respond = name => name in bodies ? {{ delay: 10, ok: true, status: 200, body: bodies[name] }}
                                          : {{ delay: 10, ok: false, status: 503 }};
        start();
        await advance(500);
        result = {{ hardware: document.getElementById('hardware-section').innerHTML,
                    network: document.getElementById('network-section').innerHTML,
                    system: document.getElementById('system-section').innerHTML,
                    nodeBar: document.getElementById('node-bar').innerHTML,
                    error: document.getElementById('error-bar')?.textContent || '' }};
    """,
    )
    assert "Not available" in res["hardware"]
    assert "Not available" in res["network"]
    assert "EFI boot manager" in res["system"]  # rendered after hardware/network
    assert "Talos (v1.9)" in res["nodeBar"]
    assert "hardware/cpu" in res["error"] and "network/connectivity" in res["error"]
    assert "Cannot read" not in res["error"]


def test_render_error_in_one_section_does_not_abort_the_others(tmp_path):
    system = {
        "efi": {"entries": [], "boot_order": [], "boot_current": None, "timeout": None}
    }
    res = run_scenario(
        tmp_path,
        f"""
        // storage payload of an unexpected shape makes its renderer throw
        const bodies = {{ node: {json.dumps(NODE_DATA)}, storage: {{}}, system: {json.dumps(system)} }};
        respond = name => name in bodies ? {{ delay: 10, ok: true, status: 200, body: bodies[name] }}
                                          : {{ delay: 10, ok: false, status: 503 }};
        start();
        await advance(500);
        result = {{ system: document.getElementById('system-section').innerHTML,
                    status: document.getElementById('status').textContent,
                    error: document.getElementById('error-bar')?.textContent || '' }};
    """,
    )
    assert "EFI boot manager" in res["system"]
    assert "Last updated" in res["status"]
    assert "storage (render error)" in res["error"]
