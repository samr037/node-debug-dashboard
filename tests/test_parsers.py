"""Baseline coverage for the text/JSON parsers behind the collectors.

Fixtures under tests/fixtures/ are real output captured from a Talos node,
so these tests pin the formats the parsers actually meet in production.
"""

import json

from app.collectors.memory import _dimm_from_dict


def test_lsblk_fixture_shape(fixture_text):
    """lsblk -J output parses and exposes the fields collect_disks() reads."""
    data = json.loads(fixture_text("lsblk.json"))
    disks = [d for d in data["blockdevices"] if d.get("type") == "disk"]
    assert disks, "fixture should contain at least one disk"
    for d in disks:
        assert "name" in d
        assert "size" in d


def test_smartctl_ata_fixture_shape(fixture_text):
    """smartctl --json output exposes the keys collect_smart_for_device() reads."""
    data = json.loads(fixture_text("smartctl-ata.json"))
    assert "smart_status" in data
    assert isinstance(data["smart_status"].get("passed"), bool)
    table = data.get("ata_smart_attributes", {}).get("table", [])
    assert table, "ATA fixture should carry a SMART attribute table"
    assert {"id", "name", "value", "worst", "thresh"} <= set(table[0])


def test_dimm_parsing_from_dmidecode(fixture_text):
    """dmidecode -t 17 blocks convert into DimmInfo with sizes normalised to MB."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in fixture_text("dmidecode-t17.txt").splitlines():
        stripped = line.strip()
        if stripped == "Memory Device":
            if current:
                blocks.append(current)
            current = {}
        elif ":" in stripped:
            key, val = stripped.split(":", 1)
            current[key.strip().lower()] = val.strip()
    if current:
        blocks.append(current)

    populated = [
        b
        for b in blocks
        if b.get("size", "").lower() not in ("", "no module installed", "unknown")
    ]
    assert populated, "fixture should contain at least one populated DIMM"

    for block in populated:
        dimm = _dimm_from_dict(block)
        assert dimm.locator
        assert dimm.size_mb and dimm.size_mb > 0


def test_dimm_size_units_normalise():
    """GB sizes convert to MB; MB sizes pass through."""
    assert _dimm_from_dict({"size": "8 GB", "locator": "DIMM0"}).size_mb == 8192
    assert _dimm_from_dict({"size": "512 MB", "locator": "DIMM1"}).size_mb == 512


def test_dimm_placeholder_serials_become_none():
    """dmidecode placeholders shouldn't surface as real serial numbers."""
    for placeholder in ("Unknown", "Not Specified", ""):
        dimm = _dimm_from_dict({"size": "8 GB", "serial number": placeholder})
        assert dimm.serial is None
