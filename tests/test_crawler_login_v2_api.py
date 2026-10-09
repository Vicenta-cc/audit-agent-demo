import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.application_auth.middleware import ApplicationAuthMiddleware
from backend.application_auth.store import AuthStore
from backend.audit_agent.crawler_login_v2_api import create_login_v2_router
from backend.investigation_creation.principal import RequestPrincipalProvider
from test_crawler_login_v2_manager import manager as manager, wait_until


@pytest.fixture
def api(manager, tmp_path):
    m, a = manager
    auth = AuthStore(tmp_path / "auth.db")
    user = auth.create_user(username="owner", password="long password 123456")
    auth.create_user(username="other", password="long password 123456")
    _, token, csrf = auth.login("owner", "long password 123456")
    _, other_token, _ = auth.login("other", "long password 123456")
    principal = auth.authenticate_session(token)
    allowed = [True]

    def require(account_id, p, permission):
        if not allowed[0] or p.id != user["id"] or account_id != a["id"]:
            raise HTTPException(404)
        return a

    app = FastAPI()
    provider = RequestPrincipalProvider()
    app.add_middleware(ApplicationAuthMiddleware, store=auth, cookie_name="session")
    app.include_router(
        create_login_v2_router(
            m,
            principal_provider=provider,
            auth_store=auth,
            require_account=require,
            auth_required=lambda: True,
            cookie_name="session",
            allowed_origins=lambda: ["https://app.test"],
        )
    )
    s = m.start(
        a,
        user_id=principal.id,
        app_session_id=principal.session_id,
        owner_token="x" * 32,
        request_key="key",
    )[0]
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    client = TestClient(app)
    client.cookies.set("session", token)
    client.headers.update({"X-CSRF-Token": csrf, "X-Login-Token": "x" * 32})
    yield client, m, s, auth, principal, other_token, allowed
    client.close()


def test_rest_cookie_csrf_owner_and_command_queries(api):
    client, m, s, auth, p, other_token, allowed = api
    path = f"/api/crawler-account-login-sessions/{s['id']}/ws-tickets"
    assert client.post(path, headers={"X-CSRF-Token": ""}).status_code == 403
    assert client.post(path, headers={"X-Login-Token": "bad"}).status_code == 403
    assert client.post(path).status_code == 200
    client.cookies.set("session", other_token)
    assert (
        client.get(
            f"/api/crawler-accounts/{s['account_id']}/login-operations/{s['operation_id']}"
        ).status_code
        == 404
    )


def test_ws_rejects_origin_missing_cookie_and_ticket_reuse(api):
    client, m, s, auth, p, _, _ = api
    url = f"/api/crawler-account-login-sessions/{s['id']}/stream"
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url, headers={"Origin": "https://evil.test"}):
            pass
    assert exc.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as exc:
        with TestClient(client.app).websocket_connect(
            url, headers={"Origin": "https://app.test"}
        ):
            pass
    assert exc.value.code == 4401
    ticket = client.post(
        f"/api/crawler-account-login-sessions/{s['id']}/ws-tickets"
    ).json()["ticket"]
    with client.websocket_connect(url, headers={"Origin": "https://app.test"}) as ws:
        ws.send_json({"type": "auth", "protocol_version": 2, "ticket": ticket})
        snapshot = ws.receive_json()["item"]
        assert snapshot["controller_state"] == "attached"
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            url, headers={"Origin": "https://app.test"}
        ) as ws:
            ws.send_json({"type": "auth", "protocol_version": 2, "ticket": ticket})
            ws.receive_json()
    assert exc.value.code == 4403


def test_ws_rest_shared_controller_and_revocation(api):
    client, m, s, auth, p, _, allowed = api
    path = f"/api/crawler-account-login-sessions/{s['id']}"
    ticket = client.post(path + "/ws-tickets").json()["ticket"]
    with client.websocket_connect(
        path + "/stream", headers={"Origin": "https://app.test"}
    ) as ws:
        ws.send_json({"type": "auth", "protocol_version": 2, "ticket": ticket})
        snapshot = ws.receive_json()["item"]
        epoch = snapshot["controller_epoch"]
        c = dict(
            type="action",
            command_id="sms1",
            operation_id=s["operation_id"],
            controller_epoch=epoch,
            sequence=1,
            expected_state_revision=snapshot["state_revision"],
            action="request_sms",
            payload={"phone": "13800000000"},
        )
        assert client.post(path + "/actions", json=c).status_code == 202
        query = f"/api/crawler-accounts/{s['account_id']}/login-operations/{s['operation_id']}/commands/sms1"
        wait_until(
            lambda: client.get(query).json()["item"]["execution_status"] == "executed"
        )
        assert (
            client.post(path + "/actions", json=c).json()["item"]["execution_status"]
            == "executed"
        )
        allowed[0] = False
        ws.send_json({**c, "command_id": "sms2", "sequence": 2})
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "cancelled")


def test_rest_requires_live_controller(api):
    client, m, s, auth, p, _, _ = api
    current = m.sessions[s["id"]].snapshot
    c = dict(
        type="action",
        command_id="sms",
        operation_id=s["operation_id"],
        controller_epoch=1,
        sequence=1,
        expected_state_revision=current["state_revision"],
        action="request_sms",
        payload={},
    )
    response = client.post(
        f"/api/crawler-account-login-sessions/{s['id']}/actions", json=c
    )
    assert (
        response.status_code == 409
        and response.json()["detail"]["code"] == "CONTROLLER_REPLACED"
    )


def test_ws_stale_command_explicitly_not_executed_and_new_id_retry(api):
    client, m, s, auth, p, _, _ = api
    path = f"/api/crawler-account-login-sessions/{s['id']}"
    ticket = client.post(path + "/ws-tickets").json()["ticket"]
    with client.websocket_connect(
        path + "/stream", headers={"Origin": "https://app.test"}
    ) as ws:
        ws.send_json({"type": "auth", "protocol_version": 2, "ticket": ticket})
        snapshot = ws.receive_json()["item"]
        command = dict(
            type="action",
            command_id="stale",
            operation_id=s["operation_id"],
            controller_epoch=snapshot["controller_epoch"],
            sequence=1,
            expected_state_revision=snapshot["state_revision"] - 1,
            action="request_sms",
            payload={"phone": "13800000000"},
        )
        ws.send_json(command)
        error = ws.receive_json()
        assert error["type"] == "error" and error["code"] == "STALE_STATE"
        assert (
            error["execution_status"] == "rejected"
            and error["platform_status"] == "not_applicable"
        )
        assert m.operations.get_command(s["operation_id"], "stale") is None
        ws.send_json(
            {
                **command,
                "command_id": "deliberate-retry",
                "sequence": 2,
                "expected_state_revision": snapshot["state_revision"],
            }
        )
        messages = []
        while not any(msg.get("type") == "command_result" for msg in messages):
            messages.append(ws.receive_json())
        result = next(msg for msg in messages if msg["type"] == "command_result")
        assert (
            result["execution_status"] == "executed"
            and result["platform_status"] == "confirmed"
        )
        assert (
            m.operations.get_command(s["operation_id"], "deliberate-retry")[
                "execution_status"
            ]
            == "executed"
        )


def test_ws_conflicting_id_preserves_original_unknown_outcome(api):
    client, m, s, auth, p, _, _ = api
    path = f"/api/crawler-account-login-sessions/{s['id']}"
    ticket = client.post(path + "/ws-tickets").json()["ticket"]
    with client.websocket_connect(
        path + "/stream", headers={"Origin": "https://app.test"}
    ) as ws:
        ws.send_json({"type": "auth", "protocol_version": 2, "ticket": ticket})
        snapshot = ws.receive_json()["item"]
        original = dict(
            type="action",
            command_id="uncertain",
            operation_id=s["operation_id"],
            controller_epoch=snapshot["controller_epoch"],
            sequence=1,
            expected_state_revision=snapshot["state_revision"],
            action="request_sms",
            payload={"phone": "13800000000"},
        )
        m.operations.record_command(
            s["operation_id"],
            "uncertain",
            "request_sms",
            original,
            application_session_id=p.session_id,
            controller_epoch=snapshot["controller_epoch"],
            sequence=1,
        )
        m.operations.dispatch_command(s["operation_id"], "uncertain")
        m.operations.finish_command(
            s["operation_id"],
            "uncertain",
            execution_status="unknown",
            platform_status="unknown",
        )
        ws.send_json({**original, "payload": {"phone": "13900000000"}})
        error = ws.receive_json()
        assert error["code"] == "COMMAND_ID_CONFLICT"
        assert (
            error["execution_status"] == "unknown"
            and error["platform_status"] == "unknown"
        )
        assert (
            m.operations.get_command(s["operation_id"], "uncertain")["execution_status"]
            == "unknown"
        )


def test_old_ws_attach_failure_cannot_cancel_replacement(api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    client, m, s, auth, p, _, _ = api
    path = f"/api/crawler-account-login-sessions/{s['id']}"
    entered, release = threading.Event(), threading.Event()
    wait = m.wait_controller

    def delayed(session_id, epoch, timeout=3):
        if epoch == 1:
            entered.set()
            assert release.wait(3)
        return wait(session_id, epoch, timeout)

    monkeypatch.setattr(m, "wait_controller", delayed)
    first = client.post(path + "/ws-tickets").json()["ticket"]
    second = client.post(path + "/ws-tickets").json()["ticket"]

    def old_connection():
        with client.websocket_connect(
            path + "/stream", headers={"Origin": "https://app.test"}
        ) as ws:
            ws.send_json({"type": "auth", "protocol_version": 2, "ticket": first})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4409

    with ThreadPoolExecutor() as pool:
        old = pool.submit(old_connection)
        assert entered.wait(3)
        with client.websocket_connect(
            path + "/stream", headers={"Origin": "https://app.test"}
        ) as ws:
            ws.send_json({"type": "auth", "protocol_version": 2, "ticket": second})
            snapshot = ws.receive_json()["item"]
            assert (
                snapshot["controller_epoch"] == 2
                and snapshot["controller_state"] == "attached"
            )
            release.set()
            old.result(timeout=3)
            assert m.sessions[s["id"]].snapshot["phase"] == "sms_required"
            ws.send_json(
                dict(
                    type="action",
                    command_id="successor",
                    operation_id=s["operation_id"],
                    controller_epoch=2,
                    sequence=1,
                    expected_state_revision=snapshot["state_revision"],
                    action="request_sms",
                    payload={"phone": "13800000000"},
                )
            )
            messages = []
            while not any(msg.get("type") == "command_result" for msg in messages):
                messages.append(ws.receive_json())
            assert (
                next(msg for msg in messages if msg["type"] == "command_result")[
                    "platform_status"
                ]
                == "confirmed"
            )


def test_unclassified_value_error_is_unknown_not_rejected(api, monkeypatch):
    client, m, s, auth, p, _, _ = api

    def failing(*args, **kwargs):
        raise ValueError("unexpected failure")

    monkeypatch.setattr(m.operations, "record_command", failing)
    path = f"/api/crawler-account-login-sessions/{s['id']}"
    ticket = client.post(path + "/ws-tickets").json()["ticket"]
    with client.websocket_connect(
        path + "/stream", headers={"Origin": "https://app.test"}
    ) as ws:
        ws.send_json({"type": "auth", "protocol_version": 2, "ticket": ticket})
        snapshot = ws.receive_json()["item"]
        ws.send_json(
            dict(
                type="action",
                command_id="unknown",
                operation_id=s["operation_id"],
                controller_epoch=snapshot["controller_epoch"],
                sequence=1,
                expected_state_revision=snapshot["state_revision"],
                action="request_sms",
                payload={},
            )
        )
        result = ws.receive_json()
        assert (
            result["execution_status"] == "unknown"
            and result["platform_status"] == "unknown"
        )
