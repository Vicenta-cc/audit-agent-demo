import asyncio
import json
import os
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.audit_agent import crawler_route as app
from test_managed_route_integration import managed, store_operation
from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter


def test_diagnostic_frame_before_ack_preserves_real_subprocess_handshake(tmp_path):
    snapshot = managed(tmp_path)
    store = app.RouteOperationStore(tmp_path / "ops.db")
    store_operation(store, snapshot)
    bundle = SimpleNamespace(
        snapshot=snapshot, _lock=threading.RLock(), route_operation_store=store
    )
    config, pipe = app.RouteConfigPipe(snapshot), app.RouteReceiptPipe(bundle)
    script = "from tools.bound_proxy import initialize_bound_route,emit_browser_guard_stopped,obtain_route_ack\ns=initialize_bound_route();assert emit_browser_guard_stopped(s,'PAGE_SIZES_FAILED');s.require_managed_ack(obtain_route_ack(s));print('ACK_OK')"
    child = subprocess.Popen(
        ["/Users/hose/project/zhanyun/media-crawler/.venv/bin/python", "-c", script],
        env={
            **os.environ,
            **config.env,
            **pipe.env,
            "PYTHONPATH": str(app.settings.media_crawler_dir),
            "MEDIACRAWLER_ACCOUNT_ID": "a",
        },
        pass_fds=(*config.pass_fds, *pipe.pass_fds),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        config.start(child)
        pipe.start(child)
        output, error = child.communicate(timeout=5)
        assert child.returncode == 0, error
        assert (
            output.strip() == "ACK_OK"
            and bundle.browser_guard_first_reason == "PAGE_SIZES_FAILED"
        )
    finally:
        config.close()
        pipe.close()


@pytest.mark.parametrize(
    "alteration",
    [
        {},
        {"owner_id": "wrong"},
        {"lease_version": True},
        {"url": "https://secret.invalid/password"},
        {"reason": "https://secret.invalid/password"},
    ],
)
def test_actual_receipt_pipe_validates_private_guard_diagnostic(
    tmp_path, caplog, alteration
):
    snapshot = managed(tmp_path)
    store = app.RouteOperationStore(tmp_path / "ops.db")
    store_operation(store, snapshot)
    bundle = SimpleNamespace(
        snapshot=snapshot, _lock=threading.RLock(), route_operation_store=store
    )
    pipe = app.RouteReceiptPipe(bundle)
    value = dict(
        type="browser_guard_stopped",
        operation_id=snapshot.operation_id,
        owner_id=snapshot.owner_id,
        lease_version=snapshot.lease_version,
        snapshot_digest=snapshot.snapshot_digest,
        context_id=snapshot.context_id,
        reason="DOCUMENT_GUARD_FAILED",
    )
    value.update(alteration)
    second = {**value, "reason": "DOCUMENT_GUARD_TIMEOUT"}
    script = "import os,sys;os.write(int(sys.argv[1]),sys.argv[2].encode())"
    child = subprocess.Popen(
        [
            app.settings.crawler_login_python
            or "/Users/hose/project/zhanyun/media-crawler/.venv/bin/python",
            "-c",
            script,
            str(pipe.write_fd),
            json.dumps(value) + "\n" + json.dumps(second) + "\n",
        ],
        pass_fds=pipe.pass_fds,
    )
    try:
        pipe.start(child)
        child.wait(timeout=5)
        pipe.thread.join(timeout=5)
        assert getattr(bundle, "browser_guard_first_reason", None) == (
            None if alteration else "DOCUMENT_GUARD_FAILED"
        )
        assert "secret.invalid" not in caplog.text and "wrong" not in caplog.text
        assert not hasattr(bundle, "route_error") and not hasattr(
            bundle, "account_risk_recorded"
        )
    finally:
        pipe.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [asyncio.TimeoutError(), RuntimeError("https://secret.invalid/password")]
)
async def test_firefox_guard_reports_fixed_first_cause_before_context_close(failure):
    events = []
    session = SimpleNamespace(
        record_browser_guard_stop=lambda reason: events.append(reason)
    )
    context = SimpleNamespace(
        close=AsyncMock(side_effect=lambda: events.append("closed"))
    )
    page = SimpleNamespace()
    frame = SimpleNamespace(page=page, parent_frame=None)
    request = SimpleNamespace(
        frame=frame,
        url="https://www.douyin.com/",
        is_navigation_request=lambda: True,
        resource_type="document",
        method="GET",
        redirected_from=None,
    )
    route = SimpleNamespace(
        request=request, fallback=AsyncMock(side_effect=failure), abort=AsyncMock()
    )
    adapter = DouyinLoginAdapter(context, page, bound_proxy_session=session)
    await adapter._guard_request(route)
    assert events == [
        "DOCUMENT_GUARD_TIMEOUT"
        if isinstance(failure, asyncio.TimeoutError)
        else "DOCUMENT_GUARD_FAILED",
        "closed",
    ]
