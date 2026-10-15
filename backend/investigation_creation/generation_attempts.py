"""Decide receipt reuse before a new resource generation, inside the store lock."""
import json

from .errors import IdempotencyConflictError

TOOLS = frozenset({'create_ruleset_proposal', 'create_lexicon_edit'})


def is_generation(row):
    return isinstance(json.loads(row['save_arguments_json'] or '{}').get('generation_request'), dict)


def may_correct(row):
    if row['status'] != 'FAILED' or row['committed_proposal_snapshot_json'] or row['recovered_response_json']:
        return False
    response = json.loads(row['response_json'] or '{}')
    error = response.get('error', {})
    details = error.get('details', {})
    return (is_generation(row) and response.get('status') == 'error'
            and error.get('code') in {'RESOURCE_GENERATION_INVALID', 'RESOURCE_GENERATION_PROVIDER_ERROR'}
            and details.get('generation_stage') in {'generation', 'validation'}
            and details.get('retryable') is True
            and details.get('resource_created') is False
            and details.get('mutation_applied') is False)


def prior_receipt(rows, *, tool_name, arguments, fingerprint):
    """Return a protected receipt, or None to admit one fresh/corrected attempt.

    Exact call identities are handled first by the caller. A successful lexicon
    generation can be followed by a distinct new lexicon in the same turn, as
    before; identical requests reuse its result. Rules retain their existing
    single logical creation per turn.
    """
    if not rows:
        return None
    generated = [row for row in rows if is_generation(row)]
    latest = rows[-1]
    if generated and generated[-1]['status'] == 'FAILED' and not arguments.get('generation_request'):
        raise IdempotencyConflictError(
            '生成失败不能改用手工正文绕过；仅可按原需求纠正 generation_request。',
            code='RESOURCE_GENERATION_RETRY_REQUIRES_REQUEST',
            details={'retryable': False, 'mutation_applied': False, 'resource_created': False})
    if tool_name == 'create_lexicon_edit':
        matches = [row for row in rows if row['arguments_fingerprint'] == fingerprint]
        successes = [row for row in matches if row['status'] == 'SUCCEEDED' or row['recovered_response_json']]
        if successes:
            return successes[-1]
        unsettled = [row for row in generated if row['status'] == 'STARTED']
        if unsettled:
            return unsettled[-1]
        if not generated or generated[-1]['status'] != 'FAILED':
            return matches[-1] if matches else None
        latest = generated[-1]
    if may_correct(latest):
        # Count durable attempts, not model-supplied retry IDs. Never reset the
        # budget when the request wording changes or a new call ID is invented.
        if len(generated) >= 2:
            raise IdempotencyConflictError('本轮生成纠正次数已用完，未创建资源。',
                code='RESOURCE_GENERATION_RETRY_EXHAUSTED',
                details={'retryable': False, 'mutation_applied': False, 'resource_created': False})
        return None
    return latest
