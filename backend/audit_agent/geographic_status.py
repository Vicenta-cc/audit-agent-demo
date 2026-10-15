"""Public, bounded geographic progress and completion checks (no raw evidence)."""
from copy import deepcopy
import json
import sqlite3


def classify_geographic_status(report):
    if not isinstance(report, dict):
        return {'status':'incomplete', 'reason':'geographic_state_missing'}
    if not isinstance(report.get('unfinished_queries'), list) or not isinstance(report.get('reasons'), list):
        return {'status':'incomplete', 'reason':'geographic_state_invalid'}
    if report.get('unfinished_queries'):
        return {'status':'incomplete', 'reason':'geographic_queries_unfinished'}
    for key in ('reserved','collected','handed_off','collected_not_delivered'):
        if type(report.get(key)) is not int or report[key] < 0:
            return {'status':'incomplete', 'reason':'geographic_state_invalid'}
    if report['reserved'] or 'awaiting_reconciliation' in report.get('reasons', []):
        return {'status':'incomplete', 'reason':'geographic_receipt_pending'}
    if report['collected_not_delivered'] or report['collected'] != report['handed_off']:
        return {'status':'incomplete', 'reason':'geographic_delivery_pending'}
    if any(r in report.get('reasons',[]) for r in ('paused','query_failed','capture_requires_reconciliation','collection_failed','auth_exhausted','auth_no_replacement','awaiting_account_resolution')):
        return {'status':'incomplete', 'reason':'geographic_execution_incomplete'}
    return {'status':'ready' if report['handed_off'] else 'empty',
            'reason':'geographic_complete' if report['handed_off'] else 'no_eligible_geographic_content'}


def geographic_completion_failure(report):
    classification = classify_geographic_status(report)
    if classification['status'] == 'incomplete':
        return classification['reason'], '地域采集或交付尚未完成，请先核对执行记录。'
    return None


def selection_page(service, run_id, principal, *, offset=0, limit=50, tier=None):
    """Authorize before accessing a known Job DB; never accept caller paths."""
    from backend.investigation_creation.contracts import parse_confirmed_configuration_snapshot
    from backend.investigation_creation.scope_search_plan import parse_confirmed_scope_search_plan
    from .geographic_bridge import project_plan
    from .geographic_execution_store import GeographicExecutionStore
    from backend.investigation_creation.adapters import AuditPipelineExecutionAdapter
    run = service.store.get_run(run_id, principal=principal.id)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid pagination")
    if tier is not None and tier not in {"local","province_unknown","ip_only","other_region","unresolved","conflict"}:
        raise ValueError("invalid geographic tier")
    snapshot = parse_confirmed_configuration_snapshot(run.confirmed_configuration)
    raw_plan = snapshot.execution.geographic_plan
    if not raw_plan:
        return {'run_id':run.id, 'status':'not_applicable', 'items':[], 'total':0, 'offset':offset, 'limit':limit}
    if not run.job_id:
        return {'run_id':run.id, 'status':'pending', 'items':[], 'total':0, 'offset':offset, 'limit':limit}
    if run.job_id != AuditPipelineExecutionAdapter.job_id_for_run(run.id):
        raise ValueError("geographic Run/Job identity mismatch")
    identity = raw_plan["identity"]
    if (identity["owner"], identity["draft_id"], identity["revision"]) != (run.owner_principal, run.draft_id, run.draft_revision):
        raise ValueError("geographic Run identity mismatch")
    job_store = service.run_projector.job_store
    job = job_store.get(run.job_id)
    if not job or job.get('owner_user_id') != run.owner_principal or job.get('geographic_plan') != raw_plan:
        raise ValueError('geographic Run/Job binding mismatch')
    AuditPipelineExecutionAdapter._validate_job_payload(job, snapshot.execution.model_dump(mode="json"))
    summary = deepcopy((job.get('control') or {}).get('geographic_selection'))
    if not summary:
        return {'run_id':run.id, 'status':'pending', 'items':[], 'total':0, 'offset':offset, 'limit':limit}
    p2 = parse_confirmed_scope_search_plan(raw_plan, expected_identity=raw_plan['identity'], expected_digest=raw_plan['plan_digest'])
    expected = project_plan(p2, execution_id=summary['execution_id'],run_id=run.id,job_id=run.job_id,config_hash=snapshot.config_hash)
    if summary.get('plan_digest') != expected['plan_digest']:
        raise ValueError('geographic summary binding mismatch')
    with sqlite3.connect(job_store.db_path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        row = db.execute('SELECT snapshot FROM geographic_executions WHERE execution_id=?',(expected['execution_id'],)).fetchone()
    if not row:
        raise ValueError('geographic state missing')
    state = json.loads(row[0])
    validator = object.__new__(GeographicExecutionStore)
    validator.expected_binding = expected['binding'] | expected['runtime_binding']
    validator.expected_plan_digest = expected['plan_digest']
    validator._validate_snapshot(state,expected['execution_id'])
    rows=[]
    for query in state['queries'].values():
        for occurrence in query['occurrences']:
            geo = occurrence.get('geo',{}).get('result',{})
            geo_tier = geo.get('geo_tier')
            if tier is not None and geo_tier != tier:
                continue
            score = occurrence.get('risk',{}).get('score',{})
            rows.append({key:occurrence.get(key) for key in ('occurrence_id','query_id','content_key','rank','source_keyword')} |
                        {'geo_tier':geo_tier,'geo_status':geo.get('status'),
                         'risk_band':score.get('band'), 'risk_score':score.get('score')})
    return {'run_id':run.id, **classify_geographic_status(summary),
            'summary':{k:summary.get(k) for k in ('candidate_occurrences','reserved','collected','handed_off','collected_not_delivered','unfinished_queries','reasons')},
            'items':rows[offset:offset+limit], 'total':len(rows), 'offset':offset,'limit':limit}
