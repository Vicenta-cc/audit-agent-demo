"""Rebuild the reviewed, pinned cn-division snapshot offline; never downloads or executes upstream code.

The review file is a human-reviewable input. Keep original upstream files, review,
MIT license, snapshot, normalized output and manifest together for reproduction.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.investigation_creation.geographic_catalog import (
    CN_DIVISION_COMMIT, CN_DIVISION_URL, TRANSFORM_VERSION,
    implementation_sha256, transform_raw, transform_cn_division, require,
)
from scripts.build_geographic_catalog import build_catalog


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def rebuild(root: Path, output: Path | None = None):
    root = root.resolve()
    protected = {root / name for name in ('review.json', 'snapshot.json', 'normalized.json', 'excluded.json', 'manifest.json', 'generation_report.json')}
    if output is not None:
        output = output.resolve()
        upstream = (root / 'upstream').resolve()
        require(output not in {path.resolve() for path in protected} and output != upstream and upstream not in output.parents, 'output must not overwrite source or generated evidence')
    review = json.loads((root / 'review.json').read_text(encoding='utf-8'))
    inputs = []
    for path in sorted((root / 'upstream').rglob('*')):
        if path.is_file():
            relative = path.relative_to(root / 'upstream').as_posix()
            raw = path.read_bytes()
            inputs.append(dict(path=relative,
                url=f'https://raw.githubusercontent.com/kk-418/cn-division/{CN_DIVISION_COMMIT}/{relative}',
                sha256=hashlib.sha256(raw).hexdigest(), content_base64=base64.b64encode(raw).decode('ascii')))
    snapshot = dict(schema='cn-division-audit-snapshot-v1', commit=CN_DIVISION_COMMIT, inputs=inputs, review=review)
    write_json(root / 'snapshot.json', snapshot)
    raw = (root / 'snapshot.json').read_bytes()
    normalized = transform_raw(raw, 'cn_division_snapshot_v1')
    (root / 'normalized.json').write_bytes(normalized)
    _, excluded = transform_cn_division(snapshot)
    write_json(root / 'excluded.json', excluded)
    source = dict(raw_file='snapshot.json', raw_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_file='normalized.json', normalized_sha256=hashlib.sha256(normalized).hexdigest(),
        url=CN_DIVISION_URL, data_date='2026-09-02', retrieved_date='2026-10-15',
        version=f'cn-division-2026.0.1@{CN_DIVISION_COMMIT}', full_table_evidence_url=CN_DIVISION_URL,
        coverage_review=review['coverage_review'],
        transform=dict(id='cn_division_snapshot_v1', version=TRANSFORM_VERSION, implementation_sha256=implementation_sha256()))
    manifest = dict(version='cn-division-2026.0.1-reviewed-p1-v1', source=source)
    write_json(root / 'manifest.json', manifest)
    catalog, report = build_catalog(manifest, root)
    report['excluded_counts'] = {reason: sum(x['reason'] == reason for x in excluded)
        for reason in sorted({x['reason'] for x in excluded})}
    report['upstream_input_sha256'] = {x['path']: x['sha256'] for x in inputs}
    report['date_boundary'] = '2026-09-02 is the upstream Git commit date, not an official administrative effective date. 2026-10-15 is the retrieval/review date.'
    report['license'] = 'MIT; retained in upstream/LICENSE and losslessly embedded in snapshot.json.'
    write_json(root / 'generation_report.json', report)
    if catalog is not None and output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, catalog)
    return catalog, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    catalog, report = rebuild(args.source_dir, args.output)
    print(f"{report['status']}: {report['record_count']} normalized records")
    return 0 if catalog is not None else 2

if __name__ == '__main__':
    raise SystemExit(main())
