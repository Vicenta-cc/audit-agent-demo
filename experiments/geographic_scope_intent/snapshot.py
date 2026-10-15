"""Freeze creation prompt/tools in a disposable, network/database-blocked subprocess."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def literal(path: Path, name: str):
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"literal missing: {name}")


def provider_config(config_root=ROOT):
    """Read only coordinator settings; no config module import or credential output."""
    from dotenv import dotenv_values
    values = dotenv_values(Path(config_root) / '.env') if (Path(config_root) / '.env').exists() else {}
    environment = {**values, **os.environ}
    settings = next(n for n in ast.parse((ROOT / 'backend/audit_agent/config.py').read_text()).body
                    if isinstance(n, ast.ClassDef) and n.name == 'Settings')
    result = {}
    for field, key in [('conversation_model', 'CONVERSATION_MODEL'),
                       ('conversation_base_url', 'CONVERSATION_BASE_URL'),
                       ('conversation_api_key', 'CONVERSATION_API_KEY')]:
        assignment = next(n for n in settings.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == field for t in n.targets))
        # Current fields are literal os.getenv calls, optionally followed by rstrip.
        calls = [n for n in ast.walk(assignment.value) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'getenv']
        default = ast.literal_eval(calls[0].args[1])
        result[field] = str(environment.get(key) or default)
    result['conversation_base_url'] = result['conversation_base_url'].rstrip('/')
    return result


def freeze():
    # Only the exporter imports business modules. Their mkdir side effects are disposable;
    # no app, service, worker, runtime agent or dispatcher is constructed.
    child = '''
import json, socket, sqlite3
def deny(*args, **kwargs): raise RuntimeError('snapshot forbids network/database')
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
socket.getaddrinfo = deny
sqlite3.connect = deny
from backend.investigation_creation.tools import creation_tool_schemas
from backend.audit_agent.config import settings
settings.investigation_multi_round_enabled = True
print(json.dumps(creation_tool_schemas(), ensure_ascii=False))
'''
    with tempfile.TemporaryDirectory(prefix='geo-p0-snapshot-') as temp:
        env = {k: v for k, v in os.environ.items() if not any(s in k for s in ('API_KEY', 'AUTH_KEY', 'ENCRYPTION_KEY'))}
        env.update(PYTHON_DOTENV_DISABLED='1', PYTHONDONTWRITEBYTECODE='1',
                   XHS_AUDIT_DATA_DIR=temp+'/data', XHS_AUDIT_OUTPUTS_DIR=temp+'/outputs')
        schemas = json.loads(subprocess.check_output([sys.executable, '-c', child], cwd=ROOT, env=env, text=True))
    base = literal(ROOT/'backend/investigation_creation/conversation.py', 'CREATION_SYSTEM_PROMPT')
    resource = literal(ROOT/'backend/resource_management/tools.py', 'RESOURCE_PROMPT')
    rounds = literal(ROOT/'backend/investigation_creation/rounds.py', 'GUIDANCE')
    continuity = literal(ROOT/'backend/investigation_creation/continuity.py', 'GUIDANCE')
    files = {
        'baseline_tools.json': json.dumps(schemas, ensure_ascii=False, indent=2)+'\n',
        'baseline_prompt.txt': base+resource,
        'rounds_guidance.txt': rounds,
        'continuity_guidance.txt': continuity,
    }
    for name, body in files.items():
        (HERE/name).write_text(body)
    sources = ['backend/investigation_creation/conversation.py', 'backend/investigation_creation/configure.py',
               'backend/investigation_creation/contracts.py', 'backend/investigation_creation/tools.py',
               'backend/resource_management/tools.py', 'backend/investigation_creation/rounds.py',
               'backend/investigation_creation/continuity.py', 'backend/audit_agent/config.py']
    config = provider_config()
    manifest = {
        'version': 'geo-intent-p0-v1', 'baseline_head': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'sources_sha256': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources},
        'snapshots_sha256': {p: hashlib.sha256((HERE/p).read_bytes()).hexdigest() for p in files},
        'coordinator_model': config['conversation_model'],
        'provider_host': __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(config['conversation_base_url']).hostname,
        'tools_include_optional_rounds': True,
        'note': 'Frozen creation tools; per-case feature flags select rounds/continuity guidance. Report tools excluded: P0 creation intent only.',
    }
    (HERE/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    freeze()
