"""HTTP and explicitly authenticated WebSocket endpoints for login V2."""

from __future__ import annotations

import asyncio
import json
import time
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)

from .crawler_login_v2_manager import LoginV2Error
from backend.application_auth.store import AuthenticationError, AuthorizationError


def fail(exc):
    if isinstance(exc, LoginV2Error):
        return HTTPException(
            exc.status, detail={"code": exc.code, "message": exc.message}
        )
    return HTTPException(409, detail={"code": str(exc), "message": str(exc)})


def create_login_v2_router(
    manager,
    *,
    principal_provider,
    auth_store,
    require_account,
    auth_required,
    cookie_name,
    allowed_origins,
):
    router = APIRouter()

    def no_store(response: Response):
        response.headers["Cache-Control"] = "private, no-store"

    def session_key(principal):
        return principal.session_id or "local"

    def active(session_id, principal, owner):
        require_account(manager.account_id(session_id), principal, permission="manage")
        return manager.get(
            session_id,
            user_id=principal.id,
            app_session_id=session_key(principal),
            owner_token=owner,
        )

    def operation(account_id, operation_id, principal, owner=None):
        require_account(account_id, principal, permission="manage")
        op = manager.operations.get(operation_id)
        if (
            not op
            or op["account_id"] != account_id
            or op["owner_user_id"] != principal.id
        ):
            raise HTTPException(
                404, detail={"code": "OPERATION_NOT_FOUND", "message": "登录记录不存在"}
            )
        for s in manager.sessions.values():
            if s.operation_id == operation_id and s.snapshot["phase"] not in {
                "done",
                "failed",
                "expired",
                "cancelled",
            }:
                manager.get(
                    s.id,
                    user_id=principal.id,
                    app_session_id=session_key(principal),
                    owner_token=owner or "",
                )
        return op

    def public_operation(op):
        fields = (
            "id",
            "account_id",
            "intent",
            "engine",
            "source_operation_id",
            "recovered_by_operation_id",
            "stage",
            "credentials_reset",
            "profile_authority_established",
            "verified_platform_account_id",
            "result",
            "error_code",
            "created_at",
            "updated_at",
            "completed_at",
        )
        return {k: op.get(k) for k in fields}

    def public_command(command):
        return {
            k: command.get(k)
            for k in (
                "command_id",
                "kind",
                "execution_status",
                "platform_status",
                "result",
                "error_code",
                "created_at",
                "updated_at",
            )
        }

    @router.post("/api/crawler-account-login-sessions/{session_id}/ws-tickets")
    def ticket(
        session_id: str, request: Request, principal=Depends(principal_provider)
    ):
        try:
            active(session_id, principal, request.headers.get("x-login-token", ""))
            return manager.ticket(
                session_id,
                user_id=principal.id,
                app_session_id=session_key(principal),
                owner_token=request.headers.get("x-login-token", ""),
            )
        except ValueError as exc:
            raise fail(exc) from exc

    @router.post(
        "/api/crawler-account-login-sessions/{session_id}/actions",
        status_code=202,
        dependencies=[Depends(no_store)],
    )
    def action(
        session_id: str,
        request: Request,
        command: dict,
        principal=Depends(principal_provider),
    ):
        try:
            active(session_id, principal, request.headers.get("x-login-token", ""))
            return {
                "item": manager.command(
                    session_id,
                    command,
                    user_id=principal.id,
                    app_session_id=session_key(principal),
                    owner_token=request.headers.get("x-login-token", ""),
                )
            }
        except ValueError as exc:
            raise fail(exc) from exc

    @router.get("/api/crawler-accounts/{account_id}/login-operations/{operation_id}")
    def get_operation(
        account_id: str,
        operation_id: str,
        request: Request,
        principal=Depends(principal_provider),
    ):
        try:
            return {
                "item": public_operation(
                    operation(
                        account_id,
                        operation_id,
                        principal,
                        request.headers.get("x-login-token", ""),
                    )
                )
            }
        except ValueError as exc:
            raise fail(exc) from exc

    @router.get(
        "/api/crawler-accounts/{account_id}/login-operations",
        dependencies=[Depends(no_store)],
    )
    def list_operations(
        account_id: str,
        recoverable: bool = False,
        principal=Depends(principal_provider),
    ):
        require_account(account_id, principal, permission="manage")
        manager.recover_interrupted()
        ops = manager.operations.list(
            account_id=account_id,
            owner_user_id=principal.id,
            stages=["recovery_required"] if recoverable else None,
        )
        return {
            "items": [
                public_operation(op)
                for op in ops
                if op["stage"]
                in {
                    "recovery_required",
                    "committed",
                    "failed",
                    "expired",
                    "cancelled",
                    "recovered",
                    "superseded",
                }
            ]
        }

    @router.get(
        "/api/crawler-accounts/{account_id}/login-operations/{operation_id}/commands/{command_id}",
        dependencies=[Depends(no_store)],
    )
    def get_command(
        account_id: str,
        operation_id: str,
        command_id: str,
        request: Request,
        principal=Depends(principal_provider),
    ):
        try:
            operation(
                account_id,
                operation_id,
                principal,
                request.headers.get("x-login-token", ""),
            )
            command = manager.operations.get_command(operation_id, command_id)
            if not command:
                raise HTTPException(
                    404,
                    detail={"code": "COMMAND_NOT_FOUND", "message": "操作记录不存在"},
                )
            return {"item": public_command(command)}
        except ValueError as exc:
            raise fail(exc) from exc

    @router.websocket("/api/crawler-account-login-sessions/{session_id}/stream")
    async def stream(ws: WebSocket, session_id: str):
        def authenticate():
            if auth_required():
                return auth_store.authenticate_session(ws.cookies.get(cookie_name, ""))
            return principal_provider()

        if ws.headers.get("origin", "") not in allowed_origins():
            await ws.close(code=4403)
            return
        try:
            principal = authenticate()
            require_account(
                manager.account_id(session_id), principal, permission="manage"
            )
        except Exception:
            await ws.close(code=4401)
            return
        await ws.accept()
        epoch = None
        sub = None
        receiver = None
        try:
            raw = await asyncio.wait_for(ws.receive_text(), 5)
            if len(raw.encode()) > 4096:
                await ws.close(code=1009)
                return
            auth = json.loads(raw)
            if (
                set(auth) != {"type", "protocol_version", "ticket"}
                or auth.get("type") != "auth"
                or auth.get("protocol_version") != 2
            ):
                await ws.close(code=4403)
                return
            epoch, old = manager.attach(
                session_id,
                auth["ticket"],
                user_id=principal.id,
                app_session_id=session_key(principal),
                controller=ws,
            )
            if old:
                try:
                    await old.close(code=4409)
                except RuntimeError:
                    pass
            await asyncio.to_thread(manager.wait_controller, session_id, epoch)
            sub = manager.subscribe(session_id)
            last_revision = -1
            last_frame = -1
            result_offset = 0
            next_auth = 0
            receiver = asyncio.create_task(ws.receive())
            while True:
                if time.monotonic() >= next_auth:
                    current = authenticate()
                    if current.id != principal.id or session_key(
                        current
                    ) != session_key(principal):
                        raise LoginV2Error("SESSION_REPLACED", status=401)
                    require_account(
                        manager.account_id(session_id), current, permission="manage"
                    )
                    next_auth = time.monotonic() + 2
                snapshot, frame, frame_revision, results, result_offset = (
                    manager.updates(session_id, sub, result_offset)
                )
                if snapshot["controller_epoch"] != epoch and snapshot["phase"] not in {
                    "done",
                    "failed",
                    "expired",
                    "cancelled",
                }:
                    await ws.close(code=4409)
                    break
                if snapshot["state_revision"] != last_revision:
                    await asyncio.wait_for(
                        ws.send_json({"type": "snapshot", "item": snapshot}), 2
                    )
                    last_revision = snapshot["state_revision"]
                for result in results:
                    await asyncio.wait_for(ws.send_json(result), 2)
                if frame and frame_revision != last_frame:
                    await asyncio.wait_for(ws.send_bytes(frame), 2)
                    last_frame = frame_revision
                if snapshot["phase"] in {"done", "failed", "expired", "cancelled"}:
                    await ws.close(code=1000 if snapshot["phase"] == "done" else 4410)
                    break
                wake = asyncio.create_task(sub["event"].wait())
                done, _ = await asyncio.wait(
                    [receiver, wake],
                    timeout=max(0.01, next_auth - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if wake not in done:
                    wake.cancel()
                    try:
                        await wake
                    except asyncio.CancelledError:
                        pass
                if receiver in done:
                    message = receiver.result()
                    if message["type"] == "websocket.disconnect":
                        break
                    raw = message.get("text")
                    if raw is None or len(raw.encode()) > 16384:
                        await ws.close(code=1009)
                        break
                    value = json.loads(raw)
                    if value.get("type") not in {"pong", "frame_rendered"}:
                        # Recheck auth for every mutating command, not just the periodic observer.
                        current = authenticate()
                        require_account(
                            manager.account_id(session_id), current, permission="manage"
                        )
                        try:
                            ack = manager.command(
                                session_id,
                                value,
                                user_id=current.id,
                                app_session_id=session_key(current),
                                epoch=epoch,
                            )
                            await ws.send_json(
                                {"type": "command_ack", **public_command(ack)}
                            )
                        except ValueError as exc:
                            await ws.send_json(
                                {
                                    "type": "error",
                                    "code": getattr(exc, "code", str(exc)),
                                    "message": str(exc),
                                    "command_id": value.get("command_id"),
                                }
                            )
                    receiver = asyncio.create_task(ws.receive())
        except asyncio.TimeoutError:
            try:
                await ws.close(code=4408 if epoch is None else 4410)
            except RuntimeError:
                pass
        except WebSocketDisconnect:
            pass
        except (
            ValueError,
            HTTPException,
            AuthenticationError,
            AuthorizationError,
        ) as exc:
            if epoch is not None:
                manager.cancel(session_id, internal=True)
            try:
                await ws.close(
                    code=4401
                    if isinstance(exc, AuthenticationError)
                    or getattr(exc, "status", None) == 401
                    else 4403
                )
            except RuntimeError:
                pass
        finally:
            if receiver and not receiver.done():
                receiver.cancel()
                try:
                    await receiver
                except asyncio.CancelledError:
                    pass
            if epoch is not None:
                manager.detach(session_id, epoch)
            if sub:
                manager.unsubscribe(session_id, sub)

    return router
