import base64
import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend.product_help.manual import load_manual, read_manual
from backend.product_help.tools import ProductReadTools, ManualInput, ReportsInput
from backend.investigation_creation.principal import Principal


def test_manual_uses_shipped_sections_without_base64_and_bounded_images():
    version, sections, images = load_manual()
    assert len(sections) == 12 and len(images) == 19
    assert all(key == __import__('hashlib').sha256(value).hexdigest() for key, value in images.items())
    result = read_manual('第一次用，怎么发起调查？')
    assert {s['section_id'] for s in result['sections']} >= {'s1', 's5'}
    assert sum(len(s['images']) for s in result['sections']) <= 2
    assert 'data:image' not in json.dumps(result)
    assert result['manual_version'] == version
    assert '示意' in json.dumps(result, ensure_ascii=False)
    assert len(json.dumps(result, ensure_ascii=False)) < 12000
    assert read_manual()['directory']
    assert not read_manual('毫不相关的天文问题')['matched']
    with pytest.raises(ValueError): read_manual(section_id='../../secret')
    with pytest.raises(ValueError): ManualInput(url='https://example.com')


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / 'reports.db'
    def connect():
        db = sqlite3.connect(path); db.row_factory = sqlite3.Row; return db
    with connect() as db:
        db.executescript('CREATE TABLE reports(id TEXT,task_id TEXT); CREATE TABLE report_versions(id TEXT,report_id TEXT,title TEXT,version_number INTEGER,published_at TEXT,status TEXT);')
        for i in range(25):
            db.execute('INSERT INTO reports VALUES(?,?)', (f'r{i}', f'task:{i}'))
            db.execute('INSERT INTO report_versions VALUES(?,?,?,?,?,?)', (f'report-version:{i}', f'r{i}', '同名报告', 1, f'2026-10-{i+1:02}T12:00:00Z', 'published'))
        db.execute("INSERT INTO report_versions VALUES('report-version:new','r0','新版',2,'2026-10-26T12:00:00Z','published')")
        db.execute("INSERT INTO report_versions VALUES('report-version:draft','r0','草稿',3,'2026-10-27','draft')")
    allowed = set(range(24))
    reader = ProductReadTools(report_store=SimpleNamespace(_connect=connect),
        can_read=lambda p, row: p.id == 'owner' and int(row['task_id'].split(':')[1]) in allowed,
        principal_for_session=lambda _: Principal('owner'))
    return reader, allowed


def test_catalog_pages_all_authorized_latest_reports_without_binding_or_title_dedup(catalog):
    reader, _ = catalog; owner = Principal('owner'); items = []; cursor = ''
    for _ in range(3):
        page = reader.reports(owner, cursor=cursor)
        assert page['total_count'] == 24
        items.extend(page['reports']); cursor = page['cursor']
    assert len(items) == 24 and len({i['url'] for i in items}) == 24
    assert items[0]['title'] == '新版'
    assert not page['has_more'] and page['remaining_count'] == 0
    assert 'report-version%3Adraft' not in json.dumps(items)
    assert all('/report?report=report-version%3A' in i['url'] for i in items)
    assert reader.reports(Principal('other'))['total_count'] == 0


def test_catalog_cursor_cannot_cross_principal_or_permissions(catalog):
    reader, allowed = catalog; owner = Principal('owner'); cursor = reader.reports(owner)['cursor']
    with pytest.raises(ValueError): reader.reports(Principal('other'), cursor=cursor)
    allowed.remove(3)
    with pytest.raises(ValueError): reader.reports(owner, cursor=cursor)
    for value in ['invalid', base64.urlsafe_b64encode(b'{}').decode()]:
        with pytest.raises(ValueError): reader.reports(owner, cursor=value)
    for args in [{'limit': 0}, {'limit': 21}, {'limit': True}, {'owner_id': 'other'}]:
        with pytest.raises(ValueError): ReportsInput(**args)


def test_tool_registration_in_creation_and_report_modes(monkeypatch):
    from hermes_m0 import plugin
    from backend.product_help.tools import INPUTS
    for mode in ['HERMES_INVESTIGATION_CREATION_MODE', 'HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE']:
        for name in ['HERMES_INVESTIGATION_CREATION_MODE', 'HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE', 'HERMES_INVESTIGATION_REAL_REPORT_MODE', 'HERMES_INVESTIGATION_REPORT_TASK_MODE', 'HERMES_INVESTIGATION_TASK_MODE']:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(mode, '1')
        registered = []
        plugin.register(SimpleNamespace(register_middleware=lambda *a: None, register_tool=lambda **kw: registered.append(kw['name'])))
        assert set(INPUTS) <= set(registered)
        assert len(registered) == len(set(registered))


def test_public_projection_preserves_only_supported_product_links():
    from backend.investigation_creation.public_answer import redact_creation_internal_references
    image = '/api/user-manual/images/' + 'a' * 64
    report = '/investigation/investigation-run%3A' + 'b' * 32 + '/report?report=report-version%3A' + 'c' * 32
    content = f'![示意图]({image})\n[查看报告]({report})'
    assert redact_creation_internal_references(content)[0] == content
    for value in ['a' * 64, 'b' * 32, '/Users/private/secret']:
        assert value not in redact_creation_internal_references(value)[0]
    assert '内部标识已隐藏' in redact_creation_internal_references('[外部](https://example.com/' + 'a' * 64 + ')')[0]


def test_catalog_is_owner_only_even_for_admin_and_legacy_grantee(catalog):
    from backend.api.reporting import can_read_m3_report
    reader, _ = catalog
    grants = {('guest', 'report-version', 'report-version:2', 'read')}
    auth = SimpleNamespace(store=SimpleNamespace(has_grant=lambda *args: args in grants))
    runs = SimpleNamespace(
        admission=SimpleNamespace(for_task=lambda task: {'owner_id': 'owner'} if task == 'task:0' else None),
        owner_principals_for_report=lambda **kw: ('owner',) if kw['task_id'] == 'task:1' else ('admin',) if kw['task_id'] == 'task:2' else (),
    )
    reader.can_read = lambda principal, row: can_read_m3_report(runs, principal,
        report_version_id=row['id'], task_id=row['task_id'], auth_service=auth)
    assert reader.reports(Principal('owner'))['total_count'] == 2
    assert reader.reports(Principal('guest'))['total_count'] == 0
    assert reader.reports(Principal('unrelated'))['total_count'] == 0
    assert reader.reports(Principal('admin', role='admin'))['total_count'] == 1
    grants.clear()
    assert reader.reports(Principal('guest'))['total_count'] == 0


def test_dispatch_rejects_invalid_session_before_reading_reports(catalog, monkeypatch):
    from backend.product_help import tools
    reader, _ = catalog
    def denied(_): raise ValueError('当前会话不可用。')
    reader.principal_for_session = denied
    monkeypatch.setattr(tools, '_service', reader)
    for name in tools.INPUTS:
        result = json.loads(tools.dispatch(name, {}, 'invalid-session'))
        assert result['status'] == 'error'
        assert 'data' not in result


def test_report_delivery_repairs_missing_links_without_replacing_model_prose():
    from backend.product_help.delivery import complete_report_links
    url = '/investigation/task%3A1/report?report=report-version%3A' + 'a' * 32
    url2 = url.replace('a' * 32, 'b' * 32)
    old = {'role': 'tool', 'name': 'list_completed_reports', 'content': json.dumps({'status': 'ok', 'data': {'reports': [{'title': '旧报告', 'url': url2}]}})}
    current = {'role': 'tool', 'name': 'list_completed_reports', 'content': json.dumps({'status': 'ok', 'data': {'reports': [{'title': '同名报告', 'url': url}, {'title': '同名报告', 'url': url2}]}})}
    answer = '这两份供你参考：\n\n| 同名报告 | v1 |\n| 同名报告 | v2 |'
    transcript = [old, current, {'role': 'assistant', 'content': answer}]
    result, updated = complete_report_links(answer, transcript, 1)
    assert result.startswith('这两份供你参考：')
    assert result.count('](') == 2 and url in result and url2 in result
    assert '旧报告' not in result
    assert updated[-1]['content'] == result
    assert complete_report_links(result, updated, 1)[0] == result
    assert complete_report_links(answer, transcript, 2)[0] == answer
