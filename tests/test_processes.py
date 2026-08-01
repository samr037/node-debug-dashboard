"""CPU accounting in the process collector."""

from app.collectors.processes import _CLK_TCK, _cpu_percent


def test_first_sighting_uses_process_age_not_system_uptime():
    """A process started late in a long uptime must not read as idle.

    The original formula divided CPU time by *system* uptime, so a process
    that burned a full core for its entire 10s life showed as 0.03% on a
    node up for 100 days.
    """
    uptime = 100 * 86400.0  # node up 100 days
    age = 10.0
    starttime_ticks = int((uptime - age) * _CLK_TCK)
    cpu_ticks = int(age * _CLK_TCK)  # one core, fully busy, for its whole life

    pct = _cpu_percent(
        pid=1234,
        starttime=starttime_ticks,
        cpu_ticks=cpu_ticks,
        uptime_secs=uptime,
        now=0.0,
        prev_samples={},
    )
    assert pct == 100.0


def test_delta_sampling_reports_current_usage():
    """With a previous sample, usage is measured over the interval."""
    starttime = 5000
    prev = {(1234, starttime): (1000, 100.0)}
    # Half a core's worth of ticks over a 10s window.
    cpu_ticks = 1000 + int(5 * _CLK_TCK)

    pct = _cpu_percent(
        pid=1234,
        starttime=starttime,
        cpu_ticks=cpu_ticks,
        uptime_secs=999999.0,
        now=110.0,
        prev_samples=prev,
    )
    assert pct == 50.0


def test_recycled_pid_does_not_reuse_stale_sample():
    """A new process reusing a PID is keyed separately, so no bogus delta."""
    old_start, new_start = 5000, 900000
    prev = {(1234, old_start): (10_000_000, 100.0)}

    pct = _cpu_percent(
        pid=1234,
        starttime=new_start,
        cpu_ticks=0,
        uptime_secs=(new_start / _CLK_TCK) + 10.0,
        now=110.0,
        prev_samples=prev,
    )
    # Falls back to lifetime average for the new process: no CPU used yet.
    assert pct == 0.0


def test_counter_going_backwards_falls_back():
    """A decreasing tick counter is impossible; don't emit a negative percent."""
    starttime = 5000
    prev = {(1234, starttime): (5000, 100.0)}

    pct = _cpu_percent(
        pid=1234,
        starttime=starttime,
        cpu_ticks=1000,
        uptime_secs=(starttime / _CLK_TCK) + 100.0,
        now=110.0,
        prev_samples=prev,
    )
    assert pct >= 0.0


def test_zero_elapsed_does_not_divide_by_zero():
    starttime = 5000
    prev = {(1234, starttime): (1000, 100.0)}
    pct = _cpu_percent(
        pid=1234,
        starttime=starttime,
        cpu_ticks=2000,
        uptime_secs=(starttime / _CLK_TCK) + 50.0,
        now=100.0,  # same instant as the previous sample
        prev_samples=prev,
    )
    assert pct >= 0.0


def test_real_stat_fixture_exposes_starttime(fixture_text):
    """Field 22 of /proc/<pid>/stat is where starttime lives."""
    stat_raw = fixture_text("proc-pid-stat.txt")
    after_comm = stat_raw[stat_raw.rfind(")") + 2 :].split()
    assert len(after_comm) >= 20
    starttime = int(after_comm[19])
    assert starttime >= 0
