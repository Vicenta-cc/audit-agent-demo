"""Real isolated P1/P2 setup and controlled ports for offline joint evidence."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from backend.audit_agent.scoped_execution import ScopedExecution
from backend.audit_agent.scoped_selection_store import Store
from experiments.geographic_execution_plan.authority import capture_fixture_authority
from experiments.geographic_execution_plan.storage import save_plan, load_plan
from backend.investigation_creation.scope_search_plan import build_scope_search_plan
from .adapter import BoundAdapter, project_plan

QUERIES = ('克拉玛依市包过', '包过', '北京路欠薪', '克拉玛依市 包过')
MANIFEST = Path(__file__).parent / 'artifacts/joint-20261015-01/INPUT_MANIFEST.json'


def make_real_bundle(stack, content, tmp_path, case_id, *, target='克拉玛依',
                     policy='local_first_with_supplement', task_limit=1,
                     formal_rules=False, formal_lexicon=False, ui=False,
                     collect_comments=True, max_comments=20):
    # These helpers create real temporary stores/resources and genuine source
    # records. They never call the formal confirmation/Run creation path.
    from test_unified_draft_configuration import prepare, P
    from test_geographic_draft_configuration import flow, change
    from test_geographic_scope_selection import select
    first, ctx, manager, rule, _, args = prepare(stack, content, formal_rules=formal_rules)
    word = manager.create_lexicon({'title': '离线联调原词', 'entries': [
        {'id': 'joint-term-' + str(i), 'term': q, 'kind': 'main'}
        for i, q in enumerate(QUERIES)]}, **ctx)
    if formal_lexicon:
        saved = manager.save(word['edit_id'], 1, 'new', operation_id='joint-lex-save', **ctx)
        word = manager.read('lexicon', saved['resource_id'], principal=P)
    args.update(platform='dy', lexicon_ref=word['resource_ref'], scope_decision='apply',
                relevance_scope_change=change(target, '调查' + target, policy))
    result, _ = flow(stack, first['session_id'], args, '调查' + target)
    assert result['status'] == 'ok', result
    draft_id = result['data']['draft']['id']
    if ui:
        select(stack, first['session_id'], draft_id, target, revision=1)
    settings = stack['app_service'].resource_service.task_settings
    current = settings.get()
    settings.save({**current['parameters'], 'max_total_notes': task_limit,
                   'collect_comments': collect_comments, 'max_comments': max_comments},
                  expected_revision=current['revision'])
    from backend.audit_agent.config import settings as fixture_settings
    # Explicit offline select profile, captured before P2 freezes it. The adapter
    # itself still rejects off/compare and never changes a received plan.
    with patch.object(fixture_settings, 'triage_mode', 'select'):
        authority = capture_fixture_authority(stack['app_service'], draft_id, principal=P,
            session_id=first['session_id'], fixture_id='fixture:joint:' + case_id)
    identity = deepcopy(authority['identity'])
    plan = build_scope_search_plan(authority, expected_identity=identity)
    path = (tmp_path / 'p2-plan.sqlite3').resolve()
    expected_digest = save_plan(path, plan)
    restored = load_plan(path, expected_identity=identity, expected_digest=expected_digest)
    assert restored == plan
    assert [q.query for q in restored.execution_queries] == list(QUERIES)
    source_digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    execution_id = 'fixture:joint:' + case_id
    bundle = project_plan(restored, plan_path=path, authority=authority,
        expected_identity=identity, expected_p2_digest=expected_digest,
        execution_id=execution_id, source_manifest_digest=source_digest, manifest_path=MANIFEST)
    expected = dict(expected_identity=identity, expected_p2_digest=expected_digest,
        expected_binding_digest=bundle['binding']['binding_digest'], execution_id=execution_id,
        source_manifest_digest=source_digest, manifest_path=str(MANIFEST))
    return bundle, expected, authority


def row(key, text='克拉玛依市欠薪', *, band='weak', place='克拉玛依市', rank=1,
        comments=(), **fields):
    return {'key': key, 'rank': rank, 'item': {'post_id': key, 'desc': text, **fields},
            'comments': list(comments),
            'risk_response': {'suspicion': band, 'reason': '离线人工响应', 'matched_terms': [], 'locations': []},
            'geo_response': {'status': 'located', 'locations': [{'field': 'desc', 'place': place}]}
            if place else {'status': 'no_location', 'locations': []}}


class JointHarness:
    """Inject ports only; selection, ranking, transactions and recovery stay P4."""
    def __init__(self, bundle, expected, db_path, rows):
        self.bundle, self.expected = deepcopy(bundle), deepcopy(expected)
        self.adapter = BoundAdapter(self.bundle, **self.expected)
        self.plan = self.adapter.plan
        self.db_path = Path(db_path)
        self.store = Store(self.db_path)
        self.rows = deepcopy(rows)
        self.calls, self.risks, self.geos = [], {}, {}
        self.effects, self.deliveries = {}, {}
        self.failure, self.risk_override = {}, {}
        self.crash_at = None
        self.stop_now = False
        self.review_change = None
        self.inputs = {}
        self.occurrences = {}
        for i, query in enumerate(self.plan['queries']):
            occurrences = []
            for entry in self.rows.get(i, []):
                item = {**entry['item'], 'source_keyword': query['query']}
                occurrence = self.adapter.occurrence(query['query_id'], entry['key'], entry['rank'],
                                                     item, entry['comments'])
                occurrences.append(occurrence)
                self.inputs[occurrence['occurrence_id']] = entry
            self.occurrences[query['query_id']] = occurrences

    def capture(self, plan, query):
        self.calls.append(['capture', query['query_id']])
        return {'account_id': 'fixture:capture-account',
                'occurrences': deepcopy(self.occurrences[query['query_id']])}

    def risk(self, plan, query, occurrence):
        oid = occurrence['occurrence_id']
        self.calls.append(['risk', oid])
        risk = self.risk_override.get(oid)
        if risk is None:
            risk = self.adapter.risk(occurrence, self.inputs[oid]['risk_response'])
        self.risks[oid] = deepcopy(risk)
        return deepcopy(risk)

    def geo(self, plan, query, occurrence):
        oid = occurrence['occurrence_id']
        self.calls.append(['geo', oid])
        result = self.adapter.geo(occurrence['candidate'], self.inputs[oid]['geo_response'])
        self.geos[oid] = deepcopy(result)
        return result

    def detail(self, occurrence, attempt, *, account_id, content_callback):
        key = occurrence['content_key']
        self.calls.append(['detail', key, account_id])
        before = len(self.deliveries)
        content_callback({'premature': key})
        assert len(self.deliveries) == before
        if key in self.failure:
            value = self.failure.pop(key)
            if isinstance(value, Exception):
                raise value
            return value
        candidate = deepcopy(occurrence['candidate'])
        if self.review_change:
            raw = deepcopy(candidate['raw_source'])
            raw['post'].update(self.review_change.get('item', {}))
            if 'comments' in self.review_change:
                raw['comments'] = deepcopy(self.review_change['comments'])
            candidate = self.adapter.normalize(raw)
        self.effects[attempt['attempt_id']] = deepcopy(candidate)
        return candidate

    def review(self, plan, occurrence, content):
        self.calls.append(['review', occurrence['content_key']])
        response = (self.review_change or {}).get('geo_response',
                   self.inputs[occurrence['occurrence_id']]['geo_response'])
        result = self.adapter.geo(content, response)
        self.geos['review:' + occurrence['occurrence_id']] = deepcopy(result)
        return result

    def deliver(self, content, *, idempotency_key, binding):
        self.calls.append(['deliver', binding['content_key']])
        self.deliveries.setdefault(idempotency_key, deepcopy(content))
        return {**binding, 'idempotency_key': idempotency_key, 'status': 'accepted'}

    def rotate(self, query, account, error):
        self.calls.append(['rotate', query['query_id'], account, type(error).__name__])
        return 'fixture:second-account'

    def checkpoint(self, stage, payload):
        self.calls.append(['checkpoint', stage, deepcopy(payload)])
        if self.crash_at == stage:
            self.crash_at = None
            raise RuntimeError('joint interruption:' + stage)

    def runner(self):
        # Every recreation revalidates the independent upstream bindings.
        self.adapter = BoundAdapter(self.bundle, **self.expected)
        self.store = Store(self.db_path)
        return ScopedExecution(self.store, self.adapter.plan, capture=self.capture, risk=self.risk,
            geo=self.geo, validate_geo=self.adapter.validate_geo, detail=self.detail, review=self.review,
            deliver=self.deliver, account=lambda query: 'fixture:detail-account',
            rotate=self.rotate,
            stop=lambda: self.stop_now, checkpoint=self.checkpoint)

    def evidence(self, case_id, result=None, **extra):
        body = {'case_id': case_id, 'fixture_only': True, 'bundle': self.bundle,
                'expected': self.expected, 'rows': self.rows, 'calls': self.calls,
                'risk_records': self.risks, 'geo_records': self.geos,
                'effects': self.effects, 'deliveries': self.deliveries,
                'result': result, 'snapshot': self.store.load(self.plan['execution_id'], self.plan['plan_digest']),
                'assertions_passed': True, **extra}
        if os.environ.get('GEO_JOINT_OUTPUT'):
            path = Path(os.environ['GEO_JOINT_OUTPUT']) / 'cases' / (case_id + '.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str) + '\n')
        return body
