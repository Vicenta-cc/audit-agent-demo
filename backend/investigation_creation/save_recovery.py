"""Read existing resource save receipts; never replay resource mutations."""
import json

from backend.resource_management.contracts import SaveResourceInput
from backend.resource_management.snapshot_refs import tool_view
from .store import RESOURCE_SAVE_TOOLS


def saved_request(manager, tool_name, arguments, *, session_id, principal):
    if tool_name not in RESOURCE_SAVE_TOOLS:
        return None
    if tool_name == 'save_resource':
        parsed = SaveResourceInput.model_validate(arguments)
        request = dict(edit_id=parsed.edit_id, expected_version=parsed.expected_version,
                       mode=parsed.mode, session_id=session_id)
        return manager.matching_save(parsed.operation_id, request=request,
                                     session_id=session_id, principal=principal)
    kind = 'ruleset' if tool_name == 'save_draft_ruleset' else 'lexicon'
    source_key = f"draft:{arguments['draft_id']}:r{arguments['expected_revision']}:{kind}"
    saved = manager.matching_save(arguments['operation_id'], source_key=source_key,
                                 session_id=session_id, principal=principal)
    return saved if saved and saved['kind'] == kind else None


def conversation_saves(application, *, session_id, turn_id, principal):
    """Only this principal's saves bound to this application turn may recover it."""
    recovered = []
    for row in application.store.conversation_resource_save_receipts(
            session_id=session_id, turn_id=turn_id, principal=principal.id):
        if row['save_arguments_json']:
            arguments = json.loads(row['save_arguments_json'])
            if application.store.tool_arguments_fingerprint(arguments) != row['arguments_fingerprint']:
                raise RuntimeError('Resource save request identity is inconsistent')
            saved = saved_request(application.resource_management, row['tool_name'], arguments,
                                  session_id=session_id, principal=principal)
        elif row['status'] == 'SUCCEEDED':
            # Pre-upgrade completed logs have no request payload. Check the
            # exact durable result identity, never infer an old STARTED outcome.
            envelope = json.loads(row['response_json'])
            data = envelope.get('data', {}) if envelope.get('status') == 'ok' else {}
            saved = application.resource_management.matching_save(
                data.get('operation_id', ''), session_id=session_id, principal=principal)
            if not saved or any(saved.get(k) != data.get(k) for k in ('resource_id', 'kind', 'version')):
                saved = None
        else:
            saved = None
        if saved:
            recovered.append(dict(receipt_id=row['receipt_id'], tool_call_id=row['tool_call_id'],
                                  tool_name=row['tool_name'], response={'status': 'ok', 'data': tool_view(saved)}))
    return recovered


def completion_answer(receipts):
    lines = []
    seen = set()
    for receipt in receipts:
        data = receipt['response']['data']
        identity = (data['kind'], data['resource_id'], data.get('version'))
        if identity in seen:
            continue
        seen.add(identity)
        label = '审核规则' if data['kind'] == 'ruleset' else '词库'
        name = data.get('resource_name') or (data.get('content') or {}).get('name' if data['kind'] == 'ruleset' else 'title')
        version = data.get('published_version', data.get('version'))
        lines.append(f"- {label}{'「' + name + '」' if name else ''}：已保存"
                     + (f"（保存版本 v{version}）" if version is not None else '') + '。')
    if not lines:
        return ''
    return '\n'.join(['回复未能完成，已核实以下保存结果：', '', *lines, '',
        '以上仅确认已保存的资源；其他步骤是否完成需另行核对。无需重复保存。'])
