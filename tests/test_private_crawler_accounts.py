"""Private platform accounts: no sharing, including administrator task selection."""
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import main
from backend.application_auth.api import create_auth_router
from backend.application_auth.middleware import ApplicationAuthMiddleware
from backend.application_auth.service import ApplicationAuthService
from backend.application_auth.store import AuthStore
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_login_manager import CrawlerAccountLoginManager
from backend.investigation_creation.principal import Principal, RequestPrincipalProvider
from backend.task_admission.store import AdmissionError
from test_task_admission import stack, enqueue

PASSWORD = "private account password 123"


def test_private_account_http_lifecycle_and_owner_selection(tmp_path, monkeypatch):
    auth = AuthStore(tmp_path / "auth.sqlite3")
    accounts = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(settings, "app_auth_mode", "required")
    monkeypatch.setattr(settings, "app_auth_db", auth.db_path)
    monkeypatch.setattr(settings, "task_resource_lock_dir", tmp_path / "locks")
    monkeypatch.setattr(main, "auth_store", auth)
    monkeypatch.setattr(main, "auth_service", ApplicationAuthService(auth))
    monkeypatch.setattr(main, "crawler_account_store", accounts)
    monkeypatch.setattr(main, "crawler_account_login_manager", SimpleNamespace(
        start=lambda account, token, **kw: {"account_id": account["id"], "owner_user_id": kw["owner_user_id"]},
    ))
    app = FastAPI()
    app.dependency_overrides[main.principal_provider] = RequestPrincipalProvider()
    app.add_middleware(ApplicationAuthMiddleware, store=auth, cookie_name="private-session")
    app.include_router(create_auth_router(main.auth_service, principal_provider=main.principal_provider,
        cookie_name="private-session", cookie_secure=False))
    for path, handler, method in [
        ("/api/crawler-accounts", main.create_crawler_account, "POST"),
        ("/api/crawler-accounts", main.list_crawler_accounts, "GET"),
        ("/api/crawler-accounts/{account_id}", main.update_crawler_account, "PATCH"),
        ("/api/crawler-accounts/{account_id}", main.delete_crawler_account, "DELETE"),
        ("/api/crawler-accounts/{account_id}/login-sessions", main.start_crawler_account_login, "POST"),
    ]:
        app.add_api_route(path, handler, methods=[method])
    clients, users, created = {}, {}, {}
    for name in ("alice", "bravo", "admin"):
        users[name] = auth.create_user(username=name, password=PASSWORD, role="admin" if name == "admin" else "user")
        client = TestClient(app)
        payload = client.post("/api/auth/login", json={"username":name,"password":PASSWORD}).json()
        client.headers.update({"X-CSRF-Token":payload["csrf_token"]})
        clients[name] = client
        response = client.post("/api/crawler-accounts", json={"platform":"dy","display_name":name})
        assert response.status_code == 200
        created[name] = response.json()["item"]["id"]
        assert auth.owns_crawler_account(users[name]["id"], created[name])
    legacy = accounts.create(platform="dy", display_name="legacy-unassigned")
    # Historical grants cannot reintroduce sharing.
    with sqlite3.connect(auth.db_path) as db:
        db.execute("INSERT INTO resource_grants VALUES (?, 'crawler-account', ?, 'manage', 'legacy', '2026-01-01', NULL)",
                   (users["bravo"]["id"], created["alice"]))
    for name, client in clients.items():
        assert [x["id"] for x in client.get("/api/crawler-accounts").json()["items"]] == [created[name]]
        for other in (created[n] for n in clients if n != name):
            assert client.patch(f"/api/crawler-accounts/{other}", json={"display_name":"hijack"}).status_code == 404
            assert client.delete(f"/api/crawler-accounts/{other}").status_code == 404
            assert client.post(f"/api/crawler-accounts/{other}/login-sessions").status_code == 404
        own = created[name]
        assert client.patch(f"/api/crawler-accounts/{own}", json={"display_name":"renamed"}).status_code == 200
        assert client.post(f"/api/crawler-accounts/{own}/login-sessions").json()["item"]["owner_user_id"] == users[name]["id"]
    assert accounts.get(legacy["id"]) is not None
    monkeypatch.setattr(accounts, "available_accounts", lambda platform: accounts.list(platform=platform))
    from backend.audit_agent import pipeline
    monkeypatch.setattr(pipeline, "job_store", SimpleNamespace(get=lambda job_id: {"owner_user_id": users[job_id]["id"]}))
    for name in clients:
        principal = Principal(users[name]["id"], role=users[name]["role"])
        assert [x["id"] for x in main._authorized_available_crawler_accounts(principal, "dy")] == [created[name]]
        assert [x["id"] for x in main._available_crawler_accounts_for_job({"platform":"dy","owner_user_id":principal.id})] == [created[name]]
        assert pipeline._authorized_crawler_account_ids(name) == frozenset({created[name]})
    assert clients["alice"].delete(f'/api/crawler-accounts/{created["alice"]}').status_code == 200
    assert accounts.get(created["alice"]) is None
    assert accounts.get(created["bravo"]) is not None


def test_public_pool_visibility_management_and_authorization(tmp_path, monkeypatch):
    auth = AuthStore(tmp_path / "auth.sqlite3")
    accounts = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(settings, "app_auth_mode", "required")
    monkeypatch.setattr(settings, "app_auth_db", auth.db_path)
    monkeypatch.setattr(settings, "task_resource_lock_dir", tmp_path / "locks")
    monkeypatch.setattr(settings, "crawler_browser_profile_root", tmp_path / "profiles")
    monkeypatch.setattr(main, "auth_store", auth)
    monkeypatch.setattr(main, "auth_service", ApplicationAuthService(auth))
    monkeypatch.setattr(main, "crawler_account_store", accounts)
    monkeypatch.setattr(
        main,
        "crawler_account_login_manager",
        SimpleNamespace(
            start=lambda account, token, **kw: {
                "account_id": account["id"],
                "owner_user_id": kw["owner_user_id"],
            },
        ),
    )
    app = FastAPI()
    app.dependency_overrides[main.principal_provider] = RequestPrincipalProvider()
    app.add_middleware(
        ApplicationAuthMiddleware,
        store=auth,
        cookie_name="public-pool-session",
    )
    app.include_router(
        create_auth_router(
            main.auth_service,
            principal_provider=main.principal_provider,
            cookie_name="public-pool-session",
            cookie_secure=False,
        )
    )
    for path, handler, method in [
        ("/api/crawler-accounts", main.create_crawler_account, "POST"),
        ("/api/crawler-accounts", main.list_crawler_accounts, "GET"),
        ("/api/crawler-accounts/{account_id}", main.update_crawler_account, "PATCH"),
        ("/api/crawler-accounts/{account_id}", main.delete_crawler_account, "DELETE"),
        (
            "/api/crawler-accounts/{account_id}/login-sessions",
            main.start_crawler_account_login,
            "POST",
        ),
    ]:
        app.add_api_route(path, handler, methods=[method])

    users = {
        name: auth.create_user(
            username=name,
            password=PASSWORD,
            role="admin" if name == "admin" else "user",
        )
        for name in ("alice", "admin")
    }
    clients = {}
    for name in users:
        client = TestClient(app)
        login = client.post(
            "/api/auth/login",
            json={"username": name, "password": PASSWORD},
        ).json()
        client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        clients[name] = client

    private_response = clients["alice"].post(
        "/api/crawler-accounts",
        json={"platform": "dy", "display_name": "Alice 私有账号"},
    )
    assert private_response.status_code == 200
    private_id = private_response.json()["item"]["id"]
    public_response = clients["admin"].post(
        "/api/crawler-accounts",
        json={
            "platform": "dy",
            "display_name": "公共采集账号",
            "platform_account_id": "shared-platform-account",
            "access_scope": "public",
        },
    )
    assert public_response.status_code == 200
    public_id = public_response.json()["item"]["id"]
    assert not auth.owns_crawler_account(users["admin"]["id"], public_id)
    accounts.save_auth_state(private_id, "private-ciphertext")
    accounts.save_auth_state(public_id, "public-ciphertext")

    alice_payload = clients["alice"].get("/api/crawler-accounts").json()
    assert [item["id"] for item in alice_payload["items"]] == [private_id]
    assert alice_payload["shared_pool"] == {
        "total": 1,
        "ready": 1,
        "busy": 0,
        "unavailable": 0,
        "by_platform": {
            "dy": {"total": 1, "ready": 1, "busy": 0, "unavailable": 0}
        },
    }
    serialized_alice_payload = str(alice_payload)
    assert public_id not in serialized_alice_payload
    assert "公共采集账号" not in serialized_alice_payload
    assert "shared-platform-account" not in serialized_alice_payload

    admin_items = clients["admin"].get("/api/crawler-accounts").json()["items"]
    assert [item["id"] for item in admin_items] == [public_id]
    assert admin_items[0]["access_scope"] == "public"
    assert admin_items[0]["can_manage"] is True

    before_rejected_create = len(accounts.list())
    rejected_create = clients["alice"].post(
        "/api/crawler-accounts",
        json={
            "platform": "dy",
            "display_name": "越权公共账号",
            "access_scope": "public",
        },
    )
    assert rejected_create.status_code == 403
    assert len(accounts.list()) == before_rejected_create
    assert clients["alice"].patch(
        f"/api/crawler-accounts/{public_id}",
        json={"display_name": "越权修改"},
    ).status_code == 404
    assert clients["alice"].post(
        f"/api/crawler-accounts/{public_id}/login-sessions"
    ).status_code == 404
    assert clients["alice"].delete(
        f"/api/crawler-accounts/{public_id}"
    ).status_code == 404
    assert clients["admin"].patch(
        f"/api/crawler-accounts/{private_id}",
        json={"display_name": "管理员也不能接管私有账号"},
    ).status_code == 404
    assert clients["admin"].patch(
        f"/api/crawler-accounts/{public_id}",
        json={"display_name": "公共账号已更新"},
    ).status_code == 200
    assert clients["admin"].post(
        f"/api/crawler-accounts/{public_id}/login-sessions"
    ).status_code == 200

    from backend.task_admission.resources import acquire_account_handle

    handle = acquire_account_handle("dy", public_id)
    assert handle is not None
    try:
        busy_summary = clients["alice"].get("/api/crawler-accounts").json()[
            "shared_pool"
        ]
        assert busy_summary["ready"] == 0
        assert busy_summary["busy"] == 1
    finally:
        handle.close()

    from backend.audit_agent import pipeline

    monkeypatch.setattr(
        pipeline,
        "job_store",
        SimpleNamespace(
            get=lambda _job_id: {"owner_user_id": users["alice"]["id"]}
        ),
    )
    monkeypatch.setattr(pipeline, "crawler_account_store", accounts)
    principal = Principal(users["alice"]["id"])
    assert [
        item["id"]
        for item in main._authorized_available_crawler_accounts(principal, "dy")
    ] == [public_id, private_id]
    assert {
        item["id"]
        for item in main._available_crawler_accounts_for_job(
            {"platform": "dy", "owner_user_id": principal.id}
        )
    } == {public_id, private_id}
    assert pipeline._authorized_crawler_account_ids("job") == frozenset(
        {public_id, private_id}
    )

    projected_job = main.redact_public_pool_crawler_account(
        {
            "id": "job-public-pool",
            "owner_user_id": users["alice"]["id"],
            "crawler_account_id": public_id,
            "crawler_account_display_name": "公共账号已更新",
            "requested_config": {"crawler_account_id": public_id},
            "effective_config": {"crawler_account_id": public_id},
            "control": {
                "execution_account": {
                    "id": public_id,
                    "display_name": "公共账号已更新",
                }
            },
            "logs": [
                {"message": "执行账号：公共账号已更新"},
                {"message": "采集完成"},
            ],
        }
    )
    assert public_id not in str(projected_job)
    assert "公共账号已更新" not in str(projected_job)
    assert projected_job["logs"] == [{"message": "采集完成"}]

    assert clients["admin"].delete(
        f"/api/crawler-accounts/{public_id}"
    ).status_code == 200
    assert accounts.get(public_id) is None
    assert accounts.get(private_id) is not None


def test_private_account_binding_cannot_be_reassigned_and_legacy_grants_are_ignored(tmp_path):
    auth = AuthStore(tmp_path / "auth.sqlite3")
    a = auth.create_user(username="alice", password=PASSWORD)
    b = auth.create_user(username="bravo", password=PASSWORD)
    auth.register_crawler_account("account-a", a["id"])
    with pytest.raises(sqlite3.IntegrityError):
        auth.register_crawler_account("account-a", b["id"])
    with pytest.raises(ValueError, match="private"):
        auth.grant_resource(user_id=b["id"], resource_type="crawler-account", resource_id="account-a", permission="manage", actor_user_id="admin")
    assert not ApplicationAuthService(auth).can_manage_crawler_account(Principal(b["id"], role="admin"), "account-a")


def test_admission_enforces_ownership_for_users_and_permanent_admin(stack):
    creation, auth, (a, b) = stack
    auth.register_crawler_account("mine", a)
    enqueue(creation.admission, a, key="own", payload={"crawler_account_id":"mine"})
    with pytest.raises(AdmissionError) as denied:
        enqueue(creation.admission, b, key="other", payload={"crawler_account_id":"mine"})
    assert denied.value.code == "CRAWLER_ACCOUNT_FORBIDDEN"
    admin = auth.create_user(username="private-admin", password=PASSWORD, role="admin")
    auth.login("private-admin", PASSWORD)
    with pytest.raises(AdmissionError):
        enqueue(creation.admission, admin["id"], key="admin-other", payload={"crawler_account_id":"mine"})
    auth.register_crawler_account("admin-own", admin["id"])
    enqueue(creation.admission, admin["id"], key="admin-own", payload={"crawler_account_id":"admin-own"})
    assert creation.admission.summary(admin["id"])["used"] == 1


def test_admission_accepts_public_account_but_rejects_other_private(
    tmp_path, monkeypatch
):
    auth = AuthStore(tmp_path / "auth.sqlite3")
    monkeypatch.setattr(settings, "app_auth_mode", "required")
    monkeypatch.setattr(settings, "app_auth_db", auth.db_path)
    alice = auth.create_user(username="pool-alice", password=PASSWORD)
    bravo = auth.create_user(username="pool-bravo", password=PASSWORD)
    auth.login("pool-alice", PASSWORD)
    auth.login("pool-bravo", PASSWORD)
    accounts = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    public_account = accounts.create(
        platform="dy",
        display_name="公共账号",
        access_scope="public",
    )
    private_account = accounts.create(
        platform="dy",
        display_name="Alice 私有账号",
    )
    auth.register_crawler_account(private_account["id"], alice["id"])
    creation = main.InvestigationCreationStore(
        tmp_path / "creation.sqlite3",
        account_db=accounts.db_path,
    )

    enqueue(
        creation.admission,
        alice["id"],
        key="public",
        payload={"crawler_account_id": public_account["id"]},
    )
    with pytest.raises(AdmissionError) as denied:
        enqueue(
            creation.admission,
            bravo["id"],
            key="other-private",
            payload={"crawler_account_id": private_account["id"]},
        )
    assert denied.value.code == "CRAWLER_ACCOUNT_FORBIDDEN"


def test_confirmation_transaction_can_validate_public_account_in_separate_db(
    tmp_path, monkeypatch
):
    auth = AuthStore(tmp_path / "auth.sqlite3")
    monkeypatch.setattr(settings, "app_auth_mode", "required")
    monkeypatch.setattr(settings, "app_auth_db", auth.db_path)
    user = auth.create_user(username="pool-confirm", password=PASSWORD)
    auth.login("pool-confirm", PASSWORD)
    accounts = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    public_account = accounts.create(
        platform="dy",
        display_name="公共账号",
        access_scope="public",
    )
    creation = main.InvestigationCreationStore(
        tmp_path / "creation.sqlite3",
        account_db=accounts.db_path,
    )

    # InvestigationCreationStore.confirm_and_queue passes its own transaction
    # connection to AdmissionStore.reserve; it has no admission_accounts attach.
    with creation._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = creation.admission.reserve(
            connection,
            owner=user["id"],
            task_id="task:public-confirm",
            kind="investigation",
            key="public-confirm",
            payload={"crawler_account_id": public_account["id"]},
        )
    assert row["charge_state"] == "CHARGED"


def test_cancel_callback_can_write_separate_account_database(
    tmp_path, monkeypatch
):
    auth = AuthStore(tmp_path / "auth.sqlite3")
    monkeypatch.setattr(settings, "app_auth_mode", "required")
    monkeypatch.setattr(settings, "app_auth_db", auth.db_path)
    user = auth.create_user(username="pool-cancel", password=PASSWORD)
    auth.login("pool-cancel", PASSWORD)
    accounts = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    public_account = accounts.create(
        platform="dy",
        display_name="公共账号",
        access_scope="public",
    )
    creation = main.InvestigationCreationStore(
        tmp_path / "creation.sqlite3",
        account_db=accounts.db_path,
    )
    enqueue(
        creation.admission,
        user["id"],
        key="cancel-with-callback",
        payload={"crawler_account_id": public_account["id"]},
    )

    def update_projection(_row):
        with sqlite3.connect(accounts.db_path) as connection:
            connection.execute(
                "UPDATE crawler_accounts SET display_name=? WHERE id=?",
                ("停止投影已更新", public_account["id"]),
            )

    cancelled = creation.admission.cancel(
        "task:cancel-with-callback",
        user["id"],
        on_cancel=update_projection,
    )
    assert cancelled["decision"] == "CANCELLED"
    assert accounts.get(public_account["id"])["display_name"] == "停止投影已更新"


def test_interactive_login_never_bypasses_owner_for_admin():
    session = SimpleNamespace(owner_user_id="alice", interactive=True, owner_token="same-token")
    for method in (False, True):
        with pytest.raises(PermissionError):
            CrawlerAccountLoginManager._check_owner(session, "same-token", owner_user_id="bravo", owner_is_admin=method)
    CrawlerAccountLoginManager._check_owner(session, "same-token", owner_user_id="alice")


def test_failed_owner_registration_does_not_publish_an_unowned_account(tmp_path, monkeypatch):
    accounts = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(settings, "app_auth_mode", "required")
    monkeypatch.setattr(main, "crawler_account_store", accounts)
    def fail(*args):
        raise RuntimeError("owner database unavailable")
    monkeypatch.setattr(main, "auth_store", SimpleNamespace(register_crawler_account=fail))
    with pytest.raises(RuntimeError, match="owner database unavailable"):
        main.create_crawler_account(main.CrawlerAccountCreateRequest(platform="dy", display_name="new"), Principal("alice"))
    assert accounts.list() == []
