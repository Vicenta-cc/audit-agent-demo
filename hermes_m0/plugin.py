"""Hermes plugin registration for the M0 investigation toolset."""

from __future__ import annotations

from typing import Any

import os

from backend.investigation_creation.tools import (
    HERMES_M3_TOOL_SCHEMAS,
    M3_MUTATION_TOOL_NAMES,
    HermesToolExecutionIdentity,
    dispatch_hermes_investigation_creation_tool,
    dispatch_hermes_investigation_creation_tool_with_identity,
)
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
_M3_CREATION_TOOL_NAMES = frozenset(
    schema["name"] for schema in HERMES_M3_TOOL_SCHEMAS
)


def _handler(tool_name: str):
    def handle(args: dict[str, Any], **kwargs: Any) -> str:
        if tool_name in _M3_CREATION_TOOL_NAMES:
            return dispatch_hermes_investigation_creation_tool(
                tool_name,
                args,
                session_id=str(kwargs.get("session_id") or ""),
                turn_id=str(kwargs.get("task_id") or ""),
                tool_call_id=str(kwargs.get("tool_call_id") or ""),
            )
        session_id = str(kwargs.get("session_id") or "")
        report_task_runtime = report_task_runtime_for_session(session_id)
        if report_task_runtime is not None:
            return report_task_runtime.dispatch(
                tool_name,
                args,
                session_id=session_id,
                turn_id=str(kwargs.get("task_id") or ""),
            )
        if os.environ.get("HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE") == "1":
            return error_result(
                tool=tool_name,
                code="product_session_unbound",
                message="The product Investigation Session has no authorized report binding.",
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
    if (
        tool_name in M3_MUTATION_TOOL_NAMES
        and os.environ.get("HERMES_INVESTIGATION_CREATION_MODE") == "1"
    ):
        try:
            identity = HermesToolExecutionIdentity.require(
                session_id=str(kwargs.get("session_id") or ""),
                turn_id=str(kwargs.get("turn_id") or kwargs.get("task_id") or ""),
                tool_call_id=str(kwargs.get("tool_call_id") or ""),
            )
            return dispatch_hermes_investigation_creation_tool_with_identity(
                tool_name,
                dict(args or {}),
                session_id=identity.session_id,
                identity=identity,
            )
        except Exception as exc:
            return error_result(
                tool=tool_name,
                code=str(getattr(exc, "code", "idempotency_ledger_failure")),
                message=str(exc),
            )
    if tool_name not in _TOOL_NAMES and tool_name != "list_post_comments":
        return next_call(args)
    try:
        session_id = str(kwargs.get("session_id") or "")
        runtime = report_task_runtime_for_session(session_id)
        if (
            runtime is None
            and os.environ.get("HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE") == "1"
        ):
            return error_result(
                tool=tool_name,
                code="product_session_unbound",
                message="The product Investigation Session has no authorized report binding.",
            )
        runtime = runtime or task_runtime_for_session(session_id) or get_runtime()
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
    catalogs = (
        (
            "account-activity",
            "HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE",
            M2_ACCOUNT_ACTIVITY_TOOLS,
        ),
        (
            "creation",
            "HERMES_INVESTIGATION_CREATION_MODE",
            HERMES_M3_TOOL_SCHEMAS,
        ),
        (
            "real-report",
            "HERMES_INVESTIGATION_REAL_REPORT_MODE",
            REAL_REPORT_TASK_TOOLS,
        ),
        (
            "report-task",
            "HERMES_INVESTIGATION_REPORT_TASK_MODE",
            REPORT_TASK_TOOLS,
        ),
        ("task", "HERMES_INVESTIGATION_TASK_MODE", TASK_TOOLS),
    )
    active = [
        (name, schemas)
        for name, flag, schemas in catalogs
        if os.environ.get(flag) == "1"
    ]
    if len(active) > 1:
        raise RuntimeError(
            "Hermes investigation modes are mutually exclusive: "
            + ", ".join(name for name, _ in active)
        )
    schemas = list(active[0][1]) if active else list(TOOLS)
    if active and active[0][0] == "account-activity" and os.environ.get("HERMES_INVESTIGATION_PASS_REPORT") == "1":
        from .pass_support import pass_tool_schemas
        schemas = pass_tool_schemas()
    for schema in schemas:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=_handler(schema["name"]),
            description=schema["description"],
        )
