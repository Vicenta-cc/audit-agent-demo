"""Small projections bound to a single committed geographic query."""
from copy import deepcopy

from .scoped_selection import selection_entry


def _public_candidate(occurrence, parts):
    # Older capture records can already contain these fields. A saved
    # evaluation overlays that part only, as in the selection drawer.
    geo_record = parts['geo'] if 'geo' in parts else occurrence.get('geo', {})
    risk_record = parts['risk'] if 'risk' in parts else occurrence.get('risk', {})
    geo = geo_record.get('result', {})
    score = risk_record.get('score', {})
    return {
        **{key: occurrence.get(key) for key in
           ('occurrence_id', 'query_id', 'content_key', 'rank', 'source_keyword')},
        'geo_tier': geo.get('geo_tier'),
        'geo_status': geo.get('status'),
        'risk_band': score.get('band'),
        'risk_score': score.get('score'),
        'evaluation_status': geo.get('evaluation_status'),
        'pending_reason': 'geographic_response_invalid'
                          if geo.get('evaluation_status') == 'invalid_response' else None,
    }


def make_query_summary(plan, qid, query):
    """Derive public progress and ranking metadata from an authoritative query.

    Ranking entries preserve the stored order. A malformed evaluated occurrence
    must still block selection at the same position as in the legacy selector.
    """
    definition = next(q for q in plan['queries'] if q['query_id'] == qid)
    captured = (query.get('capture') or {}).get('occurrences', ())
    ranked = []
    public = []
    positive = False
    conflict_count = 0
    for occurrence in query['occurrences']:
        entry = selection_entry(plan, definition, occurrence)
        ranked.append(entry)
        row = _public_candidate(occurrence, occurrence)
        if entry.get('tier') is not None:
            row['geo_tier'] = entry['tier']
        public.append(row)
        score = occurrence.get('risk', {}).get('score', {}).get('score')
        if isinstance(score, (float, int)) and score > 0:
            positive = True
        conflict_count += row['geo_tier'] in {'unresolved', 'conflict'}

    pending = []
    for oid, parts in query.get('evaluations', {}).items():
        result = parts.get('geo', {}).get('result', {})
        if result.get('evaluation_status') == 'invalid_response':
            pending.append({'query_id': qid, 'occurrence_id': oid,
                            'reason': 'geographic_response_invalid',
                            'errors': deepcopy(result.get('errors', []))})

    if query['status'] != 'evaluated' and query.get('capture'):
        public = [
            _public_candidate(o, query['evaluations'].get(o['occurrence_id'], {}))
            for o in captured
        ]
    return {
        'status': query['status'],
        'candidate_count': len(captured),
        'ranked': ranked,
        'positive_risk': positive,
        'conflict_count': conflict_count,
        'pending_evaluations': pending,
        'selection_rows': public,
    }
