"""Project actions from durable task lifecycle, independently of quota charges."""

import json


def admission_actions(actions, row):
    result = dict(actions)
    if not row:
        result["finish_collection"] = False
        return result
    unfinished = row["state"] == "RESERVED"
    ending = unfinished and row["decision"] == "CANCELLED"
    ended = not unfinished
    if ending or (ended and row["quota_policy"] == "accepted_v2"):
        result = {key: False for key in result}
    if row.get("queue_state") != "HELD" or row["decision"] != "OPEN":
        result["finish_collection"] = False
    action = json.loads(row.get("payload_json") or "{}").get("resume_action")
    resuming = unfinished and row["decision"] == "OPEN" and row.get("queue_state") in {"QUEUED", "RUNNING"} and bool(action)
    if resuming:
        for name in ("resume_crawl", "resume_analysis", "backfill_analysis"):
            result[name] = False
    result.update(
        resuming=resuming,
        finishing_collection=resuming and action == "finish_collection",
        end_task=unfinished and row["decision"] == "OPEN",
        ending=ending,
        ended=ended,
        delete_job=ended,
    )
    return result
