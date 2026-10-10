"""Chat rounds are projections of committed creation operations, not model history.

Use the existing transactional draft/receipt relationship as the single source of
membership. Reading an older draft never changes which round is current.
"""
import json
from .errors import ConfigurationValidationError, PrincipalAccessDeniedError

TOOL_NAMES = frozenset({'copy_investigation_draft', 'list_investigation_rounds'})


def draft_ids(connection, session_id, principal):
    ids = {row[0] for row in connection.execute('''
        SELECT d.id FROM draft_creation_operations o
        JOIN investigation_drafts d ON d.id=o.draft_id
        WHERE o.session_id=? AND o.principal=? AND o.state='CREATED'
          AND d.owner_principal=? ORDER BY d.rowid
    ''', (session_id, principal, principal))}
    # Older sessions may predate creation operations. Only original successful
    # creation receipts (revision 1), never read artifacts or model prose, can
    # establish their membership. Leave historical receipts unchanged.
    for row in connection.execute('''SELECT response_json FROM investigation_creation_tool_receipts
        WHERE session_id=? AND principal=? AND status='SUCCEEDED'
        AND tool_name IN ('create_investigation_draft','use_ruleset_proposal')''', (session_id, principal)):
        try:
            response = json.loads(row[0])
            draft = response['data']['draft']
            if response['status'] == 'ok' and draft['current_revision'] == 1:
                ids.add(draft['id'])
        except (ValueError, KeyError, TypeError):
            continue
    return [row[0] for row in connection.execute('''SELECT id FROM investigation_drafts
        WHERE owner_principal=? ORDER BY rowid''', (principal,)) if row[0] in ids]


def require_member(connection, session_id, principal, draft_id):
    if draft_id not in draft_ids(connection, session_id, principal):
        raise PrincipalAccessDeniedError('草案不属于当前会话。')


def guard_new_draft(connection, operation_id, principal):
    """Called inside the draft insert transaction; competing turns cannot race."""
    operation = connection.execute(
        'SELECT session_id FROM draft_creation_operations WHERE id=? AND principal=?',
        (operation_id, principal)).fetchone()
    if operation is None:
        raise PrincipalAccessDeniedError('创建操作不属于当前会话。')
    ids = draft_ids(connection, operation[0], principal)
    if len(ids) >= 20:
        raise ConfigurationValidationError('当前会话最多支持 20 轮调查。', code='ROUND_LIMIT_REACHED')
    for draft_id in ids:
        row = connection.execute('SELECT status FROM investigation_drafts WHERE id=?', (draft_id,)).fetchone()
        if row[0] == 'DRAFT':
            raise ConfigurationValidationError('本会话已有待确认草案，请继续编辑该草案。',
                code='EDITABLE_ROUND_EXISTS', details={'draft_id': draft_id})


def project(application, session_id, principal):
    with application.store._connect() as connection:
        ids = draft_ids(connection, session_id, principal.id)
    result = []
    for number, draft_id in enumerate(ids, 1):
        draft = application.store.get_draft(draft_id, principal=principal.id)
        run = application.find_run_for_draft(draft_id, principal=principal)
        result.append({'round_number': number, 'label': f'第{number}次调查',
            'draft_id': draft_id, 'title': draft.title, 'draft_revision': draft.current_revision,
            'draft_status': draft.status.value,
            'run': run.model_dump(mode='json', exclude={'report_session_id'}) if run else None})
    return tuple(result)


GUIDANCE = '''同一会话可开展多轮调查。用 list_investigation_rounds 查看各轮草案、任务和报告；
沿用已启动调查的配置时用 copy_investigation_draft 生成独立草案，再按需编辑、展示预览，取得明确确认后启动。
已有未确认草案时继续编辑它。原任务配置与报告不变；各轮实际配置以冻结版本为准。报告查询和资源编辑可自然切换。
'''
