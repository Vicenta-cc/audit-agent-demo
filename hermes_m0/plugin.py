"""Hermes plugin registration for the M0 investigation toolset."""

from __future__ import annotations

from typing import Any

import os

from hermes_m0.runtime import (
    get_runtime,
    report_task_runtime_for_session,
    task_runtime_for_session,
)
from hermes_m0.schemas import (
    M2_ACCOUNT_ACTIVITY_TOOLS,
    REAL_REPORT_TASK_TOOLS,
    REPORT_TASK_TOOLS,
    TASK_TOOLS,
    TOOLS,
    TOOLSET,
)
from hermes_m0.tool_results import error_result


_TOOL_NAMES = frozenset(
    schema["name"]
    for schema in (
        *TOOLS,
        *TASK_TOOLS,
        *REPORT_TASK_TOOLS,
        *REAL_REPORT_TASK_TOOLS,
        *M2_ACCOUNT_ACTIVITY_TOOLS,
    )
)


def _handler(tool_name: str):
    def handle(args: dict[str, Any], **kwargs: Any) -> str:
        session_id = str(kwargs.get("session_id") or "")
        report_task_runtime = report_task_runtime_for_session(session_id)
        if report_task_runtime is not None:
            return report_task_runtime.dispatch(
                tool_name,
                args,
                session_id=session_id,
                turn_id=str(kwargs.get("task_id") or ""),
            )
        task_runtime = task_runtime_for_session(session_id)
        if task_runtime is not None:
            return task_runtime.dispatch(
                tool_name,
                args,
                session_id=session_id,
                turn_id=str(kwargs.get("task_id") or ""),
            )
        return get_runtime().dispatch(
            tool_name,
            args,
            session_id=session_id,
        )

    return handle


def _idempotent_tool_execution(**kwargs: Any) -> Any:
    tool_name = str(kwargs.get("tool_name") or "")
    args = kwargs.get("args")
    next_call = kwargs["next_call"]
    if tool_name not in _TOOL_NAMES:
        return next_call(args)
    try:
        session_id = str(kwargs.get("session_id") or "")
        runtime = (
            report_task_runtime_for_session(session_id)
            or task_runtime_for_session(session_id)
            or get_runtime()
        )
        return runtime.execute_tool_call(
            session_id=session_id,
            tool_call_id=str(kwargs.get("tool_call_id") or ""),
            tool_name=tool_name,
            args=args,
            next_call=next_call,
        )
    except Exception:
        # Hermes middleware is fail-open on callback errors. Return an explicit
        # ToolResult here so a ledger failure cannot execute a read twice.
        return error_result(
            tool=tool_name,
            code="idempotency_ledger_failure",
            message="The tool was not replayed because its idempotency ledger failed.",
        )


def register(ctx: Any) -> None:
    ctx.register_middleware("tool_execution", _idempotent_tool_execution)
    if os.environ.get("HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE") == "1":
        schemas = list(M2_ACCOUNT_ACTIVITY_TOOLS)
    elif os.environ.get("HERMES_INVESTIGATION_REAL_REPORT_MODE") == "1":
        schemas = list(REAL_REPORT_TASK_TOOLS)
    elif os.environ.get("HERMES_INVESTIGATION_REPORT_TASK_MODE") == "1":
        schemas = list(REPORT_TASK_TOOLS)
    elif os.environ.get("HERMES_INVESTIGATION_TASK_MODE") == "1":
        schemas = list(TASK_TOOLS)
    else:
        schemas = list(TOOLS)
    for schema in schemas:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=_handler(schema["name"]),
            description=schema["description"],
        )
