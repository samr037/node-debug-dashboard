import glob
import os

from app.collectors.base import read_file, ttl_cache
from app.models.hardware import SensorReading


@ttl_cache()
async def collect_sensors() -> list[SensorReading]:
    readings: list[SensorReading] = []

    # hwmon sensors
    for hwmon_dir in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        chip_name = (await read_file(f"{hwmon_dir}/name")).strip() or os.path.basename(
            hwmon_dir
        )

        # Temperature sensors: temp*_input
        for temp_file in sorted(glob.glob(f"{hwmon_dir}/temp*_input")):
            idx = os.path.basename(temp_file).replace("temp", "").replace("_input", "")
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

            critical = int(crit_str) / 1000.0 if crit_str else None
            warning = int(warn_str) / 1000.0 if warn_str else None
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
            idx = os.path.basename(fan_file).replace("fan", "").replace("_input", "")
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
            idx = os.path.basename(in_file).replace("in", "").replace("_input", "")
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
    for tz_dir in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
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
        # Skip if we already have this from hwmon
        if any(r.label == tz_type and r.unit == "°C" for r in readings):
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
