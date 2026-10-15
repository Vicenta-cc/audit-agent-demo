"""Standard-library validation and deterministic offline JSON catalog transforms.

Source authenticity and full-table coverage require review of the cited
publication or the explicitly authorized pinned third-party snapshot. Structural shape or a fixture city count cannot establish it.
No network access, external script execution, or HTML/PDF guessing occurs here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
import base64
import json
from pathlib import Path
import re
from urllib.parse import urlparse

PROVINCE_CODES = frozenset(
    '110000 120000 130000 140000 150000 210000 220000 230000 310000 '
    '320000 330000 340000 350000 360000 370000 410000 420000 430000 '
    '440000 450000 460000 500000 510000 520000 530000 540000 610000 '
    '620000 630000 640000 650000 710000 810000 820000'.split()
)
MUNICIPALITY_CODES = frozenset({'110000', '120000', '310000', '500000'})
KINDS = {
    'province': {'省', '自治区', '直辖市', '特别行政区', 'province', 'autonomous_region', 'municipality', 'special_administrative_region'},
    'prefecture': {'地级市', '自治州', '地区', '盟', 'prefecture_city', 'autonomous_prefecture', 'prefecture', 'league'},
    'county': {'市辖区', '区', '县', '自治县', '县级市', '旗', '自治旗', '特区', '林区', 'district', 'county', 'autonomous_county', 'county_city', 'banner', 'autonomous_banner', 'special_district', 'forestry_district'},
}
RECORD_KEYS = {'code', 'name', 'level', 'kind', 'province_code', 'aliases'}
IDENTITY_KEYS = ('code', 'name', 'level', 'kind', 'province_code')


class CatalogValidationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CatalogValidationError(message)


def official_url(value: str) -> bool:
    parsed = urlparse(value)
    host = parsed.hostname or ''
    return parsed.scheme == 'https' and host.endswith('.gov.cn') and not parsed.username and not parsed.password


def validate_records(records: list[dict]) -> list[dict]:
    require(isinstance(records, list) and bool(records), 'records must be a nonempty array')
    indexed = {}
    for record in records:
        require(isinstance(record, dict) and set(record) == RECORD_KEYS, 'record has missing or unknown fields')
        for field in RECORD_KEYS - {'aliases'}:
            require(isinstance(record[field], str) and bool(record[field]) and record[field] == record[field].strip(), f'{field} must be a trimmed nonempty string')
        require(bool(re.fullmatch(r'[0-9]{6}', record['code'])), 'code must be six digits')
        require(record['code'] not in indexed, f"duplicate code: {record['code']}")
        require(record['level'] in KINDS and record['kind'] in KINDS[record['level']], 'invalid administrative level/kind combination')
        aliases = record['aliases']
        require(isinstance(aliases, list) and all(isinstance(a, str) and bool(a) and a == a.strip() for a in aliases), 'aliases must be trimmed nonempty strings')
        require(len(set(aliases)) == len(aliases) and record['name'] not in aliases, 'duplicate aliases or canonical name repeated as alias')
        if record['level'] == 'province':
            require(record['code'] in PROVINCE_CODES and record['province_code'] == record['code'], 'invalid province code or self-parent')
            require((record['kind'] in {'直辖市', 'municipality'}) == (record['code'] in MUNICIPALITY_CODES), 'municipality must retain its true province type')
        else:
            require(record['code'][:2] == record['province_code'][:2], 'province code prefix mismatch')
            require(not record['code'].endswith('0000'), 'non-province has province code')
            if record['level'] == 'prefecture':
                require(record['code'].endswith('00'), 'prefecture code must end in 00')
                require(record['province_code'] not in MUNICIPALITY_CODES, 'do not create virtual municipality prefectures')
            else:
                require(not record['code'].endswith('00'), 'county code cannot use prefecture code')
        indexed[record['code']] = record
    for record in records:
        parent = indexed.get(record['province_code'])
        require(parent is not None and parent['level'] == 'province', f"missing province parent: {record['code']}")
    return sorted(records, key=lambda record: record['code'])


def national_gaps(records: list[dict]) -> list[str]:
    provinces = {r['code'] for r in records if r['level'] == 'province'}
    missing = PROVINCE_CODES - provinces
    gaps = [f"missing national province: {code}" for code in sorted(missing)]
    # Necessary shape check only. Full-table publication review plus reproducible
    # raw-to-normalized identity preservation establishes the claimed source set;
    # presence/counts alone cannot establish authoritative completeness.
    mainland_prefecture_parents = PROVINCE_CODES - MUNICIPALITY_CODES - {'710000', '810000', '820000'}
    represented = {r['province_code'] for r in records if r['level'] == 'prefecture'}
    gaps.extend(f'missing prefectures for province: {code}' for code in sorted(mainland_prefecture_parents - represented))
    return gaps


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def identities(records: list[dict]) -> list[dict]:
    return [{key: record[key] for key in IDENTITY_KEYS} for record in sorted(records, key=lambda r: r['code'])
            if record['level'] in {'province', 'prefecture'}]


TRANSFORM_VERSION = 'geographic-json-transform-v1'
CN_DIVISION_COMMIT = '88f2021ea21769bdb95d93699d8a625fcd9165ef'
CN_DIVISION_URL = f'https://github.com/kk-418/cn-division/tree/{CN_DIVISION_COMMIT}'
TRANSFORM_IDS = frozenset({'identity_records_json', 'extract_records_json', 'cn_division_snapshot_v1'})
SOURCE_KEYS = {'raw_file', 'raw_sha256', 'normalized_file', 'normalized_sha256',
               'url', 'data_date', 'retrieved_date', 'version', 'full_table_evidence_url',
               'coverage_review', 'transform'}
TRANSFORM_KEYS = {'id', 'version', 'implementation_sha256'}


def implementation_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def validate_provenance(source):
    require(isinstance(source, dict) and set(source) == SOURCE_KEYS, 'invalid source metadata fields')
    for key in SOURCE_KEYS - {'transform'}:
        require(isinstance(source[key], str) and bool(source[key].strip()), f'{key} must be a nonempty string')
    for key in ('url', 'full_table_evidence_url'):
        require(official_url(source[key]) or (source.get('transform', {}).get('id') == 'cn_division_snapshot_v1' and source[key] == CN_DIVISION_URL), f'{key} must be an official HTTPS publication URL or the explicitly reviewed fixed cn-division snapshot')
    for key in ('data_date', 'retrieved_date'):
        try:
            require(date.fromisoformat(source[key]).isoformat() == source[key], f'{key} must use YYYY-MM-DD')
        except ValueError as exc:
            raise CatalogValidationError(f'{key} must use YYYY-MM-DD') from exc
    require(source['data_date'] <= source['retrieved_date'], 'data_date cannot be after retrieved_date')
    for key in ('raw_sha256', 'normalized_sha256'):
        require(bool(re.fullmatch(r'[0-9a-f]{64}', source[key])), f'{key} must be lowercase SHA256')
    transform = source['transform']
    require(isinstance(transform, dict) and set(transform) == TRANSFORM_KEYS, 'invalid transform metadata')
    require(transform['id'] in TRANSFORM_IDS, 'unsupported transform; HTML/PDF/XML need a dedicated reviewed transform')
    require(transform['version'] == TRANSFORM_VERSION, 'unsupported transform version')
    require(transform['implementation_sha256'] == implementation_sha256(), 'transform implementation SHA256 mismatch')


def transform_raw(raw: bytes, transform_id: str) -> bytes:
    """Preserve every raw records entry; no generated or guessed identities."""
    require(transform_id in TRANSFORM_IDS, 'unsupported transform')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CatalogValidationError('unsupported raw format; only reviewed JSON transforms exist') from exc
    if transform_id == 'cn_division_snapshot_v1':
        value = {'records': transform_cn_division(value)[0]}
    require(isinstance(value, dict) and 'records' in value, 'raw JSON requires records')
    if transform_id == 'identity_records_json':
        require(set(value) == {'records'}, 'identity transform requires only records')
    require(isinstance(value['records'], list), 'raw records must be an array')
    return (json.dumps({'records': value['records']}, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def read_source(source: dict, root: Path):
    validate_provenance(source)
    raw_path = (root / source['raw_file']).resolve()
    normalized_path = (root / source['normalized_file']).resolve()
    require(raw_path != normalized_path, 'raw and normalized evidence must be separate files')
    raw = raw_path.read_bytes()
    normalized = normalized_path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == source['raw_sha256'], 'raw SHA256 mismatch')
    require(hashlib.sha256(normalized).hexdigest() == source['normalized_sha256'], 'normalized SHA256 mismatch')
    reproduced = transform_raw(raw, source['transform']['id'])
    require(reproduced == normalized, 'raw transform differs from pinned normalized bytes')
    value = json.loads(normalized)
    require(isinstance(value, dict) and set(value) == {'records'}, 'normalized JSON must contain only records')
    return validate_records(value['records'])


def validate_packaged_catalog(catalog):
    require(isinstance(catalog, dict) and set(catalog) == {'schema_version', 'version', 'sources', 'records', 'validation', 'coverage'}, 'invalid packaged catalog fields')
    require(catalog['schema_version'] == 'geographic-catalog-v1' and isinstance(catalog['version'], str) and bool(catalog['version'].strip()), 'invalid package version')
    records = validate_records(catalog['records'])
    require(not national_gaps(records), 'packaged national coverage is incomplete')
    sources = catalog['sources']
    require(isinstance(sources, list) and bool(sources), 'package requires reviewed full-publication provenance')
    for source in sources:
        validate_provenance(source)
    validation = catalog['validation']
    require(isinstance(validation, dict) and set(validation) == {'status', 'records_sha256', 'source_identities_sha256'}, 'invalid validation fields')
    require(validation['status'] == 'complete', 'package is not marked complete')
    require(validation['records_sha256'] == digest(records), 'packaged records hash mismatch')
    coverage = catalog['coverage']
    require(isinstance(coverage, dict) and set(coverage) == {'source_identities', 'national_province_codes'}, 'invalid coverage fields')
    require(coverage['national_province_codes'] == sorted(PROVINCE_CODES), 'national province set mismatch')
    require(digest(coverage['source_identities']) == validation['source_identities_sha256'], 'source identities hash mismatch')
    require(identities(records) == coverage['source_identities'], 'packaged identities differ from reviewed source')


def _cn_kind(name, level):
    """Decode documented administrative name suffixes during offline import only."""
    if level == 'county' and name == '神农架林区':
        return '林区'
    endings = ({'自治区': '自治区', '特别行政区': '特别行政区', '省': '省', '市': '直辖市'} if level == 'province' else
               {'自治州': '自治州', '地区': '地区', '盟': '盟', '市': '地级市'} if level == 'prefecture' else
               {'自治县': '自治县', '自治旗': '自治旗', '特区': '特区', '旗': '旗', '县': '县', '市': '县级市', '区': '市辖区'})
    for ending, kind in endings.items():
        if name.endswith(ending):
            return kind
    raise CatalogValidationError(f'unknown administrative kind: {name}')


def transform_cn_division(snapshot):
    """Verify original snapshot bytes, preserve real levels and apply reviewed aliases.

    Inputs retain exact upstream bytes, including the MIT license. The envelope is
    a derived audit artifact, not an upstream publication or an official raw table.
    """
    require(isinstance(snapshot, dict) and set(snapshot) == {'schema', 'commit', 'inputs', 'review'}, 'invalid cn-division snapshot')
    require(snapshot['schema'] == 'cn-division-audit-snapshot-v1' and snapshot['commit'] == CN_DIVISION_COMMIT, 'unpinned cn-division snapshot')
    files = {}
    for item in snapshot['inputs']:
        require(isinstance(item, dict) and set(item) == {'path', 'url', 'sha256', 'content_base64'}, 'invalid snapshot input')
        path = item['path']
        require(path not in files and item['url'] == f'https://raw.githubusercontent.com/kk-418/cn-division/{CN_DIVISION_COMMIT}/{path}', 'unpinned or duplicate snapshot input')
        try:
            raw = base64.b64decode(item['content_base64'], validate=True)
        except (ValueError, TypeError) as exc:
            raise CatalogValidationError('invalid snapshot bytes') from exc
        require(hashlib.sha256(raw).hexdigest() == item['sha256'], 'snapshot input SHA256 mismatch')
        files[path] = raw
    required = {'dist/code/provinces.json', 'dist/code/cities.json', 'dist/code/counties.json', 'LICENSE', 'lib/fetch.js', 'lib/build.js', 'package.json'}
    require(required <= files.keys(), 'missing original upstream inputs or license')
    require(b'MIT License' in files['LICENSE'], 'upstream MIT license missing')
    review = snapshot['review']
    require(isinstance(review, dict) and set(review) == {'supplements', 'aliases', 'coverage_review', 'identity_benchmark'}, 'invalid snapshot review')
    require(isinstance(review['coverage_review'], str) and bool(review['coverage_review'].strip()), 'missing coverage review')
    records = {}
    excluded = []
    def add(code, name, level):
        code = str(code)
        require(len(code) in {2, 4, 6} and code.isdigit(), 'unexpected administrative code width')
        code = code.ljust(6, '0')
        row = dict(code=code, name=name, level=level, kind=_cn_kind(name, level), province_code=code[:2] + '0000', aliases=[])
        if code in records:
            require(records[code] == row, f'inconsistent duplicate upstream identity: {code}')
        records[code] = row
    for item in json.loads(files['dist/code/provinces.json']):
        require(set(item) == {'c', 'n'}, 'unexpected province fields')
        add(item['c'], item['n'], 'province')
    cities = json.loads(files['dist/code/cities.json'])
    city_codes = {str(item['c']) for item in cities}
    for item in cities:
        require(set(item) == {'c', 'n', 'p'}, 'unexpected city fields')
        require(str(item['c']).startswith(str(item['p'])), 'city parent mismatch')
        if str(item['p']).ljust(6, '0') in MUNICIPALITY_CODES:
            excluded.append(dict(reason='virtual_municipality_layer', original=item))
        else:
            add(item['c'], item['n'], 'county' if len(str(item['c'])) == 6 else 'prefecture')
    for item in json.loads(files['dist/code/counties.json']):
        require(set(item) == {'c', 'n', 'cc'}, 'unexpected county fields')
        code = str(item['c'])
        require(str(item['cc']) in city_codes and code.startswith(str(item['cc'])), 'county city parent mismatch')
        if len(code) == 12:
            require(code.isdigit() and code.startswith(str(item['cc'])[:2]), 'street parent mismatch')
            excluded.append(dict(reason='unsupported_township_or_street', original=item))
            continue
        require(len(code) == 6 and code.startswith(str(item['cc'])[:2]), 'county parent mismatch')
        add(code, item['n'], 'county')
    expected_supplements = {'710000': '台湾省', '810000': '香港特别行政区', '820000': '澳门特别行政区'}
    require(isinstance(review['supplements'], list), 'supplements must be an array')
    for item in review['supplements']:
        require(set(item) == RECORD_KEYS | {'evidence_url'}, 'invalid supplement fields')
        require(expected_supplements.get(item['code']) == item['name'] and official_url(item['evidence_url']), 'supplement requires reviewed official province evidence')
        require(item['level'] == 'province' and item['code'] not in records, 'supplement must add a missing province')
        require(item['kind'] == ('省' if item['code'] == '710000' else '特别行政区') and item['aliases'] == [item['name'].removesuffix('特别行政区').removesuffix('省')], 'incorrect supplemented province type or alias')
        row = {k: item[k] for k in RECORD_KEYS}
        records[row['code']] = row
    aliases = review['aliases']
    require(isinstance(aliases, list), 'aliases must be a reviewed array')
    seen = set()
    province_short_forms = dict(zip(sorted(PROVINCE_CODES), '北京 天津 河北 山西 内蒙古 辽宁 吉林 黑龙江 上海 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 广西 海南 重庆 四川 贵州 云南 西藏 陕西 甘肃 青海 宁夏 新疆 台湾 香港 澳门'.split()))
    for item in aliases:
        require(isinstance(item, dict) and set(item) == {'code', 'canonical', 'alias', 'basis'}, 'invalid reviewed alias')
        require(item['code'] in records and item['canonical'] == records[item['code']]['name'], 'alias canonical identity mismatch')
        require(item['basis'] in {'administrative_suffix_short_form', 'reviewed_province_short_form'}, 'unreviewed alias basis')
        row = records[item['code']]
        if item['basis'] == 'administrative_suffix_short_form':
            require(row['kind'] in {'地级市', '县级市', '市辖区', '县'} and item['alias'] == row['name'][:-1], 'alias differs from reviewed administrative short form')
        else:
            require(row['level'] == 'province' and item['alias'] == province_short_forms[row['code']], 'alias differs from reviewed province short form')
        pair = (item['code'], item['alias'])
        require(pair not in seen, 'duplicate reviewed alias')
        seen.add(pair)
        records[item['code']]['aliases'].append(item['alias'])
    rows = validate_records(list(records.values()))
    benchmark = review['identity_benchmark']
    require(isinstance(benchmark, dict) and set(benchmark) == {'basis', 'records', 'artifact_sha256'}, 'invalid independent identity benchmark')
    require(isinstance(benchmark['basis'], str) and bool(benchmark['basis'].strip()), 'missing benchmark basis')
    require(bool(re.fullmatch(r'[0-9a-f]{64}', benchmark['artifact_sha256'])), 'invalid benchmark artifact SHA256')
    expected = []
    for row in benchmark['records']:
        require(set(row) == set(IDENTITY_KEYS) | {'evidence_url'} and official_url(row['evidence_url']), 'benchmark requires official identity evidence')
        expected.append({key: row[key] for key in IDENTITY_KEYS})
    require(identities(rows) == sorted(expected, key=lambda row: row['code']), 'national identities differ from independently reviewed benchmark')
    return rows, excluded
