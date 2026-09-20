from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from backend.application_auth.api import create_auth_router
from backend.application_auth.middleware import ApplicationAuthMiddleware
from backend.application_auth.service import ApplicationAuthService
from backend.application_auth.store import (
    AccountExpiredError,
    AuthStore,
    AuthenticationError,
    AuthorizationError,
)
from backend.api.investigation import create_investigation_router
from backend.api.reporting import create_reporting_router
from backend.audit_agent.ingestion import AuditResultStore
from backend.audit_agent.job_store import JobStore
from backend.historical_reports.catalog import HistoricalReportSpec
from backend.historical_reports.service import HistoricalReportDemoService
from backend.historical_reports.store import HistoricalReportWorkspaceStore
from backend.investigation.errors import InvestigationSessionNotFoundError
from backend.investigation_creation.principal import RequestPrincipalProvider
from test_investigation_creation_conversation import (
    _create_completed_turn,
    _publish_fake_run,
    creation_stack,
)


PASSWORD = "correct horse battery staple"


def auth_app(path: Path):
    store = AuthStore(path)
    service = ApplicationAuthService(store)
    provider = RequestPrincipalProvider()
    app = FastAPI()
    app.add_middleware(
        ApplicationAuthMiddleware, store=store, cookie_name="test-session"
    )
    app.include_router(
        create_auth_router(
            service,
            principal_provider=provider,
            cookie_name="test-session",
            cookie_secure=False,
        )
    )

    @app.get("/api/private")
    def private(principal=Depends(provider)):
        return {"user_id": principal.id}

    @app.post("/api/private")
    def write_private(principal=Depends(provider)):
        return {"user_id": principal.id}

    @app.get("/api/private-stream")
    def private_stream(principal=Depends(provider)):
        def events():
            yield "event: item\ndata: first\n\n"
            store.update_user(
                principal.id, actor_user_id="test", status="disabled"
            )
            yield "event: item\ndata: second\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return app, store


def login(client: TestClient, username: str, password: str = PASSWORD):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()


def test_login_cookie_csrf_and_new_tab_token(tmp_path):
    app, store = auth_app(tmp_path / "auth.sqlite3")
    user = store.create_user(username="Alice", password=PASSWORD)
    client = TestClient(app)

    assert client.get("/api/private").status_code == 401
    payload = login(client, "alice")
    assert payload["user"]["id"] == user["id"]
    assert payload["user"]["validity_started_at"]
    assert payload["user"]["expires_at"]
    second_login = client.post(
        "/api/auth/login", json={"username": "Alice", "password": PASSWORD}
    )
    assert "HttpOnly" in second_login.headers["set-cookie"]
    payload = second_login.json()

    assert client.post("/api/private").status_code == 403
    assert client.post(
        "/api/private", headers={"X-CSRF-Token": payload["csrf_token"]}
    ).status_code == 200
    private = client.get("/api/private")
    assert private.headers["cache-control"] == "private, no-store"

    second_tab = client.get("/api/auth/csrf")
    assert second_tab.status_code == 200
    second_token = second_tab.json()["csrf_token"]
    assert client.post(
        "/api/private", headers={"X-CSRF-Token": second_token}
    ).status_code == 200
    # Issuing a token for a second tab does not invalidate the first tab.
    assert client.post(
        "/api/private", headers={"X-CSRF-Token": payload["csrf_token"]}
    ).status_code == 200


def test_disable_password_reset_expiry_and_stream_recheck(tmp_path):
    app, store = auth_app(tmp_path / "auth.sqlite3")
    user = store.create_user(username="stream-user", password=PASSWORD)
    client = TestClient(app)
    payload = login(client, "stream-user")

    stream = client.get("/api/private-stream")
    assert "auth_expired" in stream.text
    assert "second" not in stream.text
    assert stream.headers["cache-control"] == "private, no-store"
    assert client.get("/api/private").status_code == 401

    store.update_user(user["id"], actor_user_id="test", status="active")
    payload = login(client, "stream-user")
    store.update_user(
        user["id"], actor_user_id="test", password="replacement password 123"
    )
    assert client.get("/api/private").status_code == 401

    _, raw_token, _ = store.login("stream-user", "replacement password 123")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE app_users SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (user["id"],),
        )
    with pytest.raises(AccountExpiredError):
        store.authenticate_session(raw_token)


def test_role_change_revokes_existing_sessions(tmp_path):
    store = AuthStore(tmp_path / "auth.sqlite3")
    admin = store.create_user(
        username="role-admin", password=PASSWORD, role="admin"
    )
    _, token, _ = store.login("role-admin", PASSWORD)

    store.update_user(
        admin["id"],
        actor_user_id="test",
        role="user",
    )

    with pytest.raises(AuthenticationError):
        store.authenticate_session(token)
    _, replacement_token, _ = store.login("role-admin", PASSWORD)
    assert store.authenticate_session(replacement_token).role == "user"


def test_background_principal_uses_current_role_and_account_state(tmp_path):
    store = AuthStore(tmp_path / "auth.sqlite3")
    admin = store.create_user(
        username="background-admin", password=PASSWORD, role="admin"
    )
    store.login("background-admin", PASSWORD)

    principal = store.principal_for_user(admin["id"])
    assert principal.id == admin["id"]
    assert principal.is_admin is True

    store.update_user(
        admin["id"], actor_user_id="test", status="disabled"
    )
    with pytest.raises(AuthenticationError):
        store.principal_for_user(admin["id"])


def test_admin_grants_and_username_normalization(tmp_path):
    store = AuthStore(tmp_path / "auth.sqlite3")
    admin = store.create_user(username="Administrator", password=PASSWORD, role="admin")
    user = store.create_user(username="Ａlice", password=PASSWORD)
    with pytest.raises(ValueError):
        store.create_user(username="alice", password=PASSWORD)

    store.register_crawler_account("crawler-1", user["id"])
    service = ApplicationAuthService(store)
    _, admin_token, _ = store.login("Administrator", PASSWORD)
    _, user_token, _ = store.login(unicodedata.normalize("NFKC", "Ａlice"), PASSWORD)
    admin_principal = store.authenticate_session(admin_token)
    user_principal = store.authenticate_session(user_token)
    service.require_admin(admin_principal)
    with pytest.raises(AuthorizationError):
        service.require_admin(user_principal)
    assert service.can_use_crawler_account(user_principal, "crawler-1")
    assert service.can_manage_crawler_account(user_principal, "crawler-1")


def test_job_and_result_queries_do_not_leak_other_owner(tmp_path):
    database = tmp_path / "audit.sqlite3"
    jobs = JobStore(database)
    results = AuditResultStore(database)
    a = jobs.create(owner_user_id="user-a", platform="dy", keyword="a")
    b = jobs.create(owner_user_id="user-b", platform="dy", keyword="b")
    legacy = jobs.create(platform="dy", keyword="legacy")
    for job, title in ((a, "a"), (b, "b"), (legacy, "legacy")):
        results.upsert_result(
            job_id=job["id"],
            platform="dy",
            content_key=title,
            result={"note_id": title, "title": title},
        )

    assert [item["id"] for item in jobs.list_summaries(owner_user_id="user-a")] == [
        a["id"]
    ]
    page = results.list_results(owner_user_id="user-a")
    assert [item["job_id"] for item in page["items"]] == [a["id"]]
    assert page["total"] == 1

    granted = results.list_results(
        owner_user_id="user-a", granted_job_ids=(b["id"],)
    )
    assert {item["job_id"] for item in granted["items"]} == {a["id"], b["id"]}
    assert results.get_result(
        next(item["id"] for item in granted["items"] if item["job_id"] == b["id"]),
        owner_user_id="user-a",
    ) is None
    assert legacy["id"] not in {item["job_id"] for item in granted["items"]}


def test_report_version_id_is_owner_scoped_and_explicitly_grantable(
    creation_stack, tmp_path
):
    stack = creation_stack
    item = _create_completed_turn(stack, workspace_key="auth-report")
    run = _publish_fake_run(stack, item, idempotency_key="auth-report")
    auth = AuthStore(tmp_path / "report-auth.sqlite3")
    user_a = auth.create_user(username="report-user-a", password=PASSWORD)
    user_b = auth.create_user(username="report-user-b", password=PASSWORD)
    with sqlite3.connect(stack["creation_store"].db_path) as connection:
        connection.execute(
            "UPDATE investigation_runs SET owner_principal=? WHERE id=?",
            (user_a["id"], run["run_id"]),
        )

    service = ApplicationAuthService(auth)
    provider = RequestPrincipalProvider()
    app = FastAPI()
    app.add_middleware(
        ApplicationAuthMiddleware, store=auth, cookie_name="report-session"
    )
    app.include_router(create_auth_router(
        service,
        principal_provider=provider,
        cookie_name="report-session",
        cookie_secure=False,
    ))
    app.include_router(create_reporting_router(
        stack["report_store"],
        principal_provider=provider,
        m3_run_store=stack["creation_store"],
        auth_service=service,
    ))
    client_a, client_b = TestClient(app), TestClient(app)
    login(client_a, "report-user-a")
    login(client_b, "report-user-b")
    path = f"/api/report-versions/{run['report_version_id']}"

    own = client_a.get(path)
    assert own.status_code == 200
    assert own.headers["cache-control"] == "private, no-store"
    assert client_b.get(path).status_code == 404

    auth.grant_resource(
        user_id=user_b["id"],
        resource_type="report-version",
        resource_id=run["report_version_id"],
        permission="read",
        actor_user_id=user_a["id"],
    )
    assert client_b.get(path).status_code == 200


def test_investigation_session_turn_and_stream_ids_are_owner_scoped(tmp_path):
    app, store = auth_app(tmp_path / "session-auth.sqlite3")
    user_a = store.create_user(username="session-user-a", password=PASSWORD)
    user_b = store.create_user(username="session-user-b", password=PASSWORD)
    provider = RequestPrincipalProvider()
    sessions = {
        "session-a": SimpleNamespace(owner_principal=user_a["id"]),
        "session-b": SimpleNamespace(owner_principal=user_b["id"]),
    }
    turns = {
        "turn-a": SimpleNamespace(session_id="session-a"),
        "turn-b": SimpleNamespace(session_id="session-b"),
    }

    class Store:
        def get_session(self, session_id):
            if session_id not in sessions:
                raise InvestigationSessionNotFoundError(session_id)
            return sessions[session_id]

        def get_turn(self, turn_id):
            if turn_id not in turns:
                raise InvestigationSessionNotFoundError(turn_id)
            return turns[turn_id]

        def session_anchor(self, _session_id):
            return ""

    fake_service = SimpleNamespace(
        store=Store(),
        get_messages=lambda *_args, **_kwargs: (),
    )
    fake_executor = SimpleNamespace(
        accept_turn=lambda *_args, **_kwargs: None,
        resume_turn=lambda *_args, **_kwargs: None,
    )
    app.include_router(create_investigation_router(
        fake_service,
        fake_executor,
        principal_provider=provider,
        auth_service=ApplicationAuthService(store),
    ))
    client_a, client_b = TestClient(app), TestClient(app)
    csrf_a = login(client_a, "session-user-a")["csrf_token"]
    csrf_b = login(client_b, "session-user-b")["csrf_token"]

    assert client_a.get("/api/investigation-sessions/session-a/messages").status_code == 200
    assert client_b.get("/api/investigation-sessions/session-b/messages").status_code == 200
    for client, other_session, other_turn, csrf in (
        (client_a, "session-b", "turn-b", csrf_a),
        (client_b, "session-a", "turn-a", csrf_b),
    ):
        assert client.get(
            f"/api/investigation-sessions/{other_session}/messages"
        ).status_code == 404
        assert client.post(
            f"/api/investigation-sessions/{other_session}/turns",
            headers={"X-CSRF-Token": csrf},
            json={"client_message_id": "cross-user", "content": "越权请求"},
        ).status_code == 404
        assert client.get(
            f"/api/investigation-turns/{other_turn}"
        ).status_code == 404
        assert client.get(
            f"/api/investigation-turns/{other_turn}/events"
        ).status_code == 404
        assert client.post(
            f"/api/investigation-turns/{other_turn}/resume",
            headers={"X-CSRF-Token": csrf},
        ).status_code == 404


def test_historical_workspace_requires_owner_or_explicit_grant(tmp_path):
    workspace_store = HistoricalReportWorkspaceStore(tmp_path / "history.sqlite3")
    source_database = tmp_path / "unused-source.sqlite3"
    source_database.touch()
    spec = HistoricalReportSpec(
        workspace_id="historical-workspace-a",
        run_id="historical-run-a",
        title="历史报告 A",
        task_id="historical-task-a",
        report_version_id="report-version:11111111111111111111111111111111",
        source_database=source_database,
        source_database_sha256="a" * 64,
        report_content_hash="b" * 64,
        snapshot_hash="c" * 64,
        draft={},
        display_timeline=(),
    )
    workspace_store.register(spec, principal_id="legacy-admin")
    auth = AuthStore(tmp_path / "history-auth.sqlite3")
    admin = auth.create_user(username="history-admin", password=PASSWORD, role="admin")
    user = auth.create_user(username="history-user", password=PASSWORD)

    class EmptyConversationStore:
        def list_report_version_sessions(self, _anchor):
            return ()

    report_database = tmp_path / "report.sqlite3"
    report_database.touch()
    report_store = SimpleNamespace(
        db_path=report_database,
        account_report_sources=(),
    )
    service = HistoricalReportDemoService(
        specs=(spec,),
        workspace_store=workspace_store,
        report_store=report_store,
        report_service=SimpleNamespace(
            store=EmptyConversationStore(),
            get_messages=lambda *_args, **_kwargs: (),
        ),
        executor=SimpleNamespace(),
        auto_register=False,
        access_authorizer=lambda principal_id, workspace: (
            principal_id == admin["id"]
            or auth.has_grant(
                principal_id,
                "report-version",
                str(workspace["report_version_id"]),
                "read",
            )
            or auth.has_grant(
                principal_id,
                "historical-workspace",
                str(workspace["id"]),
                "read",
            )
        ),
    )

    assert service.list_workspaces(principal_id=user["id"]) == ()
    with pytest.raises(InvestigationSessionNotFoundError):
        service.get_workspace(spec.workspace_id, principal_id=user["id"])
    auth.grant_resource(
        user_id=user["id"],
        resource_type="historical-workspace",
        resource_id=spec.workspace_id,
        permission="read",
        actor_user_id=admin["id"],
    )
    assert service.get_workspace(
        spec.workspace_id, principal_id=user["id"]
    )["id"] == spec.workspace_id


def test_expected_browser_identity_blocks_cross_tab_reads_and_writes(tmp_path):
    app, store = auth_app(tmp_path / "identity.sqlite3")
    alice = store.create_user(username="identity-alice", password=PASSWORD)
    bob = store.create_user(username="identity-bob", password=PASSWORD)
    client = TestClient(app)
    login(client, "identity-alice")
    payload = login(client, "identity-bob")
    for method in ("GET", "POST"):
        stale = client.request(method, "/api/private", headers={
            "X-Application-User": alice["id"], "X-CSRF-Token": payload["csrf_token"],
        })
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "AUTH_IDENTITY_CHANGED"
    own = client.get("/api/private", headers={"X-Application-User": bob["id"]})
    assert own.status_code == 200
    assert own.headers["x-application-user"] == bob["id"]


def test_admin_http_lifecycle_and_non_admin_denial(tmp_path):
    from datetime import datetime, timedelta

    app, store = auth_app(tmp_path / "admin.sqlite3")
    store.create_user(username="admin-http", password=PASSWORD, role="admin")
    user = store.create_user(username="member-http", password=PASSWORD)
    admin, member = TestClient(app), TestClient(app)
    admin_headers = {"X-CSRF-Token": login(admin, "admin-http")["csrf_token"]}
    member_headers = {"X-CSRF-Token": login(member, "member-http")["csrf_token"]}
    path = f"/api/admin/users/{user['id']}"
    for method, url, body in (
        ("GET", "/api/admin/users", None),
        ("POST", "/api/admin/users", {"username":"unauthorized", "password":PASSWORD}),
        ("PATCH", path, {"renew_days":7}),
        ("GET", path + "/grants", None),
        ("PUT", path + "/grants/crawler-account/example", {"permission":"use"}),
        ("DELETE", path + "/grants/crawler-account/example/use", None),
    ):
        assert member.request(method, url, headers=member_headers, json=body).status_code == 403
    created = admin.post("/api/admin/users", headers=admin_headers, json={
        "username":"created-user", "password":PASSWORD, "role":"user",
        "validity_days":7, "activation_mode":"first_login",
    })
    assert created.status_code == 201
    assert not created.json()["user"]["expires_at"]
    activated = login(TestClient(app), "created-user")["user"]
    assert datetime.fromisoformat(activated["expires_at"]) - datetime.fromisoformat(activated["validity_started_at"]) == timedelta(days=7)
    old_expiry = datetime.fromisoformat(store.get_user(user["id"])["expires_at"])
    renewed = admin.patch(path, headers=admin_headers, json={"renew_days":7})
    assert datetime.fromisoformat(renewed.json()["user"]["expires_at"]) == old_expiry + timedelta(days=7)
    assert admin.put(path + "/grants/crawler-account/example", headers=admin_headers, json={"permission":"use"}).status_code == 400
    assert admin.put(path + "/grants/job/example", headers=admin_headers, json={"permission":"read"}).status_code == 200
    assert admin.get(path + "/grants").json()["items"][0]["permission"] == "read"
    assert admin.delete(path + "/grants/job/example/read", headers=admin_headers).status_code == 204
    assert admin.get(path + "/grants").json()["items"] == []
    assert admin.patch(path, headers=admin_headers, json={"status":"disabled"}).status_code == 200
    assert member.get("/api/private").status_code == 401
    admin.patch(path, headers=admin_headers, json={"status":"active"})
    login(member, "member-http")
    admin.patch(path, headers=admin_headers, json={"password":"changed password 1234"})
    assert member.get("/api/private").status_code == 401
    assert member.post("/api/auth/login", json={"username":"member-http","password":PASSWORD}).status_code == 401
    login(member, "member-http", "changed password 1234")


def test_auth_config_is_public_and_only_exposes_mode(tmp_path, monkeypatch):
    from backend import main
    from fastapi import Response

    app, _ = auth_app(tmp_path / "config.sqlite3")
    app.get("/api/auth/config")(main.application_auth_config)
    client = TestClient(app)
    for enabled in (True, False):
        monkeypatch.setattr(main, "_authz_enabled", lambda: enabled)
        result = client.get("/api/auth/config")
        assert result.status_code == 200
        assert result.json() == {"enabled": enabled}
        assert result.headers["cache-control"] == "no-store"
        assert client.get("/api/private").status_code == 401


def test_workspace_navigation_run_id_is_owner_scoped(creation_stack):
    stack = creation_stack
    item = _create_completed_turn(stack, workspace_key="navigation-owner")
    run = _publish_fake_run(stack, item, idempotency_key="navigation-owner")
    own = stack["client"].get("/api/investigation-workspaces").json()["items"]
    assert any(entry["run_id"] == run["run_id"] for entry in own)
    from backend.investigation_creation.principal import Principal
    stack["principals"].current = Principal("other-owner")
    assert stack["client"].get("/api/investigation-workspaces").json()["items"] == []


def test_admin_has_no_account_expiry_but_sessions_still_expire(tmp_path):
    from datetime import datetime, timedelta, timezone
    store = AuthStore(tmp_path / "auth.sqlite3")
    admin = store.create_user(username="permanent-admin", password=PASSWORD, role="admin", activation_mode="created_at")
    assert admin["expires_at"] is None
    # Historical seven-day admin records must not lock the administrator out.
    with sqlite3.connect(store.db_path) as db:
        db.execute("UPDATE app_users SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (admin["id"],))
    assert store.get_user(admin["id"])["expires_at"] is None
    assert store.principal_for_user(admin["id"]).is_admin
    user, token, csrf = store.login("permanent-admin", PASSWORD)
    assert user["expires_at"] is None
    principal = store.authenticate_session(token, csrf_token=csrf)
    assert principal.is_admin
    store.authenticate_session(token, csrf_token=store.rotate_csrf(token))
    future = datetime.now(timezone.utc) + timedelta(days=8)
    with pytest.raises(AuthenticationError, match="session has expired"):
        store.authenticate_session(token, now=future)
    assert store.principal_for_user(admin["id"], now=future).is_admin
    with sqlite3.connect(store.db_path) as db:
        db.execute("UPDATE login_sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE user_id=?", (admin["id"],))
    with pytest.raises(AuthenticationError, match="session has expired"):
        store.rotate_csrf(token)
    _, replacement, _ = store.login("permanent-admin", PASSWORD)
    assert store.authenticate_session(replacement).is_admin
    with pytest.raises(ValueError, match="need no renewal"):
        store.update_user(admin["id"], actor_user_id="test", renew_days=7)
    store.update_user(admin["id"], actor_user_id="test", password="replacement password 123")
    with pytest.raises(AuthenticationError):
        store.authenticate_session(replacement)
    store.update_user(admin["id"], actor_user_id="test", status="disabled")
    with pytest.raises(AuthenticationError):
        store.login("permanent-admin", "replacement password 123")
    with pytest.raises(AuthenticationError):
        store.principal_for_user(admin["id"])


def test_admin_role_changes_preserve_regular_user_expiry(tmp_path):
    from datetime import datetime, timedelta, timezone
    store = AuthStore(tmp_path / "auth.sqlite3")
    user = store.create_user(username="changing-role", password=PASSWORD)
    _, token, _ = store.login("changing-role", PASSWORD)
    promoted = store.update_user(user["id"], actor_user_id="test", role="admin")
    assert promoted["expires_at"] is None
    with pytest.raises(AuthenticationError):
        store.authenticate_session(token)
    demoted = store.update_user(user["id"], actor_user_id="test", role="user")
    assert datetime.fromisoformat(demoted["expires_at"]) - datetime.fromisoformat(demoted["validity_started_at"]) == timedelta(days=7)
    with pytest.raises(AccountExpiredError):
        store.principal_for_user(user["id"], now=datetime.now(timezone.utc) + timedelta(days=8))


def test_admin_login_cookie_uses_session_expiry(tmp_path):
    app, store = auth_app(tmp_path / "auth.sqlite3")
    store.create_user(username="cookie-admin", password=PASSWORD, role="admin")
    client = TestClient(app)
    result = client.post("/api/auth/login", json={"username":"cookie-admin", "password":PASSWORD})
    assert result.status_code == 200
    assert result.json()["user"]["expires_at"] is None
    assert "Max-Age=" in result.headers["set-cookie"]
    assert "HttpOnly" in result.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/auth/csrf").status_code == 200
