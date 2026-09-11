"""Provision this experiment once from immutable A/B archives and explicit dependencies."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-runtime', 'crawler-source', 'archive-a', 'archive-b', 'python', 'node'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    runtime = ROOT / 'outputs/m3-environment'
    if runtime.exists():
        parser.error('Runtime already exists; refusing to overwrite it')
    from dotenv import dotenv_values
    source_env = json.loads((args.source_runtime / 'environment.json').read_text())
    source_env.update({k: v for k, v in dotenv_values(args.source_runtime / 'secrets.env').items() if v is not None})
    key = getpass.getpass('Paid experiment API key (hidden): ').replace('\\_', '_').strip()
    if not key:
        parser.error('An experiment API key is required')
    os.umask(0o077)
    runtime.mkdir(parents=True)
    (runtime / 'data/outputs').mkdir(parents=True)
    (runtime / 'archives').mkdir()
    expected_hashes = {'a': 'f70d1b9fb6cd85471d3f9e8e3bc2c0d89f390329a6c5a02449d4a3c506b46560',
                       'b': '2de176629ac0cd2d06588bc436786dc45819555ec48bc2cad59a84207c8793bf'}
    for label in ('a', 'b'):
        archive = getattr(args, 'archive_' + label)
        content = archive.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_hashes[label]:
            raise RuntimeError('Immutable archive checksum mismatch: ' + label)
        (runtime / f'archives/report-{label}.sqlite3').write_bytes(content)
    # Copy code only, never another environment's browser profile or cookies.
    crawler = runtime / 'crawler'
    shutil.copytree(args.crawler_source, crawler, symlinks=True,
        ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', 'browser_data',
                                    'cache', 'data', 'logs', '*.lock', '.env', 'node_modules'))
    (crawler / '.venv').symlink_to((args.crawler_source / '.venv').resolve(), target_is_directory=True)
    filtered = {k: str(v) for k, v in source_env.items() if not any(s in k for s in (
        'API_KEY', 'TOKEN', 'SECRET', 'AUTH_', 'PASSWORD', 'XHS_AUDIT_', 'HISTORICAL_REPORT_',
        'MEDIACRAWLER', 'CRAWLER_LOGIN', 'HERMES_HOME', 'PYTHONPATH', 'VITE_'))}
    filtered['DASHSCOPE_BASE_URL'] = 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
    filtered['M3_COMMENTS_PER_POST'] = '3'
    (runtime / 'environment.json').write_text(json.dumps(filtered, indent=2))
    # Other service credentials remain an explicit private copy. The paid Qwen
    # key overrides any source provider credential and never reaches frontend.
    private = {k: str(v) for k, v in source_env.items() if any(s in k for s in ('API_KEY', 'TOKEN', 'PASSWORD'))}
    private['DASHSCOPE_API_KEY'] = key
    def quote(value):
        return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"
    (runtime / 'secrets.env').write_text(''.join(k + '=' + quote(v) + '\n' for k, v in private.items()))
    config = {'environment_id': 'm3-resource-lifecycle-experiment', 'worktree': str(ROOT),
              'data_dir': str(runtime / 'data'), 'crawler_dir': str(crawler),
              'python': str(args.python.absolute()), 'node': str(args.node.absolute()),
              'api_port': 8178, 'frontend_port': 3178}
    (runtime / 'config.json').write_text(json.dumps(config, indent=2))
    print('Provisioned private M3 environment. No accounts, tasks, or browser sessions copied.')


if __name__ == '__main__':
    main()
