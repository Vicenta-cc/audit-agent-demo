"""Internal one-Turn worker; never starts the API or the acquisition Worker."""

from __future__ import annotations

import argparse
import os
import signal
import threading


def execute(scope: str, turn_id: str, *, resume: bool = False) -> None:
    # Reuse the production composition, authorization and node observers. Merely
    # importing main does not run API startup/recovery or start background jobs.
    from backend import main

    services = {
        "report": main.investigation_agent_service,
        "creation": main.investigation_creation_conversation_service,
    }
    service = services[scope]
    if not service.owns_turn(turn_id):
        raise ValueError("Turn does not belong to this worker scope")
    try:
        operation = service.execute_resume if resume else service.execute_turn
        operation(turn_id)
    finally:
        service.close()


def _watch_parent(parent_pid: int) -> None:
    tick = threading.Event()
    while not tick.wait(0.5):
        if os.getppid() != parent_pid:
            # The API may have crashed. Prevent an orphan from writing after
            # startup recovery has made the durable Turn resumable.
            os.killpg(os.getpgrp(), signal.SIGKILL)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scope", choices=("report", "creation"))
    parser.add_argument("turn_id")
    parser.add_argument("operation", choices=("execute", "resume"))
    parser.add_argument("parent_pid", type=int)
    args = parser.parse_args()
    if os.getppid() != args.parent_pid:
        raise RuntimeError("Turn executor is no longer alive")
    threading.Thread(target=_watch_parent, args=(args.parent_pid,), daemon=True).start()
    execute(args.scope, args.turn_id, resume=args.operation == "resume")


if __name__ == "__main__":
    main()
