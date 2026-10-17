"""Regression tests for the hardware collectors (CPU, GPU, memory, node, PCI,
sensors, USB): unexpected command output or sysfs content must degrade to
missing values, never crash a collector or drop a device."""

import json

import pytest

from app.collectors import cpu, gpu, node, pci, sensors, usb
from app.collectors.memory import _dimm_from_dict

ARM_LSCPU = """Architecture:            aarch64
  CPU op-mode(s):        32-bit, 64-bit
CPU(s):                  4
  On-line CPU(s) list:   0-3
Vendor ID:               ARM
  Model name:            Cortex-A72
    Model:               3
    Thread(s) per core:  1
    Core(s) per cluster: 4
    Socket(s):           -
    Cluster(s):          1
"""

# RK3588: one block per core type, cores counted per cluster
BIG_LITTLE_LSCPU = """Architecture:            aarch64
CPU(s):                  8
Vendor ID:               ARM
  Model name:            Cortex-A55
    Core(s) per cluster: 4
    Socket(s):           -
    Cluster(s):          1
  Model name:            Cortex-A76
    Core(s) per cluster: 2
    Socket(s):           -
    Cluster(s):          2
"""

X86_LSCPU = """Architecture:            x86_64
CPU(s):                  56
  On-line CPU(s) list:   0-55
Vendor ID:               GenuineIntel
  Model name:            Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz
    BIOS Model name:     pc-q35-8.1  CPU @ 2.0GHz
    Thread(s) per core:  2
    Core(s) per socket:  14
    Socket(s):           2
NUMA:
  NUMA node(s):          2
  NUMA node0 CPU(s):     0-13,28-41
"""


def _fake_run(outputs: dict[str, tuple[str, str, int]]):
    async def run(cmd, timeout=None):
        return outputs.get(cmd[0], ("", "", 1))

    return run


def _fake_read(files: dict[str, str]):
    async def read(path):
        return files.get(path, "")

    return read


# --- lscpu -------------------------------------------------------------------


def test_parse_lscpu_arm_dash_sockets():
    assert cpu.parse_lscpu(ARM_LSCPU) == ("Cortex-A72", 1, 4, 4)


def test_parse_lscpu_big_little_sums_clusters():
    model, sockets, cores_per, threads = cpu.parse_lscpu(BIG_LITTLE_LSCPU)
    assert model == "Cortex-A55 + Cortex-A76"
    assert (sockets, cores_per, threads) == (1, 8, 8)


def test_parse_lscpu_x86_ignores_bios_model_name():
    model, sockets, cores_per, threads = cpu.parse_lscpu(X86_LSCPU)
    assert model == "Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz"
    assert (sockets, cores_per, threads) == (2, 14, 56)


def test_parse_lscpu_empty_output():
    assert cpu.parse_lscpu("") == ("", 1, 1, 1)


async def test_collect_cpu_arm_does_not_crash_and_reads_features(
    monkeypatch, clear_caches
):
    monkeypatch.setattr(cpu, "run_command", _fake_run({"lscpu": (ARM_LSCPU, "", 0)}))
    monkeypatch.setattr(
        cpu,
        "read_file",
        _fake_read(
            {
                f"{cpu.HOST_PROC}/cpuinfo": "processor\t: 0\n"
                "BogoMIPS\t: 108.00\n"
                "Features\t: fp asimd evtstrm crc32 cpuid\n"
            }
        ),
    )
    info = await cpu.collect_cpu()
    assert info.model == "Cortex-A72"
    assert (info.sockets, info.cores_per_socket, info.threads) == (1, 4, 4)
    assert info.architecture == "aarch64"
    assert info.flags == ["fp", "asimd", "evtstrm", "crc32", "cpuid"]


async def test_collect_node_info_arm_and_bios_model(monkeypatch, clear_caches):
    for out, expected in (
        (ARM_LSCPU, ("Cortex-A72", 4, 4)),
        (X86_LSCPU, ("Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz", 28, 56)),
    ):
        clear_caches()
        monkeypatch.setattr(
            node,
            "run_command",
            _fake_run({"lscpu": (out, "", 0), "uname": ("6.12\n", "", 0)}),
        )
        monkeypatch.setattr(node, "read_file", _fake_read({}))
        info = await node.collect_node_info()
        assert (info.cpu_model, info.cpu_cores, info.cpu_threads) == expected


# --- node IP addresses -------------------------------------------------------


async def test_node_ips_skip_overlay_and_ipvs(monkeypatch):
    def iface(name, *addrs):
        return {
            "ifname": name,
            "addr_info": [
                {"family": "inet", "local": a, "prefixlen": 32} for a in addrs
            ],
        }

    data = [
        iface("lo", "127.0.0.1"),
        iface("eth0", "192.168.1.10"),
        iface("kube-ipvs0", *[f"10.96.0.{i}" for i in range(150)]),
        iface("cilium_host", "10.0.0.1"),
        iface("cilium_vxlan"),
        iface("tunl0", "10.244.0.1"),
        iface("vxlan.calico", "10.244.1.0"),
        iface("flannel-wg", "10.244.2.0"),
        iface("genev_sys_6081"),
        iface("nodelocaldns", "169.254.20.10"),
        iface("bond0", "10.0.10.5"),
    ]
    monkeypatch.setattr(
        node, "run_command", _fake_run({"ip": (json.dumps(data), "", 0)})
    )
    ips = await node._collect_ip_addresses()
    assert [(i.interface, i.address) for i in ips] == [
        ("eth0", "192.168.1.10"),
        ("bond0", "10.0.10.5"),
    ]


# --- GPU ---------------------------------------------------------------------


async def test_gpu_sentinels_keep_the_gpu(monkeypatch, clear_caches):
    out = (
        "0, NVIDIA GeForce RTX 3090, 550.54, 24576, 100, 45, 3, [Not Supported]\n"
        "1, Tesla T4, 550.54, 15360, 0, [N/A], [Unknown Error], 27.5\n"
        "2, Tesla T4, 550.54, [GPU is lost], [GPU is lost], [GPU is lost], "
        "[GPU is lost], [GPU is lost]\n"
    )
    monkeypatch.setattr(gpu, "run_command", _fake_run({"nsenter": (out, "", 0)}))
    gpus = await gpu.collect_gpus()
    assert [g.index for g in gpus] == [0, 1, 2]
    assert gpus[0].power_draw_w is None
    assert (gpus[0].temperature_c, gpus[0].utilization_percent) == (45, 3)
    assert gpus[0].memory_total_mb == 24576
    assert gpus[1].temperature_c is None
    assert gpus[1].utilization_percent is None
    assert gpus[1].power_draw_w == 27.5
    assert gpus[2].memory_total_mb == 0
    assert gpus[2].temperature_c is None


# --- memory ------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"configured memory speed": "Unknown", "speed": "3200 MT/s"}, 3200),
        ({"configured memory speed": "2933 MT/s", "speed": "3200 MT/s"}, 2933),
        ({"configured clock speed": "2400 MHz", "speed": "Unknown"}, 2400),
        ({"configured memory speed": "Unknown", "speed": "Unknown"}, None),
    ],
)
def test_dimm_speed_falls_back_past_unknown(fields, expected):
    dimm = _dimm_from_dict({"size": "32 GB", "locator": "DIMM_A1", **fields})
    assert dimm.speed_mhz == expected


def test_dimm_size_in_tb():
    assert _dimm_from_dict({"size": "1 TB"}).size_mb == 1024 * 1024
    assert _dimm_from_dict({"size": "512 MB"}).size_mb == 512


# --- PCI ---------------------------------------------------------------------


async def test_pci_requests_kernel_driver(monkeypatch, clear_caches):
    seen: list[list[str]] = []
    out = (
        "Slot:\t01:00.0\nClass:\tNon-Volatile memory controller\n"
        "Vendor:\tSamsung\nDevice:\tNVMe SSD\nDriver:\tnvme\nModule:\tnvme\n\n"
    )

    async def run(cmd, timeout=None):
        seen.append(cmd)
        # Real lspci only prints Driver: when -k is passed
        return (out if "-k" in cmd else out.replace("Driver:\tnvme\n", "")), "", 0

    monkeypatch.setattr(pci, "run_command", run)
    devices = await pci.collect_pci()
    assert "-k" in seen[0]
    assert devices[0].driver == "nvme"


# --- USB ---------------------------------------------------------------------


async def test_usb_keeps_devices_without_names(monkeypatch, clear_caches):
    out = (
        "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
        "Bus 002 Device 003: ID 1234:5678 \n"
        "Bus 002 Device 004: ID abcd:ef01\n"
    )
    monkeypatch.setattr(usb, "run_command", _fake_run({"lsusb": (out, "", 0)}))
    devices = await usb.collect_usb()
    assert [(d.id, d.name) for d in devices] == [
        ("1d6b:0002", "Linux Foundation 2.0 root hub"),
        ("1234:5678", ""),
        ("abcd:ef01", ""),
    ]


# --- sensors -----------------------------------------------------------------


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


async def test_sensors_voltage_label_and_bad_threshold(
    tmp_path, monkeypatch, clear_caches
):
    hw = tmp_path / "class/hwmon/hwmon0"
    _write(hw / "name", "nct6798\n")
    _write(hw / "in0_input", "1200\n")
    _write(hw / "in0_label", "Vcore\n")
    _write(hw / "in1_input", "12000\n")
    _write(hw / "temp1_input", "45000\n")
    _write(hw / "temp1_label", "CPUTIN\n")
    _write(hw / "temp1_max", "N/A\n")
    _write(hw / "temp1_crit", "100000\n")
    _write(hw / "fan2_input", "900\n")
    _write(hw / "fan2_label", "CPU fan\n")
    monkeypatch.setattr(sensors, "HOST_SYS", str(tmp_path))

    readings = {(r.unit, r.label): r for r in await sensors.collect_sensors()}
    assert readings[("V", "Vcore")].value == 1.2
    assert ("V", "in1") in readings
    temp = readings[("°C", "CPUTIN")]
    assert temp.warning is None
    assert temp.critical == 100.0
    assert ("RPM", "CPU fan") in readings


async def test_thermal_zone_bridged_to_hwmon_not_duplicated(
    tmp_path, monkeypatch, clear_caches
):
    _write(tmp_path / "class/hwmon/hwmon0/name", "acpitz\n")
    _write(tmp_path / "class/hwmon/hwmon0/temp1_input", "45000\n")
    _write(tmp_path / "class/thermal/thermal_zone0/type", "acpitz\n")
    _write(tmp_path / "class/thermal/thermal_zone0/temp", "45000\n")
    # A zone without an hwmon twin still comes through the fallback
    _write(tmp_path / "class/thermal/thermal_zone1/type", "x86_pkg_temp\n")
    _write(tmp_path / "class/thermal/thermal_zone1/temp", "52000\n")
    monkeypatch.setattr(sensors, "HOST_SYS", str(tmp_path))

    readings = await sensors.collect_sensors()
    assert [(r.name, r.label, r.value) for r in readings] == [
        ("acpitz", "temp1", 45.0),
        ("thermal_zone", "x86_pkg_temp", 52.0),
    ]


def test_gpu_num_rejects_non_finite():
    # int(float("inf")) raises OverflowError; nan breaks the JSON response
    assert gpu._num("inf", int) is None
    assert gpu._num("nan") is None
    assert gpu._num("ERR!") is None
    assert gpu._num("72.40") == 72.4
