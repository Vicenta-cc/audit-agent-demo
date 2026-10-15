"""Independent immutable experiment storage; never consult live application state."""
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from experiments.geographic_execution_plan.storage import save_blob, load_blob


def test_blob_roundtrip_is_immutable_and_independent_process(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    content = b'{"fixture":true,"rules":"original"}'
    digest = save_blob(path, content)
    assert digest == hashlib.sha256(content).hexdigest()
    assert save_blob(path, content) == digest
    assert load_blob(path, expected_digest=digest) == content
    result = subprocess.run([sys.executable, "-c",
        "from experiments.geographic_execution_plan.storage import load_blob; import sys; "
        "sys.stdout.buffer.write(load_blob(sys.argv[1],expected_digest=sys.argv[2]))", str(path), digest],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == content


def test_unknown_schema_missing_or_tampered_blob_fails(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    digest = save_blob(path, b"original")
    with pytest.raises(ValueError):
        load_blob(path, expected_digest="0" * 64)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE geo_fixture_blobs SET content=? WHERE digest=?", (b"modified", digest))
    with pytest.raises(ValueError, match="digest"):
        load_blob(path, expected_digest=digest)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM geo_fixture_blobs")
    with pytest.raises(ValueError, match="missing"):
        load_blob(path, expected_digest=digest)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="schema"):
        load_blob(path, expected_digest=digest)


def test_reject_business_or_relative_paths_and_absent_restore(tmp_path):
    for path in [Path("relative.sqlite3"), Path.cwd() / "data" / "audit.sqlite3", Path(":memory:")]:
        with pytest.raises(ValueError):
            save_blob(path, b"fixture")
    absent = tmp_path / "missing.sqlite3"
    with pytest.raises(ValueError):
        load_blob(absent, expected_digest="0" * 64)
    assert not absent.exists()


# Reuse the real isolated P1 store fixture; its service guard remains active.
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_unified_draft_configuration import P


def test_plan_process_restore_external_identity_and_embedded_copies(tmp_path, creation_stack, content, monkeypatch):
    import json
    from copy import deepcopy
    from test_geographic_execution_plan import make_authority, build, expected
    from experiments.geographic_execution_plan.storage import save_plan, load_plan
    from test_geographic_draft_configuration import flow

    authority = make_authority(creation_stack, content)
    plan = build(authority)
    original = plan.model_dump(mode="json")
    identity = expected(authority)
    path = tmp_path / "plan.sqlite3"
    digest = save_plan(path, plan)
    # Change the real live draft and in-memory authoritative snapshots after saving.
    flow(creation_stack, identity["session_id"], {"operation": "update", "draft_id": identity["draft_id"],
        "expected_revision": identity["revision"], "title": "保存后新的草案标题"}, "保存后改标题")
    application = creation_stack['app_service']
    manager = application.resource_management
    lexicon_ref = original['inputs']['configuration']['investigation']['recall_plan']['source_edit_ref']
    with application.store._connect() as conn:
        edit_id = conn.execute('SELECT edit_id FROM lexicon_edit_refs WHERE ref=?', (lexicon_ref,)).fetchone()[0]
    manager.update(edit_id, 1, [{'operation':'set_metadata', 'values':{'title':'保存后编辑器词库新版'}}],
        principal=P, session_id=identity['session_id'])
    rule_id = original['inputs']['configuration']['judgement']['proposal_id']
    manager.update(rule_id, 1, [{'operation':'set_metadata', 'values':{'name':'保存后编辑器规则新版'}}],
        principal=P, session_id=identity['session_id'])
    task_settings = application.resource_service.task_settings
    parameters = task_settings.get()
    task_settings.save({**parameters['parameters'], 'collect_comments':False}, expected_revision=0)
    # A mutable external catalog is never the restoration source: all catalog
    # content is embedded. Even accidental reads of the pinned disk files fail.
    live_catalog = tmp_path / 'live-catalog.json'
    live_catalog.write_text(json.dumps(original['inputs']['p3_inputs']['admin_catalog']['snapshot']))
    live_catalog.write_text('{"version":"new-live-version","records":[]}')
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    def no_catalog_read(self, *args, **kwargs):
        if 'dependencies/p3' in str(self) or self == live_catalog:
            raise AssertionError('restore must not consult a live disk catalog')
        return original_read_bytes(self, *args, **kwargs)
    def no_catalog_text(self, *args, **kwargs):
        if 'dependencies/p3' in str(self) or self == live_catalog:
            raise AssertionError('restore must not consult a live disk catalog')
        return original_read_text(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_bytes', no_catalog_read)
    monkeypatch.setattr(Path, 'read_text', no_catalog_text)
    authority["settings"]["revision"] = 999
    authority["risk_inputs"]["rules"]["audit_goal"] = "new live rules"
    authority["p3_inputs"]["admin_catalog"]["snapshot"]["records"] = []
    assert load_plan(path, expected_identity=identity, expected_digest=digest).model_dump(mode="json") == original
    script = (
        "import json,sys; from experiments.geographic_execution_plan.storage import load_plan; "
        "p=load_plan(sys.argv[1],expected_identity=json.loads(sys.argv[2]),expected_digest=sys.argv[3]); "
        "print(json.dumps(p.model_dump(mode='json'),ensure_ascii=False,sort_keys=True))"
    )
    result = subprocess.run([sys.executable, "-c", script, str(path), json.dumps(identity), digest],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == original
    for key, bad in [("owner", "wrong"), ("session_id", "wrong"), ("draft_id", "wrong"), ("revision", 999)]:
        with pytest.raises(ValueError):
            load_plan(path, expected_identity={**identity, key: bad}, expected_digest=digest)
    with pytest.raises(ValueError):
        load_plan(path, expected_identity=identity, expected_digest="0" * 64)
    # Embedded content deletion and future schema stay failures even with intact blob hashing.
    for mutation in ("missing_copy", "unknown_schema"):
        bad = deepcopy(original)
        if mutation == "missing_copy":
            bad["inputs"]["p3_inputs"]["admin_catalog"].pop("snapshot")
        else:
            bad["schema_version"] = "future-plan-v9"
        bad_blob = save_blob(path, json.dumps(bad).encode())
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE geo_fixture_plans SET blob_digest=? WHERE plan_digest=?", (bad_blob, digest))
        with pytest.raises(ValueError):
            load_plan(path, expected_identity=identity, expected_digest=digest)
