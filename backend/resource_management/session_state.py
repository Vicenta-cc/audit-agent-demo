"""Read-only projection of existing resource facts. No store constructors or Agents.

Each database has its own read transaction; this is deliberately not a claim of
cross-database atomicity. These observations never authorize a later mutation.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import sqlite3

from .lexicon_versions import digest
from .contracts import LexiconContent
from backend.rulesets.compiler import content_hash as rule_hash


class ResourceStateError(Exception):
    def __init__(self, code, message, status_code):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@contextmanager
def _read_database(path):
    # mode=ro also prevents accidentally creating a missing database. Do not use
    # immutable=1: live WAL changes must remain visible on subsequent requests.
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        conn.execute('SELECT name FROM sqlite_master').fetchall()  # pin read snapshot
        yield conn
    finally:
        conn.close()


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _object(value):
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError('Expected a stored JSON object')
    return parsed


def _summary(item):
    return {k: v for k, v in item.items() if k != '_content'}


class SessionResourceReader:
    def __init__(self, creation_db, resource_db, conversation_db):
        self.creation_db = Path(creation_db)
        self.resource_db = Path(resource_db)
        self.conversation_db = Path(conversation_db)

    def read(self, session_id, *, principal, limit=20, cursor=''):
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ResourceStateError('RESOURCE_STATE_INVALID', '分页数量必须为 1 至 100。', 422)
        items, issues, selection = self._load(session_id, principal)
        token = digest({'session': session_id, 'principal': principal.id, 'items': items, 'issues': issues,
                        'selection': selection})
        offset = 0
        if cursor:
            try:
                old_token, offset = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if type(offset) is not int or offset < 0:
                    raise ValueError('Invalid offset')
            except (ValueError, TypeError, UnicodeError):
                raise ResourceStateError('RESOURCE_STATE_INVALID', '无效的分页游标。', 422) from None
            if old_token != token:
                raise ResourceStateError('RESOURCE_STATE_STALE', '资源目录已经变化，请重新读取第一页。', 409)
            if offset >= len(items):
                raise ResourceStateError('RESOURCE_STATE_INVALID', '无效的分页游标。', 422)
        end = offset + limit
        next_cursor = (base64.urlsafe_b64encode(json.dumps([token, end]).encode()).decode()
                       if end < len(items) else '')
        return dict(schema_version='session-resource-state-v1', session_id=session_id,
                    status='partial' if issues else 'complete', issues=issues,
                    consistency='per_database_snapshot', revalidate_before_write=True,
                    selection=selection, snapshot=token,
                    items=[_summary(i) for i in items[offset:end]], next_cursor=next_cursor)

    def detail(self, session_id, key, *, principal):
        items, issues, _ = self._load(session_id, principal)
        item = next((i for i in items if i['key'] == key), None)
        if item is None:
            raise ResourceStateError('RESOURCE_STATE_NOT_FOUND', '资源版本不存在或不属于当前会话。', 404)
        return {**_summary(item), 'content': item.get('_content'),
                'read_status': 'partial' if issues else 'complete', 'issues': issues,
                'consistency': 'per_database_snapshot', 'revalidate_before_write': True}

    def _load(self, session_id, principal):
        try:
            with ExitStack() as stack:
                conversation = stack.enter_context(_read_database(self.conversation_db))
                self.authorize(conversation, session_id, principal)
                creation = stack.enter_context(_read_database(self.creation_db))
                resources = stack.enter_context(_read_database(self.resource_db))
                items, issues = self._project(creation, resources, session_id, principal)
                from .selections import project
                return items, issues, project(creation, items, session_id=session_id, principal=principal)
        except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as exc:
            # Do not turn a failed source into an empty list or expose database paths.
            raise ResourceStateError('RESOURCE_STATE_UNAVAILABLE', '资源状态暂时无法读取，请稍后重试。', 503) from exc

    @staticmethod
    def authorize(conversation, session_id, principal):
        session = conversation.execute(
            "SELECT owner_principal,scope_type FROM investigation_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session or session['owner_principal'] != principal.id or session['scope_type'] != 'creation':
            raise ResourceStateError('RESOURCE_STATE_NOT_FOUND', '会话不存在或无权访问。', 404)

    def _project(self, db, resources, session_id, principal):
        tables = _tables(db)
        resource_tables = _tables(resources)
        items, issues = [], set()
        known_versions = {}
        formal_ids = set()
        if 'resource_edit_origins' not in tables:
            issues.add('origins_unavailable')
        if 'resource_edit_history' not in tables:
            issues.add('history_unavailable')

        def add_formal(kind, identifier):
            if identifier:
                formal_ids.add((kind, identifier))

        def edit_versions(row, kind):
            identifier = row['proposal_id'] if kind == 'ruleset' else row['id']
            origin = (db.execute('SELECT * FROM resource_edit_origins WHERE edit_id=?',
                                 (identifier,)).fetchone() if 'resource_edit_origins' in tables else None)
            if origin and (origin['principal_id'] != principal.id or origin['kind'] != kind
                           or origin['session_id'] != session_id):
                issues.add('unverified_edit_owner')
                return
            if kind == 'lexicon' and not origin:
                issues.add('unverified_edit_owner')
                return
            source = _object(origin['source_json']) if origin else {}
            add_formal(kind, source.get('id'))
            history = dict(db.execute('SELECT version,content_json FROM resource_edit_history WHERE edit_id=?',
                                      (identifier,))) if 'resource_edit_history' in tables else {}
            if row['version'] in history and _object(history[row['version']]) != _object(row['content_json']):
                raise ValueError('Current edit differs from stored history')
            history[row['version']] = row['content_json']
            if any(v > row['version'] or v < 1 for v in history):
                raise ValueError('Edit history version exceeds current version')
            if set(history) != set(range(1, row['version'] + 1)):
                issues.add('history_incomplete')
            for version, encoded in sorted(history.items()):
                content = _object(encoded)
                hashed = rule_hash(content) if kind == 'ruleset' else digest(content)
                if kind == 'ruleset' and version == row['version'] and hashed != row['content_hash']:
                    raise ValueError('Edit hash mismatch')
                known_versions[(identifier, version)] = hashed
                items.append(dict(key=f'edit/{identifier}/{version}', type='edit_version', kind=kind,
                    id=identifier, version=version, is_current=version == row['version'],
                    title=content.get('name') or content.get('title', ''), content_hash=hashed,
                    origin_status='recorded' if source else 'unknown',
                    origin={k: source[k] for k in ('id', 'version', 'content_hash', 'published_revision_id') if k in source},
                    _content=content))

        for row in db.execute('SELECT * FROM ruleset_proposals WHERE session_id=?', (session_id,)):
            edit_versions(row, 'ruleset')
        if 'lexicon_edits' in tables:
            for row in db.execute('SELECT * FROM lexicon_edits WHERE session_id=?', (session_id,)):
                edit_versions(row, 'lexicon')
        else:
            issues.add('lexicon_edits_unavailable')

        def rule_source(value, frozen=False):
            if frozen:
                value = value.get('temporary_ruleset') or value.get('ruleset_revision') or {}
            if value.get('proposal_id'):
                identity = (value['proposal_id'], value['proposal_version'])
                if 'content' in value and rule_hash(value['content']) != value.get('content_hash'):
                    raise ValueError('Inline rule snapshot hash mismatch')
                verified = identity in known_versions and known_versions[identity] == value.get('content_hash')
                return dict(proposal_id=identity[0], proposal_version=identity[1],
                            content_hash=value.get('content_hash', ''),
                            lineage='verified_edit_version' if verified else 'unknown')
            revision = value.get('ruleset_revision_id') or value.get('id')
            if revision:
                row = resources.execute('SELECT ruleset_id FROM rule_set_revisions WHERE id=?', (revision,)).fetchone()
                if row:
                    add_formal('ruleset', row[0])
                return dict(revision_id=revision, version=value.get('expected_ruleset_version', value.get('version')),
                            content_hash=value.get('expected_ruleset_content_hash', value.get('content_hash', '')),
                            lineage='published_revision')
            return {'lineage': 'unknown'}

        def recall_source(value):
            add_formal('lexicon', value.get('lexicon_id'))
            for identifier in value.get('source_lexicon_ids') or []:
                add_formal('lexicon', identifier)
            ref = value.get('source_edit_ref')
            if ref and 'lexicon_edit_refs' in tables:
                row = db.execute('SELECT * FROM lexicon_edit_refs WHERE ref=? AND principal_id=? AND session_id=?',
                                 (ref, principal.id, session_id)).fetchone()
                if row and known_versions.get((row['edit_id'], row['version'])) == row['content_hash']:
                    inline = value.get('lexicon_content')
                    if inline is not None and digest(LexiconContent.model_validate(inline).storage_dict()) != row['content_hash']:
                        raise ValueError('Lexicon origin differs from adopted content')
                    return dict(strategy=value.get('strategy'), edit_id=row['edit_id'],
                                edit_version=row['version'], content_hash=row['content_hash'],
                                lineage='verified_edit_version')
            # Legacy/direct word lists do not identify their original edit.
            return dict(strategy=value.get('strategy', 'unknown'), lexicon_id=value.get('lexicon_id', ''),
                        content_hash=digest(value), lineage='formal_resource' if value.get('lexicon_id') else 'unknown')

        # Use durable business receipts/approvals, not model text, UI focus or the
        # latest edited proposal, to establish workspace membership of a draft.
        draft_ids = set()
        if 'draft_creation_operations' in tables:
            draft_ids.update(r[0] for r in db.execute(
                'SELECT draft_id FROM draft_creation_operations WHERE session_id=? AND principal=? AND draft_id IS NOT NULL',
                (session_id, principal.id)))
        if 'ruleset_proposal_approvals' in tables:
            draft_ids.update(r[0] for r in db.execute(
                'SELECT draft_id FROM ruleset_proposal_approvals WHERE session_id=?', (session_id,)))
        if 'investigation_creation_tool_receipts' in tables:
            for row in db.execute("""SELECT response_json FROM investigation_creation_tool_receipts
                WHERE session_id=? AND principal=? AND status='SUCCEEDED'
                AND tool_name IN ('create_investigation_draft','use_ruleset_proposal')""", (session_id, principal.id)):
                envelope = _object(row[0])
                if envelope.get('status') == 'ok':
                    identifier = (envelope.get('data', {}).get('draft') or {}).get('id')
                    if identifier:
                        draft_ids.add(identifier)
        else:
            issues.add('draft_links_unavailable')

        for identifier in sorted(draft_ids):
            draft = db.execute('SELECT * FROM investigation_drafts WHERE id=? AND owner_principal=?',
                               (identifier, principal.id)).fetchone()
            if not draft:
                issues.add('draft_link_unavailable')
                continue
            for row in db.execute('SELECT * FROM investigation_draft_revisions WHERE draft_id=?', (identifier,)):
                config = _object(row['configuration_json'])
                recall = config.get('investigation', {}).get('recall_plan') or {}
                content = dict(judgement=config.get('judgement', {}), recall_plan=recall)
                items.append(dict(key=f'draft/{identifier}/{row["revision"]}', type='draft_revision',
                    id=identifier, version=row['revision'], is_current=row['revision'] == draft['current_revision'],
                    title=row['title'], content_hash=digest(config),
                    ruleset=rule_source(content['judgement']), lexicon=recall_source(recall), _content=content))
            for row in db.execute('SELECT * FROM investigation_runs WHERE draft_id=? AND owner_principal=?',
                                  (identifier, principal.id)):
                config = _object(row['confirmed_configuration_json'])
                if config.get('draft_id') != identifier or config.get('draft_revision') != row['draft_revision']:
                    raise ValueError('Run snapshot identity mismatch')
                # Never return execution/account/provider configuration from this API.
                content = {k: config[k] for k in ('temporary_ruleset', 'ruleset_revision', 'recall_plan',
                                                  'resolved_search_terms') if k in config}
                items.append(dict(key=f'run/{row["id"]}', type='run', id=row['id'], draft_id=identifier,
                    draft_revision=row['draft_revision'], status=row['status'], title=config.get('title', ''),
                    content_hash=digest(config), ruleset=rule_source(config, frozen=True),
                    lexicon=recall_source(config.get('recall_plan') or {}),
                    report_version_id=row['report_version_id'], _content=content))

        if 'resource_save_receipts' not in resource_tables:
            issues.add('save_receipts_unavailable')
        else:
            for row in resources.execute('SELECT * FROM resource_save_receipts WHERE principal_id=? AND session_id=?',
                                         (principal.id, session_id)):
                saved = _object(row['result_json'])
                # Whitelist receipt fields; do not emit reusable capability handles.
                fields = ('kind', 'edit_id', 'edit_version', 'edit_content_hash', 'resource_id',
                          'resource_version', 'version', 'revision_id', 'content_hash', 'draft_id', 'draft_revision')
                item = {k: saved[k] for k in fields if k in saved}
                item.update(key=f'save/{row["operation_id"]}', type='save_receipt', operation_id=row['operation_id'])
                if item.get('edit_id'):
                    identity = (item['edit_id'], item.get('edit_version'))
                    item['edit_link'] = ('verified' if identity in known_versions
                        and known_versions[identity] == item.get('edit_content_hash') else 'unknown')
                    if item['edit_link'] == 'unknown':
                        issues.add('save_edit_link_unverified')
                items.append(item)
                add_formal(saved.get('kind'), saved.get('resource_id'))

        for kind, identifier in sorted(formal_ids):
            if kind == 'ruleset':
                row = resources.execute("""SELECT * FROM rule_sets WHERE id=? AND status!='deleted'
                    AND owner_id IN (?, 'system')""", (identifier, principal.id)).fetchone()
                if row:
                    body = _object(row['draft_content_json'])
                    if rule_hash(body) != row['draft_content_hash']:
                        raise ValueError('Formal draft hash mismatch')
                    items.append(dict(key=f'formal/ruleset/{identifier}/draft/{row["draft_revision"]}',
                        type='formal_resource', kind=kind, stage='draft', is_current=True,
                        id=identifier, version=row['draft_revision'], content_hash=row['draft_content_hash'],
                        published_revision_id=row['published_revision_id'], published_version=row['published_version'],
                        title=body.get('name', ''), editable=row['owner_id'] == principal.id, _content=body))
                    for revision in resources.execute('SELECT * FROM rule_set_revisions WHERE ruleset_id=?', (identifier,)):
                        content = _object(revision['snapshot_json'])
                        if rule_hash(content) != revision['content_hash']:
                            raise ValueError('Published rule hash mismatch')
                        items.append(dict(key=f'formal/ruleset/{identifier}/published/{revision["version"]}',
                            type='formal_resource', kind=kind, stage='published',
                            id=identifier, version=revision['version'], draft_revision=revision['draft_revision'],
                            revision_id=revision['id'], content_hash=revision['content_hash'],
                            is_current=revision['id'] == row['published_revision_id'],
                            title=content.get('name', ''), editable=False, _content=content))
            elif kind == 'lexicon':
                row = resources.execute("""SELECT * FROM lexicon_categories
                    WHERE id=? AND owner_id IN (?, 'system')""", (identifier, principal.id)).fetchone()
                if row:
                    versions = resources.execute('SELECT * FROM lexicon_content_versions WHERE category_id=? ORDER BY version',
                                                 (identifier,)).fetchall()
                    if not versions:
                        issues.add('formal_history_unavailable')
                    for revision in versions:
                        body = _object(revision['content_json'])
                        if digest(body) != revision['content_hash']:
                            raise ValueError('Stored lexicon hash mismatch')
                        current = revision['version'] == versions[-1]['version']
                        items.append(dict(key=f'formal/lexicon/{identifier}/{revision["version"]}',
                            type='formal_resource', kind=kind, stage='stored', is_current=current,
                            id=identifier, version=revision['version'], content_hash=revision['content_hash'],
                            title=body.get('title', ''), editable=current and row['owner_id'] == principal.id, _content=body))
            else:
                raise ValueError('Unknown resource kind')
            if not row:
                issues.add('formal_source_unavailable')
        return sorted(items, key=lambda i: i['key']), sorted(issues)
