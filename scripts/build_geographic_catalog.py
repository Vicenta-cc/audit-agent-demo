"""Build a catalog offline from an explicitly reviewed offline publication or pinned cn-division snapshot.

Manifest: {version, source, benchmark?}. Each source names separate raw_file /
raw_sha256 and normalized_file / normalized_sha256, official url, data_date,
retrieved_date, version, full_table_evidence_url, coverage_review, and transform
{id, version, implementation_sha256}. Supported built-ins preserve all records:
identity_records_json accepts {records}; extract_records_json extracts records
from a JSON object with publication metadata. The SHA pins the backend transform
implementation. No scripts are executed; HTML/PDF/XML remain unsupported.

The source must be reviewed as a complete national table. A second source is
optional and, when supplied, its exact province/prefecture identities are checked.
A matching city count alone never proves a source complete. coverage_review
must also document cross-level alias conflict review against the full official
county table or other cited disambiguation evidence; a city-only list cannot
establish global alias uniqueness. Synthetic fixtures
only test this protocol and must never be supplied as official publications.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

# Support direct `python scripts/build_geographic_catalog.py` as well as -m.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.investigation_creation.geographic_catalog import (
    CatalogValidationError, MUNICIPALITY_CODES, PROVINCE_CODES, digest, identities,
    national_gaps, read_source, require, validate_packaged_catalog, validate_records,
)


def build_catalog(manifest: dict, root: Path):
    require(isinstance(manifest, dict) and set(manifest) <= {'version', 'source', 'benchmark'}
            and {'version', 'source'} <= set(manifest), 'manifest requires version/source and optional benchmark only')
    require(isinstance(manifest['version'], str) and bool(manifest['version'].strip()), 'catalog version must be nonempty')
    records = read_source(manifest['source'], root)
    gaps = national_gaps(records)
    sources = [manifest['source']]
    if 'benchmark' in manifest:
        expected = read_source(manifest['benchmark'], root)
        expected_ids = identities(expected)
        if identities(records) != expected_ids:
            gaps.append('optional benchmark province/prefecture identities differ')
        sources.append(manifest['benchmark'])
    aliases = defaultdict(set)
    for record in records:
        for name in [record['name'], *record['aliases']]:
            aliases[name].add(record['code'])
    report = {
        'status': 'incomplete' if gaps else 'complete', 'production_output_allowed': not gaps,
        'version': manifest['version'], 'record_count': len(records),
        'level_counts': dict(Counter(r['level'] for r in records)),
        'kind_counts': dict(Counter(r['kind'] for r in records)),
        'municipality_count': sum(r['code'] in MUNICIPALITY_CODES for r in records),
        'alias_ambiguities': {name: sorted(codes) for name, codes in sorted(aliases.items()) if len(codes) > 1},
        'gaps': gaps, 'sources': sources, 'records_sha256': digest(records),
        'evidence_boundary': 'Local raw bytes, normalized bytes, deterministic transform, records and coverage shape verified. Authenticity and full-table completeness require review of the cited source and coverage_review; matching counts or invented fixtures cannot prove national coverage. Alias cross-level uniqueness requires review against county-level official evidence and is not established by a city-only table.',
    }
    catalog = None if gaps else {
        'schema_version': 'geographic-catalog-v1', 'version': manifest['version'],
        'sources': sources, 'records': records,
        'validation': {'status': 'complete', 'records_sha256': digest(records),
                       'source_identities_sha256': digest(identities(records))},
        'coverage': {'source_identities': identities(records), 'national_province_codes': sorted(PROVINCE_CODES)},
    }
    if catalog is not None:
        validate_packaged_catalog(catalog)
    return catalog, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    protected = {args.manifest.resolve()}
    if args.report.resolve() in protected:
        parser.error('report must not overwrite manifest')
    try:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        if isinstance(manifest, dict):
            for key in ('source', 'benchmark'):
                source = manifest.get(key)
                if isinstance(source, dict):
                    for field in ('raw_file', 'normalized_file'):
                        if isinstance(source.get(field), str):
                            protected.add((args.manifest.parent / source[field]).resolve())
        if args.report.resolve() in protected:
            parser.error('report must not overwrite source evidence')
        if args.output and args.output.resolve() in protected | {args.report.resolve()}:
            parser.error('output must not overwrite manifest, report or source evidence')
        catalog, report = build_catalog(manifest, args.manifest.parent)
    except (CatalogValidationError, OSError, ValueError, TypeError, KeyError) as exc:
        report = {'status': 'invalid', 'production_output_allowed': False, 'error': str(exc)}
        catalog = None
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if catalog is None:
        print(f"{report['status']}: production catalog not written; see {args.report}")
        return 2
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(f"complete: {report['record_count']} records verified; see {args.report}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
