"""Pinned real input adapter tests: offline, no models, network or upstream execution."""
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from backend.investigation_creation.geographic_catalog import (
    CatalogValidationError, CN_DIVISION_URL, implementation_sha256,
    read_source, transform_cn_division, validate_packaged_catalog, validate_provenance,
)
from scripts.import_cn_division_catalog import rebuild

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'experiments/geographic_scope_draft/catalog_sources/cn_division'


def snapshot():
    return json.loads((SOURCE / 'snapshot.json').read_text())


def test_real_input_retains_true_levels_and_exclusion_audit():
    rows, excluded = transform_cn_division(snapshot())
    assert Counter(r['level'] for r in rows) == {'province': 34, 'prefecture': 333, 'county': 2851}
    assert sum(r['kind'] == '地级市' for r in rows) == 293
    assert Counter(e['reason'] for e in excluded) == {'virtual_municipality_layer': 5, 'unsupported_township_or_street': 84}
    by_code = {r['code']: r for r in rows}
    assert by_code['429021']['kind'] == '林区'
    assert by_code['140109']['kind'] == '市辖区'
    assert by_code['230751']['kind'] == '市辖区'
    assert by_code['419001']['level'] == 'county'
    assert by_code['659013']['level'] == 'county'
    assert by_code['152900']['kind'] == '盟'
    assert '110100' not in by_code and '500200' not in by_code
    assert all(len(r['code']) == 6 for r in rows)


def test_alias_collisions_remain_including_county_and_province():
    rows, _ = transform_cn_division(snapshot())
    matches = lambda name: {r['code'] for r in rows if name in [r['name'], *r['aliases']]}
    assert {'110105', '211300'} <= matches('朝阳')
    assert {'220000', '220200'} <= matches('吉林')
    assert {'460000', '150303'} <= matches('海南')
    assert matches('克拉玛依') == {'650200', '650203'}
    assert matches('克拉玛依市') == {'650200'}


@pytest.mark.parametrize('mutation, message', [
    (lambda s: s.update(commit='main'), 'unpinned'),
    (lambda s: s['inputs'][0].update(sha256='0' * 64), 'SHA256'),
    (lambda s: s['inputs'][0].update(url='https://github.com/unpinned'), 'unpinned'),
    (lambda s: s.update(inputs=[x for x in s['inputs'] if x['path'] != 'LICENSE']), 'license'),
    (lambda s: s['review']['supplements'][0].update(evidence_url='https://example.com'), 'official province'),
    (lambda s: s['review']['aliases'][0].update(alias='虚构别名'), 'short form'),
    (lambda s: s['review']['aliases'][0].update(canonical='虚构省'), 'canonical identity'),
    (lambda s: s['review']['identity_benchmark']['records'][0].update(name='错误官方名称'), 'independently reviewed benchmark'),
    (lambda s: s['review']['identity_benchmark']['records'][0].update(evidence_url='https://example.com'), 'official identity evidence'),
])
def test_tampered_snapshot_rejected(mutation, message):
    value = snapshot()
    mutation(value)
    with pytest.raises(CatalogValidationError, match=message):
        transform_cn_division(value)


def test_pinned_source_permission_does_not_allow_arbitrary_third_party():
    source = json.loads((SOURCE / 'manifest.json').read_text())['source']
    validate_provenance(source)
    for url in [CN_DIVISION_URL.replace('88f2021ea21769bdb95d93699d8a625fcd9165ef', 'main'), 'https://github.com/other/repo']:
        changed = copy.deepcopy(source)
        changed['url'] = url
        with pytest.raises(CatalogValidationError, match='fixed cn-division'):
            validate_provenance(changed)


def test_real_source_raw_and_normalized_are_reproducible_and_packaged():
    manifest = json.loads((SOURCE / 'manifest.json').read_text())
    records = read_source(manifest['source'], SOURCE)
    package = json.loads((ROOT / 'backend/investigation_creation/data/geographic_catalog.json').read_text())
    validate_packaged_catalog(package)
    assert records == package['records']
    assert manifest['source']['transform']['implementation_sha256'] == implementation_sha256()
    for item in snapshot()['inputs']:
        assert hashlib.sha256((SOURCE / 'upstream' / item['path']).read_bytes()).hexdigest() == item['sha256']


def test_offline_rebuild_exact_package_and_evidence(tmp_path):
    import shutil
    shutil.copytree(SOURCE / 'upstream', tmp_path / 'upstream')
    shutil.copyfile(SOURCE / 'review.json', tmp_path / 'review.json')
    out = tmp_path / 'catalog.json'
    catalog, report = rebuild(tmp_path, out)
    assert report['status'] == 'complete'
    assert json.loads(out.read_text()) == catalog
    assert out.read_bytes() == (ROOT / 'backend/investigation_creation/data/geographic_catalog.json').read_bytes()
    assert (tmp_path / 'snapshot.json').read_bytes() == (SOURCE / 'snapshot.json').read_bytes()
    assert (tmp_path / 'normalized.json').read_bytes() == (SOURCE / 'normalized.json').read_bytes()


@pytest.mark.parametrize('relative_output', [
    'review.json', 'snapshot.json', 'normalized.json', 'excluded.json',
    'manifest.json', 'generation_report.json', 'upstream/LICENSE',
    'upstream/dist/code/provinces.json', 'upstream/new-file.json',
])
def test_output_cannot_overwrite_source_or_generated_evidence(tmp_path, relative_output):
    import shutil
    shutil.copytree(SOURCE, tmp_path / 'source')
    root = tmp_path / 'source'
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    with pytest.raises(CatalogValidationError, match='must not overwrite'):
        rebuild(root, root / relative_output)
    after = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    assert after == before
