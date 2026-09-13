from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
from uuid import uuid4

from backend.audit_agent.lexicon_store import LexiconCategoryReferenceConflictError
from backend.rulesets.contracts import RuleSetContent
from backend.rulesets.compiler import compile_ruleset_content, content_hash
from backend.rulesets.trial_profiles import TRIAL_BUNDLES, apply_trial_profile
from .contracts import LexiconContent, LexiconEntry, ResourceError
from .lexicon_versions import canonical, digest, content as lexicon_content, synchronize


def now():
    return datetime.now(timezone.utc).isoformat()


def require_version(actual, expected):
    if actual != expected:
        raise ResourceError('内容已经变化，请读取并检查差异后再修改。', code='RESOURCE_VERSION_CONFLICT', details={'current_version': actual, 'expected_version': expected})


class ResourceManagementService:
    def __init__(self, application):
        self.app = application
        resources = application.resource_service
        self.lexicons = resources.lexicon_store
        self.rulesets = resources.ruleset_service
        if self.lexicons.db_path.resolve() != self.rulesets.store.db_path.resolve():
            raise RuntimeError('Resource persistence requires a single resource database')
        self.db = self.lexicons.db_path
        with self.app.store._connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS resource_edit_origins (
                    edit_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, principal_id TEXT NOT NULL,
                    kind TEXT NOT NULL, source_json TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lexicon_edits (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, version INTEGER NOT NULL,
                    content_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS resource_edit_history (
                    edit_id TEXT NOT NULL, version INTEGER NOT NULL, content_json TEXT NOT NULL,
                    PRIMARY KEY(edit_id,version));
            ''')
        with self.lexicons._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS resource_save_receipts (
                principal_id TEXT NOT NULL, operation_id TEXT NOT NULL, session_id TEXT NOT NULL,
                request_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(principal_id,operation_id))''')

    def read(self, kind, resource_id='', query='', offset=0, limit=20, *, principal):
        if not resource_id:
            values = self.rulesets.list(principal=principal) if kind == 'ruleset' else self.lexicons.list_categories()
            items = []
            for value in values:
                title = value.get('name') or value.get('title') or ''
                if query.casefold() not in (title + ' ' + value['id']).casefold():
                    continue
                items.append({'id': value['id'], 'title': title, 'kind': kind, 'editable': value.get('owner_id', principal.id) == principal.id and value['id'] not in TRIAL_BUNDLES})
            return {'items': items[offset:offset + limit], 'has_more': len(items) > offset + limit}
        if kind == 'ruleset':
            item = self.rulesets.get(resource_id, principal=principal)
            body = {key: item[key] for key in RuleSetContent.model_fields}
            return {'id': resource_id, 'kind': kind, 'content': body, 'version': item['draft_revision'], 'published_revision_id': item['published_revision_id'], 'published_version': item['published_version'], 'editable': item['owner_id'] == principal.id and resource_id not in TRIAL_BUNDLES, 'content_hash': content_hash(body)}
        with self.lexicons._connect() as conn:
            conn.execute('BEGIN')
            body = lexicon_content(conn, resource_id)
            if body.get('deleted'):
                raise ResourceError('词库不存在。', code='RESOURCE_NOT_FOUND')
            revision = conn.execute('SELECT version,content_hash FROM lexicon_content_versions WHERE category_id=? ORDER BY version DESC LIMIT 1', (resource_id,)).fetchone()
            terms = self.lexicons.enabled_main_terms(resource_id, connection=conn)
            runtime_hash = self.lexicons.runtime_content_hash(resource_id, connection=conn)
            return {'id': resource_id, 'kind': kind, 'content': body, 'version': revision['version'],
                    'content_hash': revision['content_hash'], 'editable': True, 'search_terms': terms,
                    'runtime_content_hash': runtime_hash,
                    'recall_plan': {'strategy': 'existing_lexicon', 'lexicon_id': resource_id,
                                    'expected_runtime_content_hash': runtime_hash, 'enabled_main_terms': terms}}

    def save_library(self, kind, resource_id, content, expected_version, operation_id, *, principal):
        """Publish a complete editor submission in one transaction, with retry receipts."""
        parsed = RuleSetContent.model_validate(content) if kind == 'ruleset' else LexiconContent.model_validate(content)
        if kind == 'ruleset':
            self.rulesets._validate_operator_content(parsed)
            compile_ruleset_content(parsed)
        request_hash = digest({'kind': kind, 'id': resource_id, 'content': content, 'version': expected_version})
        with self.lexicons._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS resource_library_writes (principal_id TEXT, operation_id TEXT, request_hash TEXT NOT NULL, result_json TEXT NOT NULL, PRIMARY KEY(principal_id, operation_id))')
            previous = conn.execute('SELECT * FROM resource_library_writes WHERE principal_id=? AND operation_id=?', (principal.id, operation_id)).fetchone()
            if previous:
                if previous['request_hash'] != request_hash:
                    raise ResourceError('保存操作已用于其他内容。', code='RESOURCE_IDEMPOTENCY_CONFLICT')
                return json.loads(previous['result_json'])
            table = 'rule_sets' if kind == 'ruleset' else 'lexicon_categories'
            row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (resource_id,)).fetchone()
            if expected_version == 0:
                tombstone = kind == 'lexicon' and conn.execute('SELECT 1 FROM lexicon_content_versions WHERE category_id=?', (resource_id,)).fetchone()
                if row or tombstone:
                    raise ResourceError('资源已存在或已删除，请重新打开资源。', code='RESOURCE_VERSION_CONFLICT')
                source = None
            else:
                if not row or (kind == 'ruleset' and row['status'] == 'deleted'):
                    raise ResourceError('资源已删除，请返回列表。', code='RESOURCE_NOT_FOUND')
                source = {'version': expected_version}
                if kind == 'ruleset':
                    source['published_revision_id'] = row['published_revision_id']
            formal = self._save_ruleset(conn, resource_id, parsed, source, principal) if kind == 'ruleset' else self._save_lexicon(conn, resource_id, parsed, source)
            result = {'id': resource_id, 'kind': kind, 'content': parsed.model_dump(mode='json'), 'editable': True,
                      **formal, 'version': formal['resource_version'],
                      'published_revision_id': formal.get('revision_id'), 'published_version': formal['version']}
            conn.execute('INSERT INTO resource_library_writes VALUES (?,?,?,?)', (principal.id, operation_id, request_hash, canonical(result)))
            return result

    def delete_library(self, kind, resource_id, expected_version, *, principal):
        if kind == 'lexicon':
            try:
                self.lexicons.delete_category_atomically(resource_id, expected_version=expected_version)
            except KeyError as exc:
                raise ResourceError('词库已删除。', code='RESOURCE_NOT_FOUND') from exc
            except LexiconCategoryReferenceConflictError as exc:
                if getattr(exc, 'references', None):
                    names = '、'.join(ref.get('policy_name', ref.get('name', ref.get('policy_id', ''))) for ref in exc.references)
                    raise ResourceError('词库正被审核方案引用，请先解除引用：' + names, code='RESOURCE_REFERENCE_CONFLICT', details={'references': exc.references}) from exc
                raise
            return {'ok': True, 'id': resource_id}
        with self.lexicons._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM rule_sets WHERE id=?', (resource_id,)).fetchone()
            if not row or row['status'] == 'deleted':
                raise ResourceError('规则已删除。', code='RESOURCE_NOT_FOUND')
            # System templates are shared; the explicit local operator may remove
            # them from this single-user installation, but never rewrite a snapshot.
            if row['owner_id'] != principal.id and not (row['owner_id'] == 'system' and principal.id == 'local-user'):
                raise ResourceError('无权删除这套规则。', code='RESOURCE_FORBIDDEN')
            require_version(row['draft_revision'], expected_version)
            revision_ids = {r[0] for r in conn.execute('SELECT id FROM rule_set_revisions WHERE ruleset_id=?', (resource_id,))}
            targets = revision_ids | {resource_id}
            def referenced(value):
                if isinstance(value, dict):
                    return any(referenced(v) for v in value.values())
                if isinstance(value, list):
                    return any(referenced(v) for v in value)
                return isinstance(value, str) and value in targets
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_policies'").fetchone():
                names = [r['name'] for r in conn.execute('SELECT name,config_json,published_config_json FROM audit_policies')
                         if any(referenced(json.loads(r[key] or '{}')) for key in ('config_json', 'published_config_json'))]
                if names:
                    raise ResourceError('规则正被审核方案引用，请先解除引用：' + '、'.join(names), code='RESOURCE_REFERENCE_CONFLICT')
            conn.execute("UPDATE rule_sets SET status='deleted',draft_revision=draft_revision+1,updated_at=? WHERE id=?", (now(), resource_id))
        return {'ok': True, 'id': resource_id}

    def _origin(self, conn, edit_id, session_id, principal, kind, source):
        conn.execute('INSERT INTO resource_edit_origins VALUES (?,?,?,?,?,?)', (edit_id, session_id, principal.id, kind, canonical(source), now()))

    def create_lexicon(self, content, *, session_id, principal, source=None):
        body = LexiconContent.model_validate(content).storage_dict()
        identifier = 'lexicon-edit:' + uuid4().hex
        with self.app.store._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO lexicon_edits VALUES (?,?,1,?,?,?)', (identifier, session_id, canonical(body), now(), now()))
            self._origin(conn, identifier, session_id, principal, 'lexicon', source or {})
            conn.execute('INSERT INTO resource_edit_history VALUES (?,1,?)', (identifier, canonical(body)))
        return self.get_edit(identifier, session_id=session_id, principal=principal)

    def open(self, kind, resource_id, *, session_id, principal):
        resource = self.read(kind, resource_id, principal=principal)
        source = {k: resource[k] for k in ('id', 'version', 'content_hash', 'editable')}
        if kind == 'lexicon':
            return self.create_lexicon(resource['content'], session_id=session_id, principal=principal, source=source)
        source['published_revision_id'] = resource['published_revision_id']
        # Migration mappings belong to provenance, not model-editable conditions.
        body = self.rulesets._without_legacy_sources(deepcopy(resource['content']))
        parsed = self.app._validated_proposal_content(body)
        compile_ruleset_content(parsed)
        with self.app.store._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            proposal = self.app.store.create_ruleset_proposal(
                session_id=session_id, content=parsed, content_hash=content_hash(parsed), connection=conn,
            )
            self._origin(conn, proposal.proposal_id, session_id, principal, kind, source)
            conn.execute('INSERT OR IGNORE INTO resource_edit_history VALUES (?,?,?)', (proposal.proposal_id, proposal.version, canonical(body)))
        return self.get_edit(proposal.proposal_id, session_id=session_id, principal=principal)

    def get_edit(self, edit_id, *, session_id, principal):
        with self.app.store._connect() as conn:
            origin = conn.execute('SELECT * FROM resource_edit_origins WHERE edit_id=?', (edit_id,)).fetchone()
            if origin and (origin['session_id'] != session_id or origin['principal_id'] != principal.id):
                raise ResourceError('当前会话不能访问该编辑内容。', code='RESOURCE_FORBIDDEN')
            source = json.loads(origin['source_json']) if origin else {}
            if edit_id.startswith('ruleset-proposal:'):
                proposal = self.app.get_ruleset_proposal(edit_id, session_id=session_id)
                result = dict(edit_id=edit_id, kind='ruleset', version=proposal.version, content_hash=proposal.content_hash, content=proposal.content.model_dump(mode='json'), source=source, proposal_snapshot=proposal.model_dump(mode='json'))
            else:
                row = conn.execute('SELECT * FROM lexicon_edits WHERE id=? AND session_id=?', (edit_id, session_id)).fetchone()
                if not row or not origin:
                    raise ResourceError('编辑内容不存在。', code='RESOURCE_NOT_FOUND')
                body = json.loads(row['content_json'])
                result = dict(edit_id=edit_id, kind='lexicon', version=row['version'], content_hash=digest(body), content=body, source=source, search_terms=LexiconContent.model_validate(body).search_terms())
        with self.lexicons._connect() as conn:
            rows = conn.execute('SELECT result_json FROM resource_save_receipts WHERE principal_id=? AND session_id=? ORDER BY created_at', (principal.id, session_id)).fetchall()
        saved = [json.loads(r[0]) for r in rows if json.loads(r[0]).get('edit_id') == edit_id]
        applicable = saved
        result['origin_source'] = source
        if applicable:
            last = applicable[-1]
            source = {'id': last['resource_id'], 'version': last['resource_version'], 'content_hash': last['content_hash'], 'editable': True}
            if result['kind'] == 'ruleset':
                source['published_revision_id'] = last['revision_id']
            result['source'] = source
        if result['kind'] == 'lexicon':
            result['recall_plan'] = {'strategy': 'temporary_terms', 'terms': result['search_terms'], 'source_lexicon_ids': [source['id']] if source else []}
        result['saves'] = saved
        result['saved'] = any(r['edit_version'] == result['version'] and r['edit_content_hash'] == result['content_hash'] for r in saved)
        return result

    def list_edits(self, *, session_id, principal):
        with self.app.store._connect() as conn:
            ids = [r[0] for r in conn.execute('SELECT proposal_id FROM ruleset_proposals WHERE session_id=? ORDER BY created_at', (session_id,))]
            ids += [r[0] for r in conn.execute('SELECT id FROM lexicon_edits WHERE session_id=? ORDER BY created_at', (session_id,))]
        return {'items': [self.get_edit(i, session_id=session_id, principal=principal) for i in ids]}

    def update(self, edit_id, expected_version, changes, *, session_id, principal):
        current = self.get_edit(edit_id, session_id=session_id, principal=principal)
        require_version(current['version'], expected_version)
        body = deepcopy(current['content'])
        for raw in changes:
            change = raw.model_dump() if hasattr(raw, 'model_dump') else raw
            operation, target, values = change['operation'], change.get('target_id', ''), change.get('values', {})
            if operation == 'set_metadata':
                allowed = {'title', 'risk_label', 'description'} if current['kind'] == 'lexicon' else {'name', 'domain', 'audit_goal'}
                if not values or not set(values) <= allowed:
                    raise ResourceError('不支持的元数据字段；允许字段：' + ', '.join(sorted(allowed)) + '。', details={'allowed_fields': sorted(allowed)})
                body.update(values)
            elif current['kind'] == 'lexicon' and operation in {'upsert_entry', 'remove_entry'}:
                entries = body['entries']
                existing = next((e for e in entries if e['id'] == target), None)
                if operation == 'remove_entry':
                    if existing is None:
                        raise ResourceError('词条不存在。', code='RESOURCE_NOT_FOUND')
                    body['entries'] = [e for e in entries if e['id'] != target and e['parent_id'] != target]
                elif existing:
                    if 'id' in values and values['id'] != target:
                        raise ResourceError('不能修改已有词条的 ID。')
                    existing.update(values)
                else:
                    if target:
                        raise ResourceError('目标词条不存在；新增词条请省略 target_id。', code='RESOURCE_NOT_FOUND')
                    entries.append(LexiconEntry.model_validate(values).model_dump(mode='json'))
            elif current['kind'] == 'ruleset' and operation in {'update_rule', 'remove_rule', 'add_rule', 'set_exemptions'}:
                if operation == 'set_exemptions':
                    if set(values) != {'general_exemptions'}:
                        raise ResourceError('请提供 general_exemptions。')
                    body.update(values)
                    continue
                category = next((c for c in body['categories'] if c['category_id'] == target), None)
                rule = next((r for c in body['categories'] for r in c['rules'] if r['rule_id'] == target), None)
                if operation == 'add_rule' and category:
                    category['rules'].append(values)
                elif operation == 'update_rule' and rule:
                    if 'rule_id' in values and values['rule_id'] != target:
                        raise ResourceError('不能修改已有规则的 ID。')
                    rule.update(values)
                elif operation == 'remove_rule' and rule:
                    for c in body['categories']:
                        c['rules'] = [r for r in c['rules'] if r['rule_id'] != target]
                    body['categories'] = [c for c in body['categories'] if c['rules']]
                else:
                    raise ResourceError('规则或分类不存在。', code='RESOURCE_NOT_FOUND')
            else:
                raise ResourceError('操作与资源类型不匹配。')
        if current['kind'] == 'ruleset':
            updated = self.app.update_ruleset_proposal(edit_id, session_id=session_id, expected_version=expected_version, content=body)
            version = updated.version
        else:
            body = LexiconContent.model_validate(body).storage_dict()
            with self.app.store._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT version FROM lexicon_edits WHERE id=?', (edit_id,)).fetchone()
                require_version(row[0], expected_version)
                version = expected_version + (body != current['content'])
                conn.execute('UPDATE lexicon_edits SET version=?,content_json=?,updated_at=? WHERE id=?', (version, canonical(body), now(), edit_id))
                conn.execute('INSERT OR IGNORE INTO resource_edit_history VALUES (?,?,?)', (edit_id, version, canonical(body)))
        return {**self.get_edit(edit_id, session_id=session_id, principal=principal), 'previous_version': expected_version, 'changes': [c.model_dump() if hasattr(c, 'model_dump') else c for c in changes]}

    def get_save(self, operation_id, *, session_id, principal):
        with self.lexicons._connect() as conn:
            row = conn.execute('SELECT result_json FROM resource_save_receipts WHERE principal_id=? AND operation_id=? AND session_id=?', (principal.id, operation_id, session_id)).fetchone()
        return json.loads(row[0]) if row else {'status': 'not_found', 'operation_id': operation_id}

    def save(self, edit_id, expected_version, mode, operation_id, *, session_id, principal):
        request = dict(edit_id=edit_id, expected_version=expected_version, mode=mode, session_id=session_id)
        request_hash = digest(request)
        with self.lexicons._connect() as conn:
            previous = conn.execute('SELECT * FROM resource_save_receipts WHERE principal_id=? AND operation_id=?', (principal.id, operation_id)).fetchone()
        if previous:
            if previous['request_hash'] != request_hash:
                raise ResourceError('操作 ID 已用于其他保存请求。', code='RESOURCE_IDEMPOTENCY_CONFLICT')
            return json.loads(previous['result_json'])
        edit = self.get_edit(edit_id, session_id=session_id, principal=principal)
        require_version(edit['version'], expected_version)
        source = edit['source']
        if mode == 'new' and edit['saves']:
            mode = 'update'
        if mode == 'update' and (not source or not source.get('editable')):
            raise ResourceError('没有可更新的原资源；请另存一份。', code='RESOURCE_FORBIDDEN')
        identifier = source['id'] if mode == 'update' else ('ruleset.' if edit['kind'] == 'ruleset' else 'custom_') + uuid4().hex[:16]
        body = edit['content']
        if edit['kind'] == 'ruleset':
            parsed = RuleSetContent.model_validate(body)
            self.rulesets._validate_operator_content(parsed)
            compile_ruleset_content(parsed)
        else:
            parsed = LexiconContent.model_validate(body)
        # One database transaction owns formal content, version, and durable receipt.
        # Session-side projections are recoverable and never determine save success.
        with self.lexicons._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous = conn.execute('SELECT * FROM resource_save_receipts WHERE principal_id=? AND operation_id=?', (principal.id, operation_id)).fetchone()
            if previous:
                if previous['request_hash'] != request_hash:
                    raise ResourceError('操作 ID 冲突。', code='RESOURCE_IDEMPOTENCY_CONFLICT')
                return json.loads(previous['result_json'])
            # Independent retries may carry different tool-call IDs. A new save
            # of the same edit must still create at most one formal resource.
            same_edit = []
            if request['mode'] == 'new':
                for row in conn.execute('SELECT result_json FROM resource_save_receipts WHERE principal_id=? AND session_id=? ORDER BY created_at', (principal.id, session_id)):
                    receipt = json.loads(row[0])
                    if receipt['edit_id'] == edit_id:
                        same_edit.append(receipt)
            exact = next((r for r in reversed(same_edit) if r['edit_version'] == expected_version and r['edit_content_hash'] == edit['content_hash']), None)
            if exact:
                identifier = exact['resource_id']
                formal = {k: exact[k] for k in ('resource_version', 'version', 'content_hash', 'revision_id', 'search_terms') if k in exact}
            else:
                if same_edit and mode == 'new':
                    raise ResourceError('本次内容已有其他保存操作，请重新读取保存回执。', code='RESOURCE_VERSION_CONFLICT')
                if edit['kind'] == 'ruleset':
                    formal = self._save_ruleset(conn, identifier, parsed, source if mode == 'update' else None, principal)
                else:
                    formal = self._save_lexicon(conn, identifier, parsed, source if mode == 'update' else None)
            result = dict(status='saved', operation_id=operation_id, kind=edit['kind'], edit_id=edit_id, edit_version=expected_version, edit_content_hash=edit['content_hash'], resource_id=identifier, **formal)
            conn.execute('INSERT INTO resource_save_receipts VALUES (?,?,?,?,?,?)', (principal.id, operation_id, session_id, request_hash, canonical(result), now()))
        return result

    def _save_ruleset(self, conn, identifier, body, source, principal):
        if identifier in TRIAL_BUNDLES:
            apply_trial_profile({'ruleset_id': identifier, 'content_hash': content_hash(body)}, compile_ruleset_content(body))
        row = conn.execute('SELECT * FROM rule_sets WHERE id=?', (identifier,)).fetchone()
        hashed, encoded = content_hash(body), canonical(body.model_dump(mode='json'))
        stamp = now()
        if source:
            if not row or row['status'] == 'deleted' or row['owner_id'] != principal.id or row['owner_id'] == 'system':
                raise ResourceError('规则不可更新。', code='RESOURCE_FORBIDDEN')
            require_version(row['draft_revision'], source['version'])
            if row['published_revision_id'] != source.get('published_revision_id'):
                raise ResourceError('已发布版本已经变化。', code='RESOURCE_VERSION_CONFLICT')
            if row['draft_content_hash'] == hashed and row['status'] == 'published':
                return {'resource_version': row['draft_revision'], 'version': row['published_version'], 'revision_id': row['published_revision_id'], 'content_hash': hashed}
            revision, version = row['draft_revision'] + 1, row['published_version'] + 1
        else:
            revision, version = 1, 1
            conn.execute("INSERT INTO rule_sets (id,schema_version,owner_id,status,draft_revision,draft_version,draft_content_hash,draft_content_json,published_version,created_at,updated_at,created_by) VALUES (?,0,?,'draft',1,1,?,?,0,?,?,?)", (identifier, principal.id, hashed, encoded, stamp, stamp, principal.id))
        revision_id = f'ruleset-revision:{identifier}:v{version}'
        conn.execute("INSERT INTO rule_set_revisions (id,ruleset_id,schema_version,draft_revision,version,status,content_hash,snapshot_json,published_at,published_by) VALUES (?,?,0,?,?,'published',?,?,?,?)", (revision_id, identifier, revision, version, hashed, encoded, stamp, principal.id))
        conn.execute("UPDATE rule_sets SET status='published',draft_revision=?,draft_version=?,draft_content_hash=?,draft_content_json=?,published_revision_id=?,published_version=?,updated_at=? WHERE id=?", (revision, version, hashed, encoded, revision_id, version, stamp, identifier))
        return {'resource_version': revision, 'version': version, 'revision_id': revision_id, 'content_hash': hashed}

    @staticmethod
    def _save_lexicon(conn, identifier, body, source):
        if source:
            last = conn.execute('SELECT version FROM lexicon_content_versions WHERE category_id=? ORDER BY version DESC LIMIT 1', (identifier,)).fetchone()
            if not last or not conn.execute('SELECT 1 FROM lexicon_categories WHERE id=?', (identifier,)).fetchone():
                raise ResourceError('词库已删除。', code='RESOURCE_NOT_FOUND')
            require_version(last[0], source['version'])
        stamp = now()
        conn.execute('INSERT INTO lexicon_categories (id,title,risk_label,description,sort_order,created_at,updated_at) VALUES (?,?,?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,risk_label=excluded.risk_label,description=excluded.description,updated_at=excluded.updated_at', (identifier, body.title, body.risk_label, body.description, stamp, stamp))
        # Remove deleted entries only; retained rows keep statistics and stable identity.
        retained = {e.id for e in body.entries}
        old = conn.execute('SELECT id,entry_id FROM lexicon_keywords WHERE category_id=?', (identifier,)).fetchall()
        for row in old:
            if row['entry_id'] not in retained:
                conn.execute('DELETE FROM lexicon_keywords WHERE id=?', (row['id'],))
        for row in old:
            if row['entry_id'] in retained:
                conn.execute('UPDATE lexicon_keywords SET keyword=? WHERE id=?', ('__resource_swap__' + uuid4().hex, row['id']))
        for e in body.entries:
            row = conn.execute('SELECT id FROM lexicon_keywords WHERE category_id=? AND entry_id=?', (identifier, e.id)).fetchone()
            values = (e.term, e.match_type, e.platform, e.risk_level, int(e.enabled), e.note, e.id, e.parent_id, e.kind, stamp)
            if row:
                conn.execute('UPDATE lexicon_keywords SET keyword=?,match_type=?,platform=?,risk_level=?,enabled=?,note=?,entry_id=?,parent_entry_id=?,entry_kind=?,updated_at=? WHERE id=?', (*values, row['id']))
            else:
                conn.execute('INSERT INTO lexicon_keywords (keyword,match_type,platform,risk_level,enabled,note,entry_id,parent_entry_id,entry_kind,updated_at,category_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (*values, identifier, stamp))
        synchronize(conn)
        version = conn.execute('SELECT version,content_hash FROM lexicon_content_versions WHERE category_id=? ORDER BY version DESC LIMIT 1', (identifier,)).fetchone()
        return {'resource_version': version['version'], 'version': version['version'], 'content_hash': version['content_hash'], 'search_terms': body.search_terms()}
