"""Replayable boundary repairs above the frozen P3 protocol."""
from copy import deepcopy
import json

from backend.audit_agent import geographic_p3_runtime as p3

VERSION = 'geo-source-compound-v1'
MAX_ATTEMPTS = 3


def consume(request, raw):
    """Bind an exact source compound only to a unique, parent-consistent city.

    All names come from same-field sent candidates. Neither target scope nor
    fuzzy text matching participates. P3 retains the final evidence authority.
    """
    original = deepcopy(raw)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return p3.consume(request, original), []
    bindings = []
    normalized = deepcopy(raw)
    if (isinstance(normalized, dict) and set(normalized) == {'status', 'locations'}
            and isinstance(normalized['locations'], list)):
        for location in normalized['locations']:
            if (not isinstance(location, dict) or set(location) != {'field', 'place'}
                    or not all(isinstance(location[k], str) for k in ('field', 'place'))):
                continue
            field, name = location['field'], location['place']
            source = next((s for s in request['model_input']['sources'] if s['field'] == field), None)
            if source is None or name not in source['text']:
                continue
            places = source['places']
            if any(p['name'] == name for p in places):
                continue  # Exact references, including ambiguous ones, stay under P3.
            choices = []
            for city in places:
                if city['ambiguous'] or len(city['options']) != 1:
                    continue
                entity = city['options'][0]
                if entity['level'] != 'city':
                    continue
                for province in places:
                    if province['ambiguous'] or len(province['options']) != 1:
                        continue
                    parent = province['options'][0]
                    if (parent['level'] == 'province' and parent['name'] == entity['province']
                            and name == province['name'] + city['name']):
                        choices.append((city, entity))
            identities = {json.dumps(entity, sort_keys=True, ensure_ascii=False) for _, entity in choices}
            if len(identities) != 1:
                continue
            choices.sort(key=lambda row: (-len(row[0]['name']), row[0]['name']))
            city, entity = choices[0]
            location['place'] = city['name']
            bindings.append({'field': field, 'model_place': name, 'source_place': city['name'],
                             'entity': deepcopy(entity), 'match': 'exact-source-parent-city'})
    return p3.consume(request, normalized), bindings


def attempt_prompt(request, attempts):
    if not attempts:
        return request['prompt']
    allowed = {s['field']: [p['name'] for p in s['places']] for s in request['model_input']['sources']}
    previous = attempts[-1]
    correction = {'previous_response': previous.get('raw_response'),
                  'validation_errors': previous.get('errors', ['MODEL_ATTEMPT_INTERRUPTED']),
                  'allowed_field_places': allowed}
    return (request['prompt'] + '\n上次响应未通过协议校验。以下是原响应、具体错误和允许的字段地名；'
            '请按原帖语义纠正，只返回原协议的status和locations，不增加字段。\n'
            + json.dumps(correction, ensure_ascii=False, sort_keys=True))
