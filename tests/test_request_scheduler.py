from pathlib import Path

from backend.audit_agent.request_scheduler import RequestScheduler


class VirtualClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_scheduler_persists_spacing_across_instances(tmp_path: Path):
    clock = VirtualClock()
    db = tmp_path / "audit.sqlite3"
    first = RequestScheduler(db, platform="dy", min_interval=2, per_minute=30, clock=lambda: clock.now, sleeper=clock.sleep)
    assert first.acquire("search") == 0
    second = RequestScheduler(db, platform="dy", min_interval=2, per_minute=30, clock=lambda: clock.now, sleeper=clock.sleep)
    assert second.acquire("detail") == 2
    assert clock.sleeps == [2]


def test_scheduler_enforces_rolling_window_and_cooldown(tmp_path: Path):
    clock = VirtualClock()
    scheduler = RequestScheduler(tmp_path / "audit.sqlite3", platform="dy", min_interval=0, per_minute=2, cooldown_seconds=10, clock=lambda: clock.now, sleeper=clock.sleep)
    scheduler.acquire()
    scheduler.acquire()
    scheduler.acquire()
    assert clock.sleeps == [60]
    scheduler.enter_cooldown("verify")
    scheduler.acquire()
    assert clock.sleeps[-1] == 10
    assert scheduler.snapshot()["last_reason"] == "verify"
