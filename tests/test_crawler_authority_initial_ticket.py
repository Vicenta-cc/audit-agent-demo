"""Initial ticket reads keep their original wait without replaying initialization."""
import os

from tests.test_camoufox_direct_route import direct  # noqa: F401
from tests.test_crawler_authority_browser_busy import pinned  # noqa: F401
from tests.test_crawler_authority_resilience import pending_commit


def test_initial_ticket_waits_for_pending_commit_without_replaying_initialize(pinned):
    from backend.audit_agent.crawler_request_authority import RequestAuthority
    from backend.audit_agent.config import settings
    from tools.request_authority import AuthorityClient, ENV_FD

    route, _, existing_owner = pinned
    calls = []

    def initial_ticket():
        calls.append(True)
        return existing_owner.initial_ack()

    options = dict(existing_owner.options, initial_ack=initial_ticket)
    owner = RequestAuthority(route, crawler_root=settings.media_crawler_dir, session_options=options)
    env = dict(owner.env)
    env[ENV_FD] = str(os.dup(int(env[ENV_FD])))
    owner.bind_child(os.getpid())
    client = AuthorityClient(route, env=env)
    try:
        with pending_commit(route.scheduler_db_path, hold=1.2):
            result = client.call('initialize')
        assert result == {'trial_enabled': False}
        assert calls == [True]
        assert owner.session is not None
        assert not client.closed
    finally:
        client.close()
        owner.close()
        owner.thread.join(2)
        assert not owner.thread.is_alive()
