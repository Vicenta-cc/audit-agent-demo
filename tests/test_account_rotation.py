from backend.audit_agent.crawler_account_store import CrawlerAccountStore


def test_next_available_excludes_cooling_account(tmp_path):
    store = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    first = store.create(platform="dy", display_name="a")
    second = store.create(platform="dy", display_name="b")
    store.save_auth_state(first["id"], "cipher-a")
    store.save_auth_state(second["id"], "cipher-b")
    store.mark_cooldown(first["id"], "verify", "9999-12-31T00:00:00", failure_kind="verify")
    chosen = store.next_available("dy", exclude_id=second["id"])
    assert chosen is None
    chosen = store.next_available("dy", exclude_id=first["id"])
    assert chosen["id"] == second["id"]
