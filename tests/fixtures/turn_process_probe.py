"""Offline child: real Hermes catalog/lock, durable transcript, no Provider."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.hermes_runtime.adapter import HermesRuntimeBinding, session_runtime_home
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.investigation.store import InvestigationStore


def main():
    scope, turn_id, operation, storage = sys.argv[1:]
    root = Path(storage)
    store = InvestigationStore(root / "investigation.sqlite3")
    turn = store.get_turn(turn_id)
    user = store.get_user_message_for_turn(turn_id).content
    request = json.loads(user)
    binding = HermesRuntimeBinding()
    mode = "creation" if scope == "creation" else "account-activity"
    with binding.product_mode_execution(
        session_runtime_home(root / "hermes", turn.session_id), product_mode=mode,
    ):
        names = {t["function"]["name"] for t in binding.tool_definitions(
            enabled_toolsets=["investigation"], product_mode=mode,
        )}
        assert ("create_investigation_draft" in names) == (scope == "creation")
        assert ("read_report" in names) == (scope == "report")
        barrier = root / request["group"]
        barrier.mkdir(exist_ok=True)
        marker = barrier / (turn.session_id + ".json")
        marker.write_text(json.dumps({"pid": os.getpid(), "entered": time.time()}))
        deadline = time.monotonic() + 15
        while len(list(barrier.glob("*.json"))) < request["count"]:
            if time.monotonic() > deadline:
                raise RuntimeError("Turns did not overlap inside the real mode lock")
            time.sleep(0.02)
        while request.get("gate") and not (root / request["gate"]).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("capacity test gate did not open")
            time.sleep(0.02)
        if operation != "resume":
            if request.get("fault") == "crash":
                os._exit(17)
            if request.get("fault") == "hang":
                time.sleep(120)
        # Check again after the other processes switched/discovered catalogs.
        assert names == {t["function"]["name"] for t in binding.tool_definitions(
            enabled_toolsets=["investigation"], product_mode=mode,
        )}
        history = store.latest_completed_hermes_transcript(turn.session_id) or []
        prior = [m["content"] for m in history if m["role"] == "assistant"]
        answer = json.dumps({"session": turn.session_id, "scope": scope,
                             "previous": len(prior), "pid": os.getpid()})
        for item in prior:
            assert json.loads(item)["session"] == turn.session_id
        transcript = history + [{"role": "user", "content": user},
                                {"role": "assistant", "content": answer}]
        result = {"completed": True, "final_response": answer, "messages": transcript}
        # Reuse the production persistence/protocol path; only the model result
        # above is deterministic. Catalog discovery and its lock are real.
        service = HermesInvestigationAgentService(
            store=store, report_facade=object(), bind_runtime=False,
        )
        service._persist_result(turn_id, result, previous_message_count=len(history),
                                transcript=transcript)


if __name__ == "__main__":
    main()
