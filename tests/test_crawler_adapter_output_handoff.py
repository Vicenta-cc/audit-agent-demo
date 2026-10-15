"""Real worker/session lifecycle at the App's private output handoff boundary."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_adapter import MediaCrawlerAdapter
from backend.audit_agent.crawler_child_outputs import ChildOutputError, ChildOutputSpool
from backend.audit_agent import crawler_route


WORKER = r'''
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
from tools.bound_proxy import initialize_bound_route,obtain_route_ack
from tools.request_authority import AuthorityClient
parser=argparse.ArgumentParser()
parser.add_argument('--save_data_path',required=True)
parser.add_argument('--fixture-mode',required=True)
parser.add_argument('--fixture-control',required=True)
args,_=parser.parse_known_args()
route=initialize_bound_route();route.require_managed_ack(obtain_route_ack(route))
authority=AuthorityClient(route);authority.call('initialize')
root=Path(args.save_data_path);control=Path(args.fixture_control)
(root/'douyin/jsonl').mkdir(parents=True)
(root/'douyin/videos').mkdir(parents=True)
media=root/'douyin/videos/final.mp4';media.write_bytes(b'partial')
row={'aweme_id':'output-one','video_path':str(media)}
(root/'douyin/jsonl/search_contents_fixture.jsonl').write_text(
    '{"aweme_id":' if args.fixture_mode=='corrupt' else json.dumps(row))
if args.fixture_mode=='descendant':
 code=r"""import os,signal,sys,time
from pathlib import Path
media=Path(sys.argv[1]);ready=Path(sys.argv[2]);receipt=Path(sys.argv[3])
def finish(*args):
 media.write_bytes(b'final-after-group-drain')
 receipt.write_text('terminated')
 raise SystemExit(0)
signal.signal(signal.SIGTERM,finish)
ready.write_text(str(os.getpid()))
while True:time.sleep(.1)
"""
 descendant=subprocess.Popen([sys.executable,'-c',code,str(media),str(control/'descendant.pid'),str(control/'descendant.drained')])
 deadline=time.monotonic()+5
 while not (control/'descendant.pid').exists():
  if time.monotonic()>deadline:raise RuntimeError('descendant failed to start')
  time.sleep(.01)
else:media.write_bytes(b'final')
os._exit(7 if args.fixture_mode=='nonzero' else 0)
'''


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    crawler = Path(
        os.environ.get("MEDIACRAWLER_DIR")
        or os.environ.get("MEDIA_CRAWLER_DIR")
        or settings.media_crawler_dir
    )
    for name, value in {
        "media_crawler_dir": crawler,
        "crawler_camoufox_state_root": tmp_path / "state",
        "crawler_browser_profile_root": tmp_path / "logical",
        "task_resource_lock_dir": tmp_path / "locks",
        "request_scheduler_db": tmp_path / "scheduler.sqlite3",
        "crawler_browser_maintenance": False,
        "crawler_safety_enabled": True,
        "crawler_proxy_enabled": False,
        "app_auth_mode": "disabled",
        "crawler_child_output_max_bytes": 1024 * 1024,
        "crawler_child_output_max_files": 50,
    }.items():
        monkeypatch.setattr(settings, name, value)
    accounts = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    account = accounts.create(platform="dy", display_name="output fixture")
    accounts.save_auth_state(account["id"], "fixture-encrypted-auth")
    monkeypatch.setattr(
        crawler_route,
        "_run_maintenance_probe",
        lambda bundle, **kwargs: crawler_route.route_contract().ProbeReceipt(
            bundle.snapshot.proxy_id,
            bundle.snapshot.config_revision,
            "offline-output-probe",
            "203.0.113.9",
            time.time(),
        ),
    )
    bundle = crawler_route.acquire_route_execution(
        account["id"], "bounded-output", "fixture-owner", account_store=accounts
    )
    adapter = MediaCrawlerAdapter(crawler, account_store=accounts)
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER)
    control = tmp_path / "control"
    control.mkdir()
    events, spools, processes = [], [], []
    create = ChildOutputSpool.create
    bind = ChildOutputSpool.bind_process_group
    final = ChildOutputSpool.sync_final
    cleanup = ChildOutputSpool.cleanup
    drain = crawler_route._stop_probe_process_group

    def created(cls, *args, **kwargs):
        spool = create(*args, **kwargs)
        spools.append(spool)
        events.append("create")
        return spool

    def bound(spool, pid):
        result = bind(spool, pid)
        events.append("bind")
        return result

    def finalized(spool):
        # Real killpg(0), not a mocked poll result, proves the exact group exited.
        with pytest.raises(ProcessLookupError):
            os.killpg(spool._process_group, 0)
        events.append("final")
        return final(spool)

    def cleaned(spool, **kwargs):
        events.append("cleanup-failed" if kwargs.get("failed") else "cleanup")
        return cleanup(spool, **kwargs)

    def drained(process):
        processes.append(process)
        result = drain(process)
        events.append("drain")
        return result

    monkeypatch.setattr(ChildOutputSpool, "create", classmethod(created))
    monkeypatch.setattr(ChildOutputSpool, "bind_process_group", bound)
    monkeypatch.setattr(ChildOutputSpool, "sync_final", finalized)
    monkeypatch.setattr(ChildOutputSpool, "cleanup", cleaned)
    monkeypatch.setattr(crawler_route, "_stop_probe_process_group", drained)
    # Unrelated private runs must survive cleanup of this run.
    settings.crawler_camoufox_state_root.mkdir(mode=0o700)
    (settings.crawler_camoufox_state_root / "outputs").mkdir(mode=0o700)
    neighbor = settings.crawler_camoufox_state_root / "outputs" / "unrelated-run"
    neighbor.mkdir(parents=True, mode=0o700)
    (neighbor / "keep.txt").write_text("keep")

    def run(mode="normal", *, save_root=None, extra=(), executable=sys.executable):
        destination = save_root or tmp_path / "published"
        return adapter._run_command_locked(
            [
                executable,
                str(worker),
                "--save_data_path",
                str(destination),
                "--fixture-mode",
                mode,
                "--fixture-control",
                str(control),
                *extra,
            ],
            destination,
            "dy",
            100,
            None,
            None,
            account_id=account["id"],
            route_context=bundle,
        )

    item = SimpleNamespace(
        run=run,
        events=events,
        spools=spools,
        processes=processes,
        control=control,
        bundle=bundle,
        tmp_path=tmp_path,
        neighbor=neighbor,
    )
    yield item
    for process in processes:
        try:
            drain(process)
        except ProcessLookupError:
            pass
    bundle.close()
    assert (neighbor / "keep.txt").read_text() == "keep"


def assert_private_payload_reclaimed(item):
    assert len(item.spools) == 1
    private = item.spools[0].child_root
    if private.exists():
        assert {p.name for p in private.iterdir()} == {"failure-receipt.json"}, (
            "private spool leaked after failure"
        )
        receipt = json.loads((private / "failure-receipt.json").read_text())
        assert receipt["failed"] and receipt["media_removed"]
    inputs = item.spools[0]._created_inputs_root
    assert inputs is None or not inputs.exists(), (
        "private staged input leaked after failure"
    )


@pytest.mark.parametrize("mode", ["normal", "descendant"])
def test_adapter_drains_exact_group_before_final_transfer_and_cleanup(handoff, mode):
    item = handoff
    output = item.run(mode)
    assert [row["aweme_id"] for row in output.contents] == ["output-one"]
    media = Path(output.contents[0]["video_path"])
    assert media.is_relative_to(output.output_dir)
    assert media.read_bytes() == (
        b"final-after-group-drain" if mode == "descendant" else b"final"
    )
    assert (
        item.events.index("bind")
        < item.events.index("drain")
        < item.events.index("final")
        < item.events.index("cleanup")
    )
    assert not item.spools[0].child_root.exists()
    if mode == "descendant":
        assert (item.control / "descendant.drained").read_text() == "terminated"
    assert all(p.poll() is not None for p in item.processes)


def test_adapter_nonzero_exit_preserves_transferred_output_and_reaps_group(handoff):
    item = handoff
    with pytest.raises(RuntimeError, match="exit code 7"):
        item.run("nonzero")
    assert item.events.index("drain") < item.events.index("final")
    assert (
        item.tmp_path / "published/douyin/videos/final.mp4"
    ).read_bytes() == b"final"
    assert_private_payload_reclaimed(item)


def test_adapter_invalid_final_json_discards_private_media_and_keeps_failure_receipt(
    handoff,
):
    item = handoff
    with pytest.raises(ChildOutputError, match="OUTPUT_FINAL_JSONL_INCOMPLETE"):
        item.run("corrupt")
    assert (
        item.events.index("drain")
        < item.events.index("final")
        < item.events.index("cleanup-failed")
    )
    assert_private_payload_reclaimed(item)


def test_adapter_spawn_failure_reclaims_unstarted_spool(handoff):
    item = handoff
    with pytest.raises(FileNotFoundError):
        item.run(executable=str(item.tmp_path / "nonexistent-executable"))
    assert "bind" not in item.events
    assert_private_payload_reclaimed(item)


def test_adapter_bind_failure_drains_started_group_and_reclaims_spool(
    handoff, monkeypatch
):
    item = handoff
    original = ChildOutputSpool.bind_process_group

    def failed(spool, pid):
        original(spool, pid)
        raise RuntimeError("fixture post-bind failure")

    monkeypatch.setattr(ChildOutputSpool, "bind_process_group", failed)
    with pytest.raises(RuntimeError, match="fixture post-bind failure"):
        item.run()
    assert "bind" in item.events and "drain" in item.events
    assert all(p.poll() is not None for p in item.processes)
    assert_private_payload_reclaimed(item)


def test_adapter_authority_bind_failure_drains_started_group_and_reclaims_spool(
    handoff, monkeypatch
):
    from backend.audit_agent.crawler_request_authority import RequestAuthority

    item = handoff

    def denied(*args, **kwargs):
        raise RuntimeError("fixture authority bind failed")

    monkeypatch.setattr(RequestAuthority, "bind_child", denied)
    with pytest.raises(RuntimeError, match="fixture authority bind failed"):
        item.run()
    assert "bind" in item.events and "drain" in item.events
    assert all(p.poll() is not None for p in item.processes)
    assert_private_payload_reclaimed(item)


@pytest.mark.parametrize("stage", ["cache", "route", "inputs"])
def test_adapter_prelaunch_preparation_failure_reclaims_spool(
    handoff, monkeypatch, stage
):
    item = handoff
    extra = ()
    if stage == "cache":
        original = item.tmp_path / "valid-skip.txt"
        original.write_text("video-one\n")
        extra = (
            "--reusable_content_db",
            str(item.tmp_path / "unauthorized.sqlite3"),
            "--skip_aweme_ids_file",
            str(original),
        )
        error, message = RuntimeError, "CRAWLER_CACHE_DATABASE_NOT_AUTHORIZED"
    elif stage == "route":

        def denied(*args, **kwargs):
            raise RuntimeError("fixture route preparation failed")

        monkeypatch.setattr(crawler_route, "prepare_collection_child", denied)
        error, message = RuntimeError, "fixture route preparation failed"
    else:
        target = item.tmp_path / "skip.txt"
        target.write_text("one\n")
        link = item.tmp_path / "skip-link.txt"
        link.symlink_to(target)
        extra = ("--skip_aweme_ids_file", str(link))
        error, message = OSError, None
    with pytest.raises(error, match=message):
        item.run(extra=extra)
    assert "bind" not in item.events and not item.processes
    assert_private_payload_reclaimed(item)
    if stage == "cache":
        assert original.read_text() == "video-one\n"


def test_adapter_accepts_mac_system_var_alias_for_the_same_save_root(handoff):
    item = handoff
    # Exercise the actual system alias rather than a new application symlink.
    with tempfile.TemporaryDirectory(prefix="adapter-var-alias-") as directory:
        alias = Path(directory) / "published"
        if sys.platform == "darwin":
            assert str(alias).startswith("/var/"), (
                "Mac fixture requires its system /var alias"
            )
            assert alias != alias.resolve()
        output = item.run(save_root=alias)
        assert output.output_dir == alias.resolve()
        assert Path(output.contents[0]["video_path"]).is_relative_to(alias.resolve())
        assert not item.spools[0].child_root.exists()
