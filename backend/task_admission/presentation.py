"""Project actions from durable task lifecycle, independently of quota charges."""


def admission_actions(actions, row):
    result = dict(actions)
    if not row:
        return result
    unfinished = row["state"] == "RESERVED"
    ending = unfinished and row["decision"] == "CANCELLED"
    ended = not unfinished
    if ending or (ended and row["quota_policy"] == "accepted_v2"):
        result = {key: False for key in result}
    result.update(
        end_task=unfinished and row["decision"] == "OPEN",
        ending=ending,
        ended=ended,
        delete_job=ended,
    )
    return result
