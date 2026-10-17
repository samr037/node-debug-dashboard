import glob
import os
import re

from app.collectors.base import read_file, ttl_cache
from app.config import HOST_SYS
from app.models.hardware import SensorReading


def _index(path: str, prefix: str) -> str:
    """'in0_input' -> '0'. str.replace would also eat the 'in' of '_input'."""
    m = re.match(rf"{prefix}(\d+)_input$", os.path.basename(path))
    return m.group(1) if m else ""


def _millis(val: str) -> float | None:
    """Parse a sysfs milli-unit threshold, None when absent or not an integer."""
    try:
        return int(val) / 1000.0 if val else None
    except ValueError:
        return None


@ttl_cache()
async def collect_sensors() -> list[SensorReading]:
    readings: list[SensorReading] = []

    # hwmon sensors
    for hwmon_dir in sorted(glob.glob(f"{HOST_SYS}/class/hwmon/hwmon*")):
        chip_name = (await read_file(f"{hwmon_dir}/name")).strip() or os.path.basename(
            hwmon_dir
        )

        # Temperature sensors: temp*_input
        for temp_file in sorted(glob.glob(f"{hwmon_dir}/temp*_input")):
            idx = _index(temp_file, "temp")
            val_str = (await read_file(temp_file)).strip()
            if not val_str:
                continue
            try:
                value = int(val_str) / 1000.0
            except ValueError:
                continue

            label = (
                await read_file(f"{hwmon_dir}/temp{idx}_label")
            ).strip() or f"temp{idx}"
            crit_str = (await read_file(f"{hwmon_dir}/temp{idx}_crit")).strip()
            warn_str = (await read_file(f"{hwmon_dir}/temp{idx}_max")).strip()

            critical = _millis(crit_str)
            warning = _millis(warn_str)
            is_alarm = False
            if critical and value >= critical:
                is_alarm = True
            elif warning and value >= warning:
                is_alarm = True

            readings.append(
                SensorReading(
                    name=chip_name,
                    label=label,
                    value=value,
                    unit="°C",
                    critical=critical,
                    warning=warning,
                    is_alarm=is_alarm,
                )
            )

        # Fan sensors: fan*_input
        for fan_file in sorted(glob.glob(f"{hwmon_dir}/fan*_input")):
            idx = _index(fan_file, "fan")
            val_str = (await read_file(fan_file)).strip()
            if not val_str:
                continue
            try:
                value = float(val_str)
            except ValueError:
                continue

            label = (
                await read_file(f"{hwmon_dir}/fan{idx}_label")
            ).strip() or f"fan{idx}"
            readings.append(
                SensorReading(
                    name=chip_name,
                    label=label,
                    value=value,
                    unit="RPM",
                )
            )

        # Voltage sensors: in*_input
        for in_file in sorted(glob.glob(f"{hwmon_dir}/in*_input")):
            idx = _index(in_file, "in")
            val_str = (await read_file(in_file)).strip()
            if not val_str:
                continue
            try:
                value = int(val_str) / 1000.0  # millivolts to volts
            except ValueError:
                continue

            label = (
                await read_file(f"{hwmon_dir}/in{idx}_label")
            ).strip() or f"in{idx}"
            readings.append(
                SensorReading(
                    name=chip_name,
                    label=label,
                    value=round(value, 3),
                    unit="V",
                )
            )

    # Thermal zones (fallback)
    for tz_dir in sorted(glob.glob(f"{HOST_SYS}/class/thermal/thermal_zone*")):
        temp_str = (await read_file(f"{tz_dir}/temp")).strip()
        if not temp_str:
            continue
        try:
            value = int(temp_str) / 1000.0
        except ValueError:
            continue

        tz_type = (await read_file(f"{tz_dir}/type")).strip() or os.path.basename(
            tz_dir
        )
        # Skip if we already have this from hwmon: zones bridged to hwmon show
        # up there with name == zone type and a generic 'temp1' label
        if any(tz_type in (r.name, r.label) and r.unit == "°C" for r in readings):
            continue

        readings.append(
            SensorReading(
                name="thermal_zone",
                label=tz_type,
                value=value,
                unit="°C",
            )
        )

    return readings
