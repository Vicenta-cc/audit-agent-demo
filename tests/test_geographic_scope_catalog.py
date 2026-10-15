"""Synthetic structural/import protocol fixtures only, never a national catalog.

Even the generated nationwide-shaped fixture below contains invented city names.
It tests reproducible import plumbing, not authoritative coverage. No fixture
is written under backend/investigation_creation/data or used by the application.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_geographic_catalog import (
    CatalogValidationError, MUNICIPALITY_CODES, PROVINCE_CODES,
    build_catalog, digest, identities, main, validate_records, validate_packaged_catalog,
)


def record(code='650000', name='新疆维吾尔自治区', level='province', kind='自治区', parent='650000', aliases=None):
    return dict(code=code, name=name, level=level, kind=kind, province_code=parent, aliases=aliases or [])


@pytest.fixture
def small_records():
    return [record(aliases=['新疆']), record('650200', '克拉玛依市', 'prefecture', '地级市', aliases=['克拉玛依'])]


def source(tmp_path, records, *, filename='raw.json', benchmark=False):
    from backend.investigation_creation.geographic_catalog import (
        TRANSFORM_VERSION, implementation_sha256, transform_raw,
    )
    path = tmp_path / filename
    # Invented raw publication bytes; metadata URL is only structural test input.
    path.write_text(json.dumps({'records': records}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    normalized = tmp_path / ('normalized-' + filename)
    normalized.write_bytes(transform_raw(path.read_bytes(), 'identity_records_json'))
    return dict(raw_file=filename, raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        normalized_file=normalized.name, normalized_sha256=hashlib.sha256(normalized.read_bytes()).hexdigest(),
        url=f'https://www.mca.gov.cn/test-only/{filename}', data_date='2024-12-31',
        retrieved_date='2026-10-15', version='synthetic-test-only',
        full_table_evidence_url='https://www.mca.gov.cn/test-only/full-publication',
        coverage_review='SYNTHETIC TEST ONLY: not an authoritative dataset or actual complete publication.',
        transform={'id': 'identity_records_json', 'version': TRANSFORM_VERSION,
                   'implementation_sha256': implementation_sha256()})


def manifest(tmp_path, records):
    return {'version': 'synthetic-test-only', 'source': source(tmp_path, records)}


def test_partial_fixture_stays_incomplete_and_no_production_output(tmp_path, small_records):
    data = manifest(tmp_path, small_records)
    catalog, report = build_catalog(data, tmp_path)
    assert catalog is None
    assert report['status'] == 'incomplete'
    assert report['production_output_allowed'] is False
    assert any('missing national province' in gap for gap in report['gaps'])
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(data))
    output = tmp_path / 'production.json'
    output.write_text('existing-production-must-not-change')
    assert main(['--manifest', str(path), '--report', str(tmp_path / 'report.json'), '--output', str(output)]) == 2
    assert output.read_text() == 'existing-production-must-not-change'


def test_actual_file_sha_is_checked_before_any_import(tmp_path, small_records):
    data = manifest(tmp_path, small_records)
    (tmp_path / 'raw.json').write_text('{"records": []}')
    with pytest.raises(CatalogValidationError, match='SHA256 mismatch'):
        build_catalog(data, tmp_path)


@pytest.mark.parametrize('mutation, message', [
    (lambda rows: rows.append(copy.deepcopy(rows[0])), 'duplicate code'),
    (lambda rows: rows[1].update(province_code='640000'), 'prefix mismatch'),
    (lambda rows: rows[1].update(kind='municipality'), 'level/kind'),
    (lambda rows: rows[1].update(code='650201'), 'end in 00'),
    (lambda rows: rows[1].update(aliases=['克拉玛依', '克拉玛依']), 'duplicate aliases'),
    (lambda rows: rows[1].update(aliases=[' 克拉玛依']), 'trimmed'),
    (lambda rows: rows[1].update(aliases=['克拉玛依市']), 'canonical name'),
    (lambda rows: rows[1].update(unknown='value'), 'unknown fields'),
    (lambda rows: rows[0].update(province_code='640000'), 'self-parent'),
    (lambda rows: rows[0].update(kind='municipality'), 'municipality'),
])
def test_invalid_record_rejected(small_records, mutation, message):
    mutation(small_records)
    with pytest.raises(CatalogValidationError, match=message):
        validate_records(small_records)


def test_parent_foreign_key_requires_real_province():
    with pytest.raises(CatalogValidationError, match='missing province parent'):
        validate_records([record('650200', '克拉玛依市', 'prefecture', '地级市')])


def test_municipality_cannot_gain_virtual_prefecture():
    beijing = record('110000', '北京市', 'province', '直辖市', '110000')
    virtual = record('110100', '北京市', 'prefecture', '地级市', '110000')
    with pytest.raises(CatalogValidationError, match='virtual municipality'):
        validate_records([beijing, virtual])


def test_true_unsupported_types_and_cross_level_aliases_remain_visible(tmp_path):
    rows = [record('150000', '内蒙古自治区', 'province', '自治区', '150000'),
            record('152900', '阿拉善盟', 'prefecture', '盟', '150000'),
            record('110000', '北京市', 'province', '直辖市', '110000'),
            record('110105', '朝阳区', 'county', '市辖区', '110000', ['朝阳']),
            record('210000', '辽宁省', 'province', '省', '210000'),
            record('211300', '朝阳市', 'prefecture', '地级市', '210000', ['朝阳'])]
    assert next(r for r in validate_records(rows) if r['code'] == '152900')['kind'] == '盟'
    _, report = build_catalog(manifest(tmp_path, rows), tmp_path)
    assert report['alias_ambiguities'] == {'朝阳': ['110105', '211300']}
    assert report['status'] == 'incomplete'


@pytest.mark.parametrize('field, value, message', [
    ('url', 'https://mca.gov.cn.attacker.example/raw', 'official'),
    ('url', 'http://www.mca.gov.cn/raw', 'official'),
    ('data_date', '2024', 'YYYY-MM-DD'),
    ('data_date', '2026-10-16', 'after retrieved_date'),
    ('raw_sha256', 'not-a-sha', 'lowercase SHA256'),
    ('version', '', 'nonempty string'),
])
def test_manifest_provenance_validation(tmp_path, small_records, field, value, message):
    data = manifest(tmp_path, small_records)
    data['source'][field] = value
    with pytest.raises(CatalogValidationError, match=message):
        build_catalog(data, tmp_path)


def nationwide_shaped_synthetic_rows():
    """Invented identities to test the protocol. Absolutely not a usable dataset."""
    rows = []
    for code in sorted(PROVINCE_CODES):
        rows.append(record(code, f'合成测试省级{code}', 'province', '直辖市' if code in MUNICIPALITY_CODES else '省', code))
        if code not in MUNICIPALITY_CODES | {'710000', '810000', '820000'}:
            rows.append(record(code[:2] + '0100', f'合成测试城市{code}', 'prefecture', '地级市', code))
    return rows


def reviewed_test_manifest(tmp_path, rows):
    data = manifest(tmp_path, rows)
    benchmark_rows = copy.deepcopy(rows)
    benchmark_rows[0]['aliases'] = ['仅测试基准别名']  # independent local bytes for comparison mechanics
    data['benchmark'] = source(tmp_path, benchmark_rows, filename='benchmark.json', benchmark=True)
    return data


def test_all_provinces_and_municipalities_are_needed_but_not_sufficient(tmp_path):
    rows = [r for r in nationwide_shaped_synthetic_rows() if r['level'] == 'province']
    _, report = build_catalog(reviewed_test_manifest(tmp_path, rows), tmp_path)
    assert report['municipality_count'] == 4
    assert report['level_counts'] == {'province': 34}
    assert report['status'] == 'incomplete'
    assert any('prefecture' in gap for gap in report['gaps'])


def test_exact_benchmark_checks_identity_not_only_record_counts(tmp_path):
    rows = nationwide_shaped_synthetic_rows()
    data = reviewed_test_manifest(tmp_path, rows)
    changed = copy.deepcopy(rows)
    next(r for r in changed if r['level'] == 'prefecture')['name'] = '同计数但身份不同'
    data['source'] = source(tmp_path, changed)
    _, report = build_catalog(data, tmp_path)
    assert report['status'] == 'incomplete'
    assert any('optional benchmark' in gap for gap in report['gaps'])


def test_comparison_protocol_has_deterministic_output_with_reviewed_synthetic_baseline(tmp_path):
    rows = nationwide_shaped_synthetic_rows()
    data = reviewed_test_manifest(tmp_path, rows)
    first, report = build_catalog(data, tmp_path)
    second, _ = build_catalog(data, tmp_path)
    assert first == second
    assert first['schema_version'] == 'geographic-catalog-v1'
    assert first['records'] == sorted(rows, key=lambda r: r['code'])
    assert report['evidence_boundary'].startswith('Local raw bytes')
    # This is protocol coverage with invented records, not national acceptance.


def test_single_reviewed_full_publication_does_not_require_second_source(tmp_path):
    data = manifest(tmp_path, nationwide_shaped_synthetic_rows())
    catalog, report = build_catalog(data, tmp_path)
    assert report['status'] == 'complete'
    assert len(catalog['sources']) == 1
    assert catalog['sources'][0]['raw_sha256'] == data['source']['raw_sha256']
    assert catalog['sources'][0]['normalized_sha256'] == data['source']['normalized_sha256']



def test_cli_never_overwrites_source_with_report(tmp_path, small_records):
    data = manifest(tmp_path, small_records)
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(data))
    before = (tmp_path / 'raw.json').read_bytes()
    with pytest.raises(SystemExit):
        main(['--manifest', str(path), '--report', str(tmp_path / 'raw.json')])
    assert (tmp_path / 'raw.json').read_bytes() == before



def test_runtime_rechecks_packaged_records_and_all_benchmark_identities(tmp_path):
    rows = nationwide_shaped_synthetic_rows()
    data = reviewed_test_manifest(tmp_path, rows)
    catalog, _ = build_catalog(data, tmp_path)
    validate_packaged_catalog(catalog)
    tampered = copy.deepcopy(catalog)
    tampered['records'][1]['name'] = '篡改'
    with pytest.raises(CatalogValidationError, match='records hash'):
        validate_packaged_catalog(tampered)
    tampered['validation']['records_sha256'] = digest(tampered['records'])
    with pytest.raises(CatalogValidationError, match='identities differ'):
        validate_packaged_catalog(tampered)
    tampered = copy.deepcopy(catalog)
    tampered['coverage']['source_identities'].pop()
    with pytest.raises(CatalogValidationError, match='identities hash'):
        validate_packaged_catalog(tampered)
    tampered = copy.deepcopy(catalog)
    tampered['validation']['status'] = 'incomplete'
    with pytest.raises(CatalogValidationError, match='not marked complete'):
        validate_packaged_catalog(tampered)


def test_nationwide_shape_without_complete_publication_evidence_is_rejected(tmp_path):
    data = manifest(tmp_path, nationwide_shaped_synthetic_rows())
    data['source'].pop('coverage_review')
    with pytest.raises(CatalogValidationError, match='metadata fields'):
        build_catalog(data, tmp_path)



def test_cli_complete_protocol_writes_reproducible_verified_package_only_in_temp_directory(tmp_path):
    # Invented identities test writer mechanics, never authoritative coverage.
    data = reviewed_test_manifest(tmp_path, nationwide_shaped_synthetic_rows())
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(data))
    output = tmp_path / 'synthetic-package-for-test-only.json'
    args = ['--manifest', str(path), '--report', str(tmp_path / 'report.json'), '--output', str(output)]
    assert main(args) == 0
    first = output.read_bytes()
    validate_packaged_catalog(json.loads(first))
    assert main(args) == 0
    assert output.read_bytes() == first


def test_cli_invalid_raw_hash_records_failure_without_writing_package(tmp_path, small_records):
    data = manifest(tmp_path, small_records)
    data['source']['raw_sha256'] = '0' * 64
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(data))
    report = tmp_path / 'report.json'
    output = tmp_path / 'catalog.json'
    assert main(['--manifest', str(path), '--report', str(report), '--output', str(output)]) == 2
    assert json.loads(report.read_text())['status'] == 'invalid'
    assert not output.exists()


def test_normalized_hash_and_transform_output_are_both_verified(tmp_path, small_records):
    data = manifest(tmp_path, small_records)
    normalized = tmp_path / data['source']['normalized_file']
    normalized.write_text('{"records": []}')
    with pytest.raises(CatalogValidationError, match='normalized SHA256 mismatch'):
        build_catalog(data, tmp_path)
    data['source']['normalized_sha256'] = hashlib.sha256(normalized.read_bytes()).hexdigest()
    with pytest.raises(CatalogValidationError, match='transform differs'):
        build_catalog(data, tmp_path)


def test_unsupported_or_tampered_transform_is_not_executed(tmp_path, small_records):
    data = manifest(tmp_path, small_records)
    data['source']['transform']['id'] = 'python_script'
    with pytest.raises(CatalogValidationError, match='unsupported transform'):
        build_catalog(data, tmp_path)
    data['source']['transform']['id'] = 'identity_records_json'
    data['source']['transform']['implementation_sha256'] = '0' * 64
    with pytest.raises(CatalogValidationError, match='implementation SHA256'):
        build_catalog(data, tmp_path)


def test_extract_records_preserves_every_identity_from_raw_json(tmp_path):
    from backend.investigation_creation.geographic_catalog import transform_raw
    rows = nationwide_shaped_synthetic_rows()
    data = manifest(tmp_path, rows)
    raw = tmp_path / data['source']['raw_file']
    raw.write_text(json.dumps({'publication_title': 'SYNTHETIC TEST ONLY', 'records': rows}), encoding='utf-8')
    normalized = tmp_path / data['source']['normalized_file']
    normalized.write_bytes(transform_raw(raw.read_bytes(), 'extract_records_json'))
    data['source']['transform']['id'] = 'extract_records_json'
    data['source']['raw_sha256'] = hashlib.sha256(raw.read_bytes()).hexdigest()
    data['source']['normalized_sha256'] = hashlib.sha256(normalized.read_bytes()).hexdigest()
    catalog, _ = build_catalog(data, tmp_path)
    assert catalog['records'] == sorted(rows, key=lambda r: r['code'])
    assert len(catalog['records']) == len(rows)


@pytest.mark.parametrize('target', ['raw_file', 'normalized_file'])
def test_output_cannot_overwrite_either_source_evidence(tmp_path, small_records, target):
    data = manifest(tmp_path, small_records)
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(data))
    output = tmp_path / data['source'][target]
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        main(['--manifest', str(path), '--report', str(tmp_path / 'report.json'), '--output', str(output)])
    assert output.read_bytes() == before


def test_html_publication_is_honestly_unsupported(tmp_path, small_records):
    data = manifest(tmp_path, small_records)
    raw = tmp_path / data['source']['raw_file']
    raw.write_text('<html>official publication needs a reviewed transform</html>')
    data['source']['raw_sha256'] = hashlib.sha256(raw.read_bytes()).hexdigest()
    with pytest.raises(CatalogValidationError, match='unsupported raw format'):
        build_catalog(data, tmp_path)
