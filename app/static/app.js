// ── Theme ──
function getCurrentTheme() {
    return localStorage.getItem('theme') || 'auto';
}

function applyTheme(theme) {
    document.body.classList.remove('light', 'auto');
    if (theme === 'light') {
        document.body.classList.add('light');
    } else if (theme === 'auto') {
        document.body.classList.add('auto');
    }
    // dark = no class needed (default)
    document.querySelectorAll('.theme-toggle button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.theme === theme);
    });
    localStorage.setItem('theme', theme);
}

// Apply theme immediately: URL param > localStorage > auto (default)
(function() {
    const urlTheme = new URLSearchParams(window.location.search).get('theme');
    if (urlTheme && ['auto', 'light', 'dark'].includes(urlTheme)) {
        applyTheme(urlTheme);
        // Clean the URL param after saving to localStorage
        const url = new URL(window.location);
        url.searchParams.delete('theme');
        window.history.replaceState({}, '', url);
    } else {
        applyTheme(getCurrentTheme());
    }
})();

const REFRESH_INTERVAL = 10000;
let timer = null;
let countdown = 10;
let lastData = null;

// Section fetching — fast sections first, slow ones in background
const FAST_SECTIONS = ['node', 'hardware', 'warnings', 'containers', 'cluster_nodes'];
const SLOW_SECTIONS = ['kubernetes', 'talos', 'storage', 'system', 'network', 'processes'];

async function fetchSection(name) {
    try {
        const resp = await fetch(`/api/sections/${name}`);
        if (!resp.ok) return null;
        return await resp.json();
    } catch {
        return null;
    }
}

async function fetchOverview() {
    try {
        const openState = isFirstRender ? {} : saveOpenState();
        const scrollY = window.scrollY;

        // Fetch fast sections in parallel
        const fastResults = await Promise.all(FAST_SECTIONS.map(s => fetchSection(s)));
        const data = {};
        FAST_SECTIONS.forEach((name, i) => { if (fastResults[i] != null) data[name] = fastResults[i]; });

        // Render fast sections immediately
        if (data.cluster_nodes) renderClusterBar(data.cluster_nodes, data.kubernetes?.ssh_info);
        if (data.node && data.hardware) renderNodeBar(data.node, data.hardware);
        if (data.warnings) renderWarnings(data.warnings);
        if (data.hardware) renderHardware(data.hardware);
        if (data.containers) renderContainers(data.containers);

        // Show header
        if (data.node) {
            const role = data.kubernetes?.node_info?.labels?.['node-role.kubernetes.io/control-plane'] !== undefined ? 'control-plane' : 'worker';
            document.getElementById('header-node').innerHTML = `${esc(data.node.hostname)} <span class="role-badge ${role === 'control-plane' ? 'role-cp' : 'role-worker'}">${role}</span>`;
        }

        // Restore scroll + open state after fast render
        if (!isFirstRender) {
            restoreOpenState(openState);
            window.scrollTo(0, scrollY);
        }

        // Fetch slow sections in parallel (background)
        const slowResults = await Promise.all(SLOW_SECTIONS.map(s => fetchSection(s)));
        SLOW_SECTIONS.forEach((name, i) => { if (slowResults[i] != null) data[name] = slowResults[i]; });

        // Save scroll again before slow render
        const scrollY2 = window.scrollY;

        // Render slow sections — also update SSH info + cluster bar once K8s data arrives
        if (data.kubernetes) {
            renderKubernetes(data.kubernetes);
            if (data.kubernetes.ssh_info) renderClusterBar(clusterNodes, data.kubernetes.ssh_info);
        }
        if (data.talos) renderTalos(data.talos);
        if (data.storage) renderStorage(data.storage);
        if (data.system) renderSystem(data.system);
        if (data.network) renderNetwork(data.network);
        if (data.processes) renderProcesses(data.processes);

        // Update role badge now that K8s data is available
        if (data.node && data.kubernetes) {
            const role = data.kubernetes.node_info?.labels?.['node-role.kubernetes.io/control-plane'] !== undefined ? 'control-plane' : 'worker';
            document.getElementById('header-node').innerHTML = `${esc(data.node.hostname)} <span class="role-badge ${role === 'control-plane' ? 'role-cp' : 'role-worker'}">${role}</span>`;
        }

        lastData = data;
        if (!isFirstRender) {
            restoreOpenState(openState);
            window.scrollTo(0, scrollY2);
        }
        isFirstRender = false;

        document.getElementById('status').textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
        document.getElementById('error-bar')?.remove();
    } catch (err) {
        showError(err.message);
    }
    resetCountdown();
}

function showError(msg) {
    let bar = document.getElementById('error-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'error-bar';
        bar.style.cssText = 'background:#f8514926;color:#f85149;padding:8px 20px;font-size:12px;text-align:center;';
        document.querySelector('.main').prepend(bar);
    }
    bar.textContent = `Error fetching data: ${msg}`;
}

function resetCountdown() {
    countdown = REFRESH_INTERVAL / 1000;
    if (timer) clearInterval(timer);
    timer = setInterval(() => {
        countdown--;
        const el = document.getElementById('countdown');
        if (el) el.textContent = `${countdown}s`;
        if (countdown <= 0) {
            fetchOverview();
        }
    }, 1000);
}

// ── Details open state persistence ──
function saveOpenState() {
    const state = {};
    document.querySelectorAll('details[data-id]').forEach(d => {
        state[d.dataset.id] = d.open;
    });
    return state;
}

function restoreOpenState(state) {
    document.querySelectorAll('details[data-id]').forEach(d => {
        const id = d.dataset.id;
        if (id in state) {
            d.open = state[id];
        }
    });
}

let isFirstRender = true;

// ── Cluster Node Bar ──
let clusterNodes = [];
let sshInfo = { enabled: false, port: 2022, password_auth: true };

function renderClusterBar(nodes, ssh) {
    clusterNodes = nodes || [];
    if (ssh) sshInfo = ssh;
    const el = document.getElementById('cluster-bar');
    if (!clusterNodes.length) { el.style.display = 'none'; return; }
    el.style.display = '';

    const current = clusterNodes.find(n => n.current);
    const readyCount = clusterNodes.filter(n => n.ready).length;

    let currentHtml = '';
    if (current) {
        const roleTag = current.role === 'control-plane' ? '<span class="cn-role cn-cp">CP</span>' : '<span class="cn-role cn-wk">W</span>';
        currentHtml = `${roleTag} <span class="cn-name">${esc(current.name)}</span> <span class="cn-ip">${esc(current.ip)}</span>`;
        if (sshInfo.enabled) {
            const sshCmd = `ssh -p ${sshInfo.port} debug@${current.ip}`;
            currentHtml += ` <span class="ssh-badge" title="Click to copy: ${sshCmd}" onclick="navigator.clipboard.writeText('${sshCmd}');this.textContent='Copied!';setTimeout(()=>this.textContent='SSH',1000)">SSH</span>`;
        }
    }
    document.getElementById('cluster-current').innerHTML = currentHtml;
    document.getElementById('cluster-count').textContent = `${readyCount}/${clusterNodes.length} nodes`;
    renderClusterList('');
}

function renderClusterList(query) {
    const list = document.getElementById('cluster-list');
    const theme = getCurrentTheme();
    const filtered = clusterNodes.filter(n =>
        !query || n.name.toLowerCase().includes(query) || n.ip.includes(query)
    );
    list.innerHTML = filtered.map(n => {
        const dot = n.ready ? '<span class="cn-dot cn-ready"></span>' : '<span class="cn-dot cn-notready"></span>';
        const role = n.role === 'control-plane' ? '<span class="cn-role cn-cp">CP</span>' : '<span class="cn-role cn-wk">W</span>';
        const link = n.current ? '#' : `http://${n.ip}/?theme=${theme}`;
        const cls = n.current ? 'cluster-item current' : 'cluster-item';
        const sshBadge = sshInfo.enabled && n.ready
            ? `<span class="ssh-badge-sm" title="ssh -p ${sshInfo.port} debug@${n.ip}" onclick="event.preventDefault();navigator.clipboard.writeText('ssh -p ${sshInfo.port} debug@${n.ip}');this.textContent='Copied!';setTimeout(()=>this.textContent='SSH',1000)">SSH</span>`
            : '';
        return `<a href="${link}" class="${cls}">${dot}${role}<span class="cn-name">${esc(n.name)}</span>${sshBadge}<span class="cn-ip">${esc(n.ip)}</span></a>`;
    }).join('');
}

// ── Node Info Bar ──
function gauge(label, percent, valueText) {
    const color = percent > 90 ? 'var(--red)' : percent > 70 ? 'var(--yellow)' : 'var(--green)';
    return `<div class="gauge">
        <div class="gauge-label">${label}</div>
        <div class="gauge-bar"><div class="gauge-fill" style="width:${Math.min(percent, 100)}%;background:${color}"></div></div>
        <div class="gauge-value">${valueText}</div>
    </div>`;
}

function renderNodeBar(node, hardware) {
    const el = document.getElementById('node-bar');
    // CPU usage from load average vs cores
    const cpuPercent = Math.round((node.load_1m / node.cpu_threads) * 100);
    // RAM usage from hardware memory
    const mem = hardware?.memory;
    const ramPercent = mem ? Math.round(mem.used_percent) : 0;
    const ramUsed = mem ? (mem.total_gb - mem.available_gb).toFixed(1) : '?';

    el.innerHTML = `
        <div class="node-bar-info">
            <div class="item"><span class="label">OS:</span><span class="value">${esc(node.os_name)}</span></div>
            ${node.talos_version ? `<div class="item"><span class="label">Talos:</span><span class="value">${esc(node.talos_version)}</span></div>` : ''}
            <div class="item"><span class="label">Kernel:</span><span class="value">${esc(node.kernel_version)}</span></div>
            <div class="item"><span class="label">Up:</span><span class="value">${esc(node.uptime_human)}</span></div>
            <div class="item"><span class="label">CPU:</span><span class="value">${esc(node.cpu_model)} (${node.cpu_cores}c/${node.cpu_threads}t)</span></div>
            ${node.ip_addresses.filter(ip => ip.family === 'inet').map(ip =>
                `<div class="item"><span class="label">${esc(ip.interface)}:</span><span class="value">${esc(ip.address)}/${ip.prefix_length}</span></div>`
            ).join('')}
        </div>
        <div class="node-bar-gauges">
            ${gauge('CPU', cpuPercent, `${cpuPercent}% (load ${node.load_1m})`)}
            ${gauge('RAM', ramPercent, `${ramUsed}/${node.ram_total_gb} GB (${ramPercent}%)`)}
        </div>
    `;
}

// ── Warnings ──
function renderWarnings(warnings) {
    const el = document.getElementById('warnings-section');
    const crits = warnings.filter(w => w.severity === 'critical');
    const warns = warnings.filter(w => w.severity === 'warning');

    el.className = 'warnings-banner ' + (crits.length ? 'has-critical' : warns.length ? 'has-warning' : 'all-ok');

    let html = '<details data-id="warnings" open><summary>';
    if (crits.length + warns.length === 0) {
        html += '<span class="badge ok">OK</span> No warnings';
    } else {
        if (crits.length) html += `<span class="badge critical">${crits.length} critical</span> `;
        if (warns.length) html += `<span class="badge warning">${warns.length} warning</span>`;
    }
    html += '</summary><div class="section-content">';

    for (const w of warnings) {
        html += `<div class="warning-item">
            <span class="warning-dot ${w.severity}"></span>
            <span class="sev-${w.severity}">[${w.source}]</span>
            ${esc(w.message)}${w.device ? ` <span style="color:var(--text-dim)">(${esc(w.device)})</span>` : ''}
        </div>`;
    }
    if (warnings.length === 0) {
        html += '<div style="color:var(--green);padding:4px 0">All systems nominal</div>';
    }
    html += '</div></details>';
    el.innerHTML = html;
}

// ── Hardware ──
function renderHardware(hw) {
    const el = document.getElementById('hardware-section');
    let html = '<details data-id="hardware"><summary>Hardware</summary><div class="section-content">';

    // CPU
    html += subSection('hw-cpu', 'CPU', `
        <div class="kv-grid">
            <span class="kv-key">Model</span><span class="kv-val">${esc(hw.cpu.model)}</span>
            <span class="kv-key">Architecture</span><span class="kv-val">${esc(hw.cpu.architecture)}</span>
            <span class="kv-key">Sockets</span><span class="kv-val">${hw.cpu.sockets}</span>
            <span class="kv-key">Cores/Socket</span><span class="kv-val">${hw.cpu.cores_per_socket}</span>
            <span class="kv-key">Threads</span><span class="kv-val">${hw.cpu.threads}</span>
        </div>
    `);

    // Memory
    const mem = hw.memory;
    let memHtml = `
        <div class="kv-grid">
            <span class="kv-key">Total</span><span class="kv-val">${mem.total_gb} GB</span>
            <span class="kv-key">Available</span><span class="kv-val">${mem.available_gb} GB</span>
            <span class="kv-key">Used</span><span class="kv-val">${mem.used_percent}%</span>
            <span class="kv-key">ECC CE</span><span class="kv-val ${mem.ecc_correctable_errors > 0 ? 'sev-warning' : ''}">${mem.ecc_correctable_errors}</span>
            <span class="kv-key">ECC UE</span><span class="kv-val ${mem.ecc_uncorrectable_errors > 0 ? 'sev-critical' : ''}">${mem.ecc_uncorrectable_errors}</span>
        </div>
    `;
    if (mem.dimms.length) {
        memHtml += table(['Locator', 'Size', 'Type', 'Speed', 'Manufacturer', 'Serial', 'Part'],
            mem.dimms.map(d => [
                d.locator, d.size_mb ? `${d.size_mb} MB` : '-', d.type || '-',
                d.speed_mhz ? `${d.speed_mhz} MHz` : '-', d.manufacturer || '-',
                d.serial || '-', d.part_number || '-'
            ])
        );
    }
    html += subSection('hw-memory', 'Memory (DIMMs)', memHtml);

    // PCI
    if (hw.pci_devices.length) {
        html += subSection('hw-pci', 'PCI Devices', table(
            ['Slot', 'Class', 'Vendor', 'Device', 'Driver'],
            hw.pci_devices.map(d => [d.slot, d.class_name, d.vendor, d.device, d.driver || '-'])
        ));
    }

    // USB
    if (hw.usb_devices.length) {
        html += subSection('hw-usb', 'USB Devices', table(
            ['Bus', 'Dev', 'ID', 'Name'],
            hw.usb_devices.map(d => [d.bus, d.device, d.id, d.name])
        ));
    }

    // NICs
    if (hw.nics.length) {
        html += subSection('hw-nics', 'Network Interfaces', table(
            ['Name', 'MAC', 'Driver', 'Speed', 'Link', 'IPs', 'Errors'],
            hw.nics.map(n => [
                n.name, n.mac, n.driver || '-', n.speed || '-',
                n.link_detected ? '<span class="sev-ok">up</span>' : '<span class="sev-critical">down</span>',
                n.ip_addresses.join(', ') || '-',
                (n.rx_errors + n.tx_errors + n.rx_crc_errors + n.tx_carrier_errors) || '0'
            ])
        ));
    }

    // Sensors
    if (hw.sensors.length) {
        const byChip = {};
        for (const s of hw.sensors) {
            (byChip[s.name] = byChip[s.name] || []).push(s);
        }
        let sensorsHtml = '';
        for (const [chip, readings] of Object.entries(byChip)) {
            sensorsHtml += `<div style="margin-top:6px;font-weight:500;color:var(--text-bright)">${esc(chip)}</div>`;
            sensorsHtml += table(
                ['Label', 'Value', 'Warning', 'Critical', 'Status'],
                readings.map(r => [
                    r.label,
                    `${r.value} ${r.unit}`,
                    r.warning != null ? `${r.warning} ${r.unit}` : '-',
                    r.critical != null ? `${r.critical} ${r.unit}` : '-',
                    r.is_alarm ? '<span class="sev-critical">ALARM</span>' : '<span class="sev-ok">OK</span>'
                ])
            );
        }
        html += subSection('hw-sensors', 'Sensors', sensorsHtml);
    }

    // GPUs
    if (hw.gpus.length) {
        html += subSection('hw-gpus', 'GPU', table(
            ['#', 'Name', 'Driver', 'VRAM', 'Used', 'Temp', 'Util', 'Power'],
            hw.gpus.map(g => [
                g.index, g.name, g.driver_version,
                `${g.memory_total_mb} MB`, `${g.memory_used_mb} MB`,
                g.temperature_c != null ? `${g.temperature_c}°C` : '-',
                g.utilization_percent != null ? `${g.utilization_percent}%` : '-',
                g.power_draw_w != null ? `${g.power_draw_w} W` : '-'
            ])
        ));
    }

    html += '</div></details>';
    el.innerHTML = html;
}

// ── Storage ──
function renderStorage(storage) {
    const el = document.getElementById('storage-section');
    let html = '<details data-id="storage"><summary>Storage</summary><div class="section-content">';

    // Disks
    if (storage.disks.length) {
        let disksHtml = table(
            ['Name', 'Model', 'Serial', 'Size', 'Transport', 'Rotational'],
            storage.disks.map(d => [
                d.name, d.model || '-', d.serial || '-', d.size,
                d.transport || '-', d.rotational ? 'HDD' : 'SSD/NVMe'
            ])
        );
        for (const disk of storage.disks) {
            if (disk.partitions.length) {
                disksHtml += `<div style="margin:6px 0 2px;color:var(--text-dim)">Partitions of ${esc(disk.name)}:</div>`;
                disksHtml += table(
                    ['Name', 'Size', 'Type', 'FS', 'Mount'],
                    disk.partitions.map(p => [p.name, p.size, p.type, p.fstype || '-', p.mountpoint || '-'])
                );
            }
        }
        html += subSection('stor-disks', 'Disks & Partitions', disksHtml);
    }

    // SMART
    if (storage.smart.length) {
        let smartHtml = '';
        for (const s of storage.smart) {
            const statusClass = s.health_passed ? 'sev-ok' : 'sev-critical';
            smartHtml += `<div style="margin-top:8px;font-weight:500;color:var(--text-bright)">${esc(s.device)} — ${esc(s.model || 'Unknown')}</div>`;
            smartHtml += `<div class="kv-grid">
                <span class="kv-key">Health</span><span class="kv-val ${statusClass}">${s.health_passed ? 'PASSED' : 'FAILED'}</span>
                <span class="kv-key">Serial</span><span class="kv-val">${esc(s.serial || '-')}</span>
                <span class="kv-key">Temperature</span><span class="kv-val">${s.temperature_celsius != null ? s.temperature_celsius + '°C' : '-'}</span>
                <span class="kv-key">Power-On Hours</span><span class="kv-val">${s.power_on_hours != null ? s.power_on_hours.toLocaleString() + 'h' : '-'}</span>
                <span class="kv-key">Wear Level</span><span class="kv-val">${s.wear_leveling_percent != null ? s.wear_leveling_percent + '%' : '-'}</span>
                <span class="kv-key">Reallocated Sectors</span><span class="kv-val ${(s.reallocated_sectors || 0) > 0 ? 'sev-warning' : ''}">${s.reallocated_sectors != null ? s.reallocated_sectors : '-'}</span>
            </div>`;
            if (s.attributes.length) {
                const devId = s.device.replace(/\//g, '-');
                smartHtml += `<details data-id="smart-attr-${devId}" class="sub-section" style="margin-top:4px"><summary>SMART Attributes (${s.attributes.length})</summary><div class="section-content">`;
                smartHtml += table(
                    ['ID', 'Name', 'Value', 'Worst', 'Thresh', 'Raw'],
                    s.attributes.map(a => [a.id, a.name, a.value, a.worst, a.threshold, a.raw_value])
                );
                smartHtml += '</div></details>';
            }
        }
        html += subSection('stor-smart', 'SMART Health', smartHtml);
    }

    // Usage
    if (storage.usage.length) {
        html += subSection('stor-usage', 'Disk Usage', table(
            ['Filesystem', 'Mount', 'Type', 'Size', 'Used', 'Avail', 'Used%'],
            storage.usage.map(u => [
                u.filesystem, u.mount, u.fs_type, u.size, u.used, u.available,
                `<span class="sev-${u.severity}">${u.used_percent}%</span>`
            ])
        ));
    }

    html += '</div></details>';
    el.innerHTML = html;
}

// ── System ──
function renderSystem(sys) {
    const el = document.getElementById('system-section');
    const efi = sys.efi;
    let html = '<details data-id="system"><summary>System</summary><div class="section-content">';

    if (efi.entries.length) {
        let efiHtml = `<div class="kv-grid">
            <span class="kv-key">Boot Current</span><span class="kv-val">${esc(efi.boot_current || '-')}</span>
            <span class="kv-key">Boot Order</span><span class="kv-val">${efi.boot_order.join(', ') || '-'}</span>
            <span class="kv-key">Timeout</span><span class="kv-val">${esc(efi.timeout || '-')}</span>
        </div>`;
        efiHtml += table(
            ['#', 'Active', 'Label', 'Path'],
            efi.entries.map(e => [
                e.number,
                e.active ? '<span class="sev-ok">*</span>' : '',
                e.label,
                `<span style="color:var(--text-dim);font-size:11px">${esc(e.path)}</span>`
            ])
        );
        html += subSection('sys-efi', 'UEFI Boot Order', efiHtml);
    } else {
        html += '<div style="color:var(--text-dim);padding:8px 0">EFI boot manager not available (legacy BIOS or not accessible)</div>';
    }

    html += '</div></details>';
    el.innerHTML = html;
}

// ── Network ──
function renderNetwork(net) {
    const el = document.getElementById('network-section');
    let html = '<details data-id="network"><summary>Network</summary><div class="section-content">';

    const conn = net.connectivity;
    let connHtml = `<div class="kv-grid">
        <span class="kv-key">Default Gateway</span><span class="kv-val">${esc(conn.default_gateway || 'none')}</span>
        <span class="kv-key">DNS Servers</span><span class="kv-val">${conn.dns_servers.join(', ') || '-'}</span>
        <span class="kv-key">DNS Resolution</span><span class="kv-val ${conn.dns_ok ? 'sev-ok' : 'sev-critical'}">${conn.dns_ok ? 'OK' : 'FAILED'}${conn.dns_result ? ' (' + esc(conn.dns_result) + ')' : ''}</span>
        <span class="kv-key">Internet</span><span class="kv-val ${conn.internet_ok ? 'sev-ok' : 'sev-critical'}">${conn.internet_ok ? 'Reachable' : 'Unreachable'}</span>
        <span class="kv-key">K8s API</span><span class="kv-val ${conn.kubernetes_api_ok ? 'sev-ok' : 'sev-critical'}">${conn.kubernetes_api_ok ? 'Reachable' : 'Unreachable'}</span>
    </div>`;
    html += subSection('net-conn', 'Connectivity', connHtml);

    if (net.interfaces.length) {
        html += subSection('net-ifaces', 'Interfaces', table(
            ['Name', 'MAC', 'Driver', 'Speed', 'Duplex', 'Link', 'IPs'],
            net.interfaces.map(n => [
                n.name, n.mac, n.driver || '-', n.speed || '-', n.duplex || '-',
                n.link_detected ? '<span class="sev-ok">up</span>' : '<span class="sev-critical">down</span>',
                n.ip_addresses.join(', ') || '-'
            ])
        ));
    }

    html += '</div></details>';
    el.innerHTML = html;
}

// ── Helpers ──
function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
}

function table(headers, rows) {
    let html = '<table><thead><tr>';
    for (const h of headers) html += `<th>${esc(h)}</th>`;
    html += '</tr></thead><tbody>';
    for (const row of rows) {
        html += '<tr>';
        for (const cell of row) html += `<td>${cell}</td>`;  // cells may contain HTML
        html += '</tr>';
    }
    html += '</tbody></table>';
    return html;
}

function subSection(id, title, content) {
    return `<details data-id="${esc(id)}" class="sub-section"><summary>${esc(title)}</summary><div class="section-content">${content}</div></details>`;
}

function severityBadge(severity, text) {
    return `<span class="sev-${severity}">${text}</span>`;
}

function humanBytes(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let val = bytes;
    while (val >= 1024 && i < units.length - 1) { val /= 1024; i++; }
    return `${val.toFixed(1)} ${units[i]}`;
}

// ── Kubernetes ──
function renderKubernetes(k8s) {
    const el = document.getElementById('kubernetes-section');
    if (!k8s) { el.innerHTML = ''; return; }
    let html = '<details data-id="kubernetes"><summary>Kubernetes</summary><div class="section-content">';

    // Node Info
    const ni = k8s.node_info;
    let nodeHtml = `<div class="kv-grid">
        <span class="kv-key">Kubelet</span><span class="kv-val">${esc(ni.kubelet_version)}</span>
        <span class="kv-key">Runtime</span><span class="kv-val">${esc(ni.container_runtime)}</span>
        <span class="kv-key">OS Image</span><span class="kv-val">${esc(ni.os_image)}</span>
        <span class="kv-key">Arch</span><span class="kv-val">${esc(ni.architecture)}</span>
    </div>`;

    // Labels
    if (Object.keys(ni.labels).length) {
        nodeHtml += '<div style="margin-top:8px;font-weight:500;color:var(--text-bright)">Labels</div>';
        nodeHtml += table(['Key', 'Value'],
            Object.entries(ni.labels).map(([k, v]) => [k, v])
        );
    }

    // Conditions
    if (ni.conditions.length) {
        nodeHtml += '<div style="margin-top:8px;font-weight:500;color:var(--text-bright)">Conditions</div>';
        nodeHtml += table(['Type', 'Status', 'Reason', 'Last Transition'],
            ni.conditions.map(c => {
                const ok = (c.type === 'Ready' && c.status === 'True') || (c.type !== 'Ready' && c.status === 'False');
                return [c.type, severityBadge(ok ? 'ok' : 'critical', c.status), c.reason, c.last_transition];
            })
        );
    }

    // Resources
    if (ni.capacity.cpu || ni.allocatable.cpu) {
        nodeHtml += '<div style="margin-top:8px;font-weight:500;color:var(--text-bright)">Resources</div>';
        nodeHtml += table(['Resource', 'Capacity', 'Allocatable'],
            [
                ['CPU', ni.capacity.cpu, ni.allocatable.cpu],
                ['Memory', ni.capacity.memory, ni.allocatable.memory],
                ['Pods', ni.capacity.pods, ni.allocatable.pods],
                ni.capacity.gpu_nvidia ? ['GPU (NVIDIA)', ni.capacity.gpu_nvidia, ni.allocatable.gpu_nvidia] : null,
            ].filter(Boolean)
        );
    }

    html += subSection('k8s-node', 'Node Info', nodeHtml);

    // API Endpoint
    if (k8s.api_endpoint.url) {
        html += `<div style="padding:6px 12px;font-size:12px;color:var(--text-dim)">
            API: ${esc(k8s.api_endpoint.url)} — ${severityBadge(k8s.api_endpoint.healthy ? 'ok' : 'critical', k8s.api_endpoint.healthy ? 'Healthy' : 'Unhealthy')}
        </div>`;
    }

    // Certificates
    if (k8s.certificates.length) {
        html += subSection('k8s-certs', 'Certificates', table(
            ['File', 'Subject', 'Issuer', 'Not After', 'Expires In', 'Fingerprint'],
            k8s.certificates.map(c => [
                c.file_path.split('/').pop(),
                esc(c.subject), esc(c.issuer), esc(c.not_after),
                severityBadge(c.expiry_severity, (c.days_until_expiry != null ? c.days_until_expiry + 'd' : '-')),
                `<span style="font-size:10px;color:var(--text-dim)">${esc((c.sha256_fingerprint || '').substring(0, 24))}...</span>`
            ])
        ));
    }

    // Components
    if (k8s.components.length) {
        let compHtml = table(
            ['Name', 'Running', 'Health', 'Uptime'],
            k8s.components.map(c => [
                c.name,
                c.running ? severityBadge('ok', 'Yes') : severityBadge('critical', 'No'),
                severityBadge(c.health_status === 'Healthy' ? 'ok' : c.health_status === 'Unknown' ? '' : 'critical', c.health_status),
                c.uptime || '-'
            ])
        );

        // etcd details (CP nodes only)
        const etcd = k8s.components.find(c => c.name === 'etcd' && c.etcd_status);
        if (etcd && etcd.etcd_status) {
            const e = etcd.etcd_status;
            compHtml += '<div style="margin-top:10px;font-weight:500;color:var(--text-bright)">etcd Details</div>';
            compHtml += `<div class="kv-grid">
                <span class="kv-key">DB Size</span><span class="kv-val">${e.db_size_mb} MB</span>
                <span class="kv-key">DB In Use</span><span class="kv-val">${e.db_size_in_use_mb} MB</span>
                <span class="kv-key">Leader</span><span class="kv-val">${e.is_leader ? severityBadge('ok', 'This node') : esc(e.leader_id)}</span>
                <span class="kv-key">Member ID</span><span class="kv-val">${esc(e.member_id)}</span>
                <span class="kv-key">Raft Index</span><span class="kv-val">${e.raft_index}</span>
            </div>`;
            if (e.members.length) {
                compHtml += '<div style="margin-top:6px;font-weight:500;color:var(--text-bright)">etcd Members</div>';
                compHtml += table(['Name', 'ID', 'Client URLs', 'Peer URLs'],
                    e.members.map(m => [
                        esc(m.name),
                        m.id === e.leader_id ? severityBadge('ok', esc(m.id) + ' (leader)') : esc(m.id),
                        (m.client_urls || []).map(esc).join(', '),
                        (m.peer_urls || []).map(esc).join(', ')
                    ])
                );
            }
        }

        html += subSection('k8s-components', 'Components', compHtml);
    }

    html += '</div></details>';
    el.innerHTML = html;
}

// ── Talos ──
function renderTalos(talos) {
    const el = document.getElementById('talos-section');
    if (!talos) { el.innerHTML = ''; return; }
    const talosLogo = '<svg viewBox="0 0 24 24" width="16" height="16" style="vertical-align:middle;margin-right:6px"><polygon points="12,2 22,7.5 22,16.5 12,22 2,16.5 2,7.5" fill="none" stroke="var(--cyan)" stroke-width="1.5"/><text x="12" y="15" text-anchor="middle" font-size="9" font-weight="700" fill="var(--cyan)" font-family="monospace">T</text></svg>';
    let html = `<details data-id="talos"><summary>${talosLogo}Talos</summary><div class="section-content">`;

    // Version
    let versionHtml = `<div class="kv-grid">
        <span class="kv-key">Version</span><span class="kv-val">${esc(talos.version.version)}</span>
        ${talos.version.schematic_id ? `<span class="kv-key">Schematic</span><span class="kv-val">${esc(talos.version.schematic_id)}</span>` : ''}
    </div>`;

    // Machine config
    const mc = talos.machine_config;
    if (mc.config_available) {
        versionHtml += `<div class="kv-grid" style="margin-top:8px">
            <span class="kv-key">Type</span><span class="kv-val">${esc(mc.machine_type)}</span>
            <span class="kv-key">Install Disk</span><span class="kv-val">${esc(mc.install_disk)}</span>
            <span class="kv-key">Install Image</span><span class="kv-val" style="word-break:break-all">${esc(mc.install_image)}</span>
            <span class="kv-key">Cluster</span><span class="kv-val">${esc(mc.cluster_name)}</span>
            <span class="kv-key">Endpoint</span><span class="kv-val">${esc(mc.cluster_endpoint)}</span>
        </div>`;

        if (mc.extensions.length) {
            versionHtml += '<div style="margin-top:8px;font-weight:500;color:var(--text-bright)">Extensions</div>';
            versionHtml += '<div style="padding:4px 0">' + mc.extensions.map(e => `<span style="background:var(--bg);padding:2px 6px;border-radius:3px;margin:2px;display:inline-block;font-size:11px">${esc(e)}</span>`).join('') + '</div>';
        }

        if (mc.network_interfaces.length) {
            versionHtml += '<div style="margin-top:8px;font-weight:500;color:var(--text-bright)">Network Interfaces</div>';
            versionHtml += table(['Name', 'Addresses', 'DHCP', 'Routes'],
                mc.network_interfaces.map(i => [
                    i.name, i.addresses.join(', ') || '-',
                    i.dhcp ? 'Yes' : 'No',
                    i.routes.length ? i.routes.join(', ') : '-'
                ])
            );
        }
    } else {
        versionHtml += `<div style="color:var(--text-dim);padding:8px 0">${esc(mc.error || 'Config not available')}</div>`;
    }
    html += subSection('talos-config', 'Machine Config', versionHtml);

    // Certificates
    if (talos.certificates.length) {
        html += subSection('talos-certs', 'Certificates', table(
            ['Name', 'Subject', 'Issuer', 'Not After', 'Expires In', 'Fingerprint'],
            talos.certificates.map(c => [
                esc(c.name), esc(c.subject), esc(c.issuer), esc(c.not_after),
                severityBadge(c.expiry_severity, (c.days_until_expiry != null ? c.days_until_expiry + 'd' : '-')),
                `<span style="font-size:10px;color:var(--text-dim)">${esc((c.sha256_fingerprint || '').substring(0, 24))}...</span>`
            ])
        ));
    }

    html += '</div></details>';
    el.innerHTML = html;
}

// ── Containers ──
function renderContainers(ct) {
    const el = document.getElementById('containers-section');
    if (!ct) { el.innerHTML = ''; return; }
    let html = '<details data-id="containers"><summary>Containers</summary><div class="section-content">';

    // System containers
    if (ct.system_containers.length) {
        html += subSection('ct-system', 'Talos System Services', table(
            ['Name', 'State', 'Memory', 'Image', 'Uptime', 'Logs'],
            ct.system_containers.map(c => [
                c.name,
                c.state === 'CONTAINER_RUNNING' ? severityBadge('ok', 'Running') : severityBadge('critical', c.state),
                c.stats.memory_human || '-',
                `<span style="font-size:11px;color:var(--text-dim)">${esc((c.image || '').split('/').pop())}</span>`,
                c.uptime || '-',
                c.container_id ? `<button class="log-btn" onclick="openLogViewer('${esc(c.container_id)}','${esc(c.name)}')">Logs</button>` : '-'
            ])
        ));
    }

    // Workload containers
    if (ct.workload_containers.length) {
        html += subSection('ct-workloads', 'Workload Pods', table(
            ['Namespace', 'Pod', 'Container', 'State', 'Memory', 'Uptime', 'Logs'],
            ct.workload_containers.map(c => [
                c.namespace,
                c.pod_name,
                c.container_name,
                c.state === 'CONTAINER_RUNNING' ? severityBadge('ok', 'Running') : severityBadge('critical', c.state),
                c.stats.memory_human || '-',
                c.uptime || '-',
                c.container_id ? `<button class="log-btn" onclick="openLogViewer('${esc(c.container_id)}','${esc(c.namespace + '/' + c.pod_name + '/' + c.container_name)}')">Logs</button>` : '-'
            ])
        ));
    }

    html += '</div></details>';
    el.innerHTML = html;
}

// ── Processes ──
function renderProcesses(procs) {
    const el = document.getElementById('processes-section');
    if (!procs) { el.innerHTML = ''; return; }
    let html = '<details data-id="processes"><summary>Processes (' + procs.total_count + ')</summary><div class="section-content">';

    html += table(
        ['PID', 'PPID', 'User', 'CPU%', 'MEM%', 'RSS', 'State', 'Command'],
        procs.processes.map(p => [
            p.pid, p.ppid, esc(p.user),
            p.cpu_percent.toFixed(1),
            p.mem_percent.toFixed(1),
            humanBytes(p.rss_kb * 1024),
            p.state,
            `<span style="font-size:11px;max-width:400px;display:inline-block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.command)}</span>`
        ])
    );

    html += '</div></details>';
    el.innerHTML = html;
}

// ── WebSocket Log Viewer ──
let logSocket = null;

function openLogViewer(containerId, containerName) {
    const modal = document.getElementById('log-modal');
    const content = document.getElementById('log-modal-content');
    const title = document.getElementById('log-modal-title');

    modal.classList.remove('hidden');
    title.textContent = 'Logs: ' + containerName;
    content.textContent = 'Connecting...\n';

    if (logSocket) { logSocket.close(); logSocket = null; }

    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    logSocket = new WebSocket(wsProtocol + '//' + location.host + '/api/containers/' + containerId + '/logs');

    logSocket.onopen = () => {
        content.textContent = '';
    };

    logSocket.onmessage = (event) => {
        content.textContent += event.data;
        // Auto-scroll if near bottom
        if (content.scrollHeight - content.scrollTop - content.clientHeight < 100) {
            content.scrollTop = content.scrollHeight;
        }
    };

    logSocket.onerror = () => {
        content.textContent += '\n[WebSocket error]\n';
    };

    logSocket.onclose = () => {
        content.textContent += '\n[Connection closed]\n';
    };
}

function closeLogViewer() {
    document.getElementById('log-modal').classList.add('hidden');
    if (logSocket) { logSocket.close(); logSocket = null; }
}

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refresh-btn').addEventListener('click', fetchOverview);
    document.getElementById('log-modal-close')?.addEventListener('click', closeLogViewer);
    document.getElementById('theme-toggle')?.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-theme]');
        if (btn) applyTheme(btn.dataset.theme);
    });

    // Cluster dropdown toggle
    document.getElementById('cluster-toggle')?.addEventListener('click', (e) => {
        e.stopPropagation();
        const dd = document.getElementById('cluster-dropdown');
        dd.classList.toggle('hidden');
        if (!dd.classList.contains('hidden')) {
            document.getElementById('cluster-search').value = '';
            document.getElementById('cluster-search').focus();
            renderClusterList('');
        }
    });

    // Cluster search
    document.getElementById('cluster-search')?.addEventListener('input', (e) => {
        renderClusterList(e.target.value.toLowerCase());
    });

    // Close dropdown on outside click
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.cluster-toggle-wrap')) {
            document.getElementById('cluster-dropdown')?.classList.add('hidden');
        }
    });

    fetchOverview();
});
