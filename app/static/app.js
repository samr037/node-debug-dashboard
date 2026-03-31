const REFRESH_INTERVAL = 10000;
let timer = null;
let countdown = 10;
let lastData = null;

async function fetchOverview() {
    try {
        const resp = await fetch('/api/overview');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        lastData = await resp.json();
        render(lastData);
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

function render(data) {
    renderNodeBar(data.node);
    renderWarnings(data.warnings);
    renderHardware(data.hardware);
    renderStorage(data.storage);
    renderSystem(data.system);
    renderNetwork(data.network);
    document.getElementById('header-node').textContent = data.node.hostname;
}

// ── Node Info Bar ──
function renderNodeBar(node) {
    const el = document.getElementById('node-bar');
    el.innerHTML = `
        <div class="item"><span class="label">OS:</span><span class="value">${esc(node.os_name)}</span></div>
        ${node.talos_version ? `<div class="item"><span class="label">Talos:</span><span class="value">${esc(node.talos_version)}</span></div>` : ''}
        <div class="item"><span class="label">Kernel:</span><span class="value">${esc(node.kernel_version)}</span></div>
        <div class="item"><span class="label">Up:</span><span class="value">${esc(node.uptime_human)}</span></div>
        <div class="item"><span class="label">Load:</span><span class="value">${node.load_1m} ${node.load_5m} ${node.load_15m}</span></div>
        <div class="item"><span class="label">CPU:</span><span class="value">${esc(node.cpu_model)} (${node.cpu_cores}c/${node.cpu_threads}t)</span></div>
        <div class="item"><span class="label">RAM:</span><span class="value">${node.ram_total_gb} GB</span></div>
        ${node.ip_addresses.filter(ip => ip.family === 'inet').map(ip =>
            `<div class="item"><span class="label">${esc(ip.interface)}:</span><span class="value">${esc(ip.address)}/${ip.prefix_length}</span></div>`
        ).join('')}
    `;
}

// ── Warnings ──
function renderWarnings(warnings) {
    const el = document.getElementById('warnings-section');
    const crits = warnings.filter(w => w.severity === 'critical');
    const warns = warnings.filter(w => w.severity === 'warning');

    el.className = 'warnings-banner ' + (crits.length ? 'has-critical' : warns.length ? 'has-warning' : 'all-ok');

    let html = '<details open><summary>';
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
    let html = '<details><summary>Hardware</summary><div class="section-content">';

    // CPU
    html += subSection('CPU', `
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
    html += subSection('Memory (DIMMs)', memHtml);

    // PCI
    if (hw.pci_devices.length) {
        html += subSection('PCI Devices', table(
            ['Slot', 'Class', 'Vendor', 'Device', 'Driver'],
            hw.pci_devices.map(d => [d.slot, d.class_name, d.vendor, d.device, d.driver || '-'])
        ));
    }

    // USB
    if (hw.usb_devices.length) {
        html += subSection('USB Devices', table(
            ['Bus', 'Dev', 'ID', 'Name'],
            hw.usb_devices.map(d => [d.bus, d.device, d.id, d.name])
        ));
    }

    // NICs
    if (hw.nics.length) {
        html += subSection('Network Interfaces', table(
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
        html += subSection('Sensors', sensorsHtml);
    }

    // GPUs
    if (hw.gpus.length) {
        html += subSection('GPU', table(
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
    let html = '<details><summary>Storage</summary><div class="section-content">';

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
        html += subSection('Disks & Partitions', disksHtml);
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
                smartHtml += `<details class="sub-section" style="margin-top:4px"><summary>SMART Attributes (${s.attributes.length})</summary><div class="section-content">`;
                smartHtml += table(
                    ['ID', 'Name', 'Value', 'Worst', 'Thresh', 'Raw'],
                    s.attributes.map(a => [a.id, a.name, a.value, a.worst, a.threshold, a.raw_value])
                );
                smartHtml += '</div></details>';
            }
        }
        html += subSection('SMART Health', smartHtml);
    }

    // Usage
    if (storage.usage.length) {
        html += subSection('Disk Usage', table(
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
    let html = '<details><summary>System</summary><div class="section-content">';

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
        html += subSection('UEFI Boot Order', efiHtml);
    } else {
        html += '<div style="color:var(--text-dim);padding:8px 0">EFI boot manager not available (legacy BIOS or not accessible)</div>';
    }

    html += '</div></details>';
    el.innerHTML = html;
}

// ── Network ──
function renderNetwork(net) {
    const el = document.getElementById('network-section');
    let html = '<details><summary>Network</summary><div class="section-content">';

    const conn = net.connectivity;
    let connHtml = `<div class="kv-grid">
        <span class="kv-key">Default Gateway</span><span class="kv-val">${esc(conn.default_gateway || 'none')}</span>
        <span class="kv-key">DNS Servers</span><span class="kv-val">${conn.dns_servers.join(', ') || '-'}</span>
        <span class="kv-key">DNS Resolution</span><span class="kv-val ${conn.dns_ok ? 'sev-ok' : 'sev-critical'}">${conn.dns_ok ? 'OK' : 'FAILED'}${conn.dns_result ? ' (' + esc(conn.dns_result) + ')' : ''}</span>
        <span class="kv-key">Internet</span><span class="kv-val ${conn.internet_ok ? 'sev-ok' : 'sev-critical'}">${conn.internet_ok ? 'Reachable' : 'Unreachable'}</span>
        <span class="kv-key">K8s API</span><span class="kv-val ${conn.kubernetes_api_ok ? 'sev-ok' : 'sev-critical'}">${conn.kubernetes_api_ok ? 'Reachable' : 'Unreachable'}</span>
    </div>`;
    html += subSection('Connectivity', connHtml);

    if (net.interfaces.length) {
        html += subSection('Interfaces', table(
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

function subSection(title, content) {
    return `<details class="sub-section"><summary>${esc(title)}</summary><div class="section-content">${content}</div></details>`;
}

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refresh-btn').addEventListener('click', fetchOverview);
    fetchOverview();
});
