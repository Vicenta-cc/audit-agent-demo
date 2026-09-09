from __future__ import annotations

from concurrent.futures import Future
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from backend.api.investigation_execution import InvestigationTurnExecutor
from backend.hermes_runtime.turn_process import TurnProcessRunner
from backend.investigation.errors import ConcurrentTurnError
from backend.investigation.store import InvestigationStore


class ProbeService:
    def __init__(self, store):
        self.store = store

    def add_turn_node_observer(self, observer):
        pass

    def accept_message(self, session_id, *, client_message_id, content):
        return self.store.create_turn(session_id, client_message_id=client_message_id,
                                      user_input=content)

    def accept_resume(self, turn_id):
        return self.store.begin_resume(turn_id), False

    def execute_turn(self, turn_id):
        raise AssertionError("must execute in a child")

    def execute_resume(self, turn_id):
        raise AssertionError("must resume in a child")


class ProbeRunner(TurnProcessRunner):
    def __init__(self, scope, root, timeout_seconds=30):
        super().__init__(scope, timeout_seconds=timeout_seconds)
        self.root = root

    def _command(self, turn_id, *, resume):
        return [sys.executable, str(Path(__file__).parent / "fixtures/turn_process_probe.py"),
                self.scope, turn_id, "resume" if resume else "execute", str(self.root)]


def wait_terminal(store, turns, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = [store.get_turn(t.id) for t in turns]
        if all(t.status != "running" and
               store.list_public_turn_events(t.id)[-1]["stage"] in
               {"completed", "interrupted", "failed"} for t in current):
            return current
        time.sleep(0.05)
    pytest.fail("durable Turn was left running or without a terminal event")


def test_six_sessions_overlap_real_catalog_and_keep_multiturn_history(tmp_path):
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    service = ProbeService(store)
    executors = {scope: InvestigationTurnExecutor(
        service, max_workers=4, process_runner=ProbeRunner(scope, tmp_path),
    ) for scope in ("creation", "report")}
    sessions = [store.create_creation_session(principal="test", anchor_key=str(i))
                for i in range(6)]
    try:
        pids = set()
        for round_no in range(3):
            turns = []
            for i, session in enumerate(sessions):
                executor = executors["creation" if i < 2 else "report"]
                content = json.dumps({"group": f"round-{round_no}", "count": 6})
                turn = executor.accept_turn(session.id, client_message_id=str(round_no),
                                             content=content)
                turns.append(turn)
                replay = executor.accept_turn(session.id, client_message_id=str(round_no),
                                               content=content)
                assert replay.id == turn.id
                with pytest.raises(ConcurrentTurnError):
                    executor.accept_turn(session.id, client_message_id="conflict",
                                          content="conflict")
            done = wait_terminal(store, turns)
            assert all(t.status == "completed" for t in done)
            for t in done:
                answer = json.loads(store.turn_result(t.id).answer)
                assert answer["session"] == t.session_id
                assert answer["previous"] == round_no
                assert answer["pid"] not in pids
                pids.add(answer["pid"])
    finally:
        for executor in executors.values():
            executor.shutdown()


@pytest.mark.parametrize("fault", ["crash", "hang"])
def test_fault_releases_capacity_other_session_survives_and_resume_works(tmp_path, fault):
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    service = ProbeService(store)
    runner = ProbeRunner("creation", tmp_path, timeout_seconds=6)
    executor = InvestigationTurnExecutor(service, max_workers=2, process_runner=runner)
    a, b = [store.create_creation_session(principal="test", anchor_key=str(i))
            for i in range(2)]
    try:
        bad = executor.accept_turn(a.id, client_message_id="bad", content=json.dumps(
            {"group": "fault", "count": 2, "fault": fault}))
        good = executor.accept_turn(b.id, client_message_id="good", content=json.dumps(
            {"group": "fault", "count": 2}))
        done = wait_terminal(store, [bad, good])
        assert [t.status for t in done] == ["interrupted", "completed"]
        assert done[0].retryable
        runner.timeout_seconds = 30
        resumed = executor.resume_turn(bad.id)
        assert wait_terminal(store, [resumed])[0].status == "completed"
        followups = [executor.accept_turn(s.id, client_message_id="after", content=json.dumps(
            {"group": "after", "count": 2})) for s in (a, b)]
        assert all(t.status == "completed" for t in wait_terminal(store, followups))
        assert all(json.loads(store.turn_result(t.id).answer)["previous"] == 1
                   for t in followups)
    finally:
        executor.shutdown()


def test_already_completed_future_callback_does_not_deadlock():
    service = ProbeService(SimpleNamespace())
    executor = InvestigationTurnExecutor(service)
    executor._pool.shutdown()
    completed = Future()
    completed.set_result(None)
    executor._pool = SimpleNamespace(submit=lambda *args: completed)
    # Deterministically exercise Future.add_done_callback's synchronous path.
    with executor._lock:
        executor._submit_locked("instant", service.execute_turn)
    assert not executor._futures


def test_capacity_queue_drains_without_blocking_acceptance(tmp_path):
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    service = ProbeService(store)
    executor = InvestigationTurnExecutor(
        service, max_workers=2, process_runner=ProbeRunner("creation", tmp_path),
    )
    sessions = [store.create_creation_session(principal="test", anchor_key=str(i))
                for i in range(6)]
    try:
        turns = [executor.accept_turn(s.id, client_message_id="queued", content=json.dumps(
            {"group": "queue", "count": 1, "gate": "release"})) for s in sessions]
        deadline = time.monotonic() + 15
        while len(list((tmp_path / "queue").glob("*.json"))) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert len(list((tmp_path / "queue").glob("*.json"))) == 2
        assert all([e["stage"] for e in store.list_public_turn_events(t.id)] == ["accepted"]
                   for t in turns[2:])
        (tmp_path / "release").touch()
        assert all(t.status == "completed" for t in wait_terminal(store, turns))
    finally:
        (tmp_path / "release").touch()
        executor.shutdown()


def test_recovery_does_not_replay_a_started_process_turn(tmp_path):
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    service = ProbeService(store)
    session = store.create_creation_session(principal="test", anchor_key="recovery")
    turn, _ = store.create_turn(session.id, client_message_id="started", user_input="test")
    store.set_turn_node(turn.id, "isolated_execution")
    executor = InvestigationTurnExecutor(service, process_runner=ProbeRunner("creation", tmp_path))
    try:
        assert executor.recover() == {"recovered": 0, "interrupted": 1}
        assert store.get_turn(turn.id).status == "interrupted"
        assert not executor._futures
    finally:
        executor.shutdown()


def test_production_composition_enables_process_isolation():
    from backend import main
    assert isinstance(main.investigation_turn_executor._process_runner, TurnProcessRunner)
    assert isinstance(main.investigation_creation_turn_executor._process_runner, TurnProcessRunner)
    assert main.investigation_turn_executor._process_runner.scope == "report"
    assert main.investigation_creation_turn_executor._process_runner.scope == "creation"
