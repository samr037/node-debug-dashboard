"""dmesg fault detection.

The regression these lock down: the original patterns matched subsystem
names, so every one of these lines from a healthy talos-3-optiplex7010
produced an alert.
"""

from datetime import datetime, timedelta, timezone

from app.collectors.dmesg import parse_line, scan

# Verbatim from tests/fixtures/dmesg-iso.txt, captured off a healthy node.
# The old rules turned these into "EDAC/ECC memory errors (2 occurrences),
# critical" and "CPU thermal throttling detected (8 occurrences)".
BENIGN_BOOT_LINES = [
    "CPU0: Thermal monitoring enabled (TM1)",
    "thermal_sys: Registered thermal governor 'step_wise'",
    "thermal_sys: Registered thermal governor 'user_space'",
    "EDAC MC: Ver: 3.0.0",
    "thermal LNXTHERM:00: registered as thermal_zone0",
    "ACPI: thermal: Thermal Zone [TZ00] (28 C)",
    "EDAC ie31200: No ECC support",
    "e1000e 0000:00:19.0: Interrupt Throttling Rate (ints/sec) set to dynamic conservative mode",
    "mce: CPU supports 7 MCE banks",
]


def test_healthy_boot_produces_no_warnings(fixture_text):
    """The recorded fixture is from a node with no faults at all."""
    assert scan(fixture_text("dmesg-iso.txt").splitlines(), cutoff=None) == []


def test_each_benign_line_individually_produces_nothing():
    """Pinned line by line so a future pattern widening is caught here."""
    for line in BENIGN_BOOT_LINES:
        assert scan([line], cutoff=None) == [], f"false positive on: {line}"


def test_no_ecc_support_is_not_an_ecc_error():
    """A node stating it has no ECC hardware cannot have ECC errors."""
    assert scan(["EDAC ie31200: No ECC support"], cutoff=None) == []


def test_nic_interrupt_throttling_is_not_cpu_throttling():
    """e1000e rate limiting has nothing to do with the CPU."""
    line = "e1000e 0000:00:19.0: Interrupt Throttling Rate (ints/sec) set to dynamic conservative mode"
    assert scan([line], cutoff=None) == []


def test_real_correctable_ecc_error_is_caught():
    line = "EDAC MC0: 1 CE memory read error on CPU_SrcID#0_MC#0_Chan#1_DIMM#0"
    (warning,) = scan([line], cutoff=None)
    assert warning.severity == "warning"
    assert warning.source == "memory"
    assert "1 in the last" in warning.message


def test_real_uncorrectable_ecc_error_is_critical():
    line = "EDAC MC0: 1 UE memory read error on CPU_SrcID#0_MC#0_Chan#0_DIMM#1"
    (warning,) = scan([line], cutoff=None)
    assert warning.severity == "critical"


def test_zero_count_edac_report_is_ignored():
    assert scan(["EDAC MC0: 0 CE memory read error"], cutoff=None) == []


def test_real_thermal_throttle_is_caught():
    line = "CPU2: Core temperature above threshold, cpu clock throttled (total events = 41)"
    (warning,) = scan([line], cutoff=None)
    assert warning.severity == "warning"
    assert warning.source == "cpu"


def test_real_disk_io_error_is_caught():
    line = "blk_update_request: I/O error, dev sda, sector 1234567 op 0x0:(READ)"
    (warning,) = scan([line], cutoff=None)
    assert warning.severity == "critical"
    assert warning.source == "disk"


def test_filesystem_and_journal_io_errors_are_caught():
    """Verbatim from talos-4-inspiron7610 during a real storage fault.

    These are the serious ones — the filesystem could not write its
    superblock — and they carry no "dev" token, so a pattern anchored on
    that missed them.
    """
    for line in (
        "I/O error, dev sda, sector 8715240 op 0x1:(WRITE) flags 0x9800 phys_seg 2",
        "Buffer I/O error on dev sda, logical block 0, lost sync page write",
        "JBD2: I/O error when updating journal superblock for sda-8.",
        "EXT4-fs (sda): I/O error while writing superblock",
    ):
        warnings = scan([line], cutoff=None)
        assert len(warnings) == 1, f"missed a real disk fault: {line}"
        assert warnings[0].severity == "critical"
        assert warnings[0].source == "disk"


def test_soft_lockup_is_critical():
    line = "watchdog: BUG: soft lockup - CPU#3 stuck for 23s! [kworker/3:1:412]"
    (warning,) = scan([line], cutoff=None)
    assert warning.severity == "critical"
    assert warning.source == "cpu"


def test_warning_includes_the_matching_line():
    """A count with no context cannot be triaged."""
    line = "blk_update_request: I/O error, dev sdb, sector 999 op 0x0:(READ)"
    (warning,) = scan([line], cutoff=None)
    assert "dev sdb" in warning.message


def test_events_outside_the_window_are_dropped():
    """A fault at boot should stop alerting, not persist until reboot."""
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=30)).isoformat()
    line = f"{old} blk_update_request: I/O error, dev sda, sector 1"

    assert scan([line], cutoff=now - timedelta(hours=24)) == []
    assert len(scan([line], cutoff=now - timedelta(days=90))) == 1


def test_recent_events_are_kept():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=5)).isoformat()
    line = f"{recent} blk_update_request: I/O error, dev sda, sector 1"
    assert len(scan([line], cutoff=now - timedelta(hours=24))) == 1


def test_monotonic_stamps_convert_via_boot_time():
    """Plain dmesg emits seconds-since-boot; those still need ageing out."""
    now = datetime.now(timezone.utc)
    boot = now - timedelta(days=10)
    stamp, message = parse_line("[   12.345678] some message", boot)
    assert stamp is not None
    assert abs((stamp - boot).total_seconds() - 12.345678) < 0.001
    assert message == "some message"


def test_iso_stamps_parse(fixture_text):
    first = fixture_text("dmesg-iso.txt").splitlines()[0]
    stamp, message = parse_line(first, None)
    assert stamp is not None and stamp.year == 2026
    assert "Thermal monitoring enabled" in message


def test_unstamped_lines_are_kept_not_dropped():
    """Better to over-report than silently discard an unparseable line."""
    now = datetime.now(timezone.utc)
    line = "blk_update_request: I/O error, dev sda, sector 1"
    assert len(scan([line], cutoff=now - timedelta(hours=24))) == 1


def test_each_line_counts_once():
    """A line matching two rules must not inflate both counts."""
    line = "EDAC MC0: 1 CE memory read error, uncorrected error"
    warnings = scan([line], cutoff=None)
    assert len(warnings) == 1
