"""Run the production TypeScript serializer before writing a real isolated store."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from test_resource_library_editor import service, rules, put

FRONTEND = Path(__file__).resolve().parents[1] / 'Audit_assistant'


def serialize(resource, *, rename=False):
    node = shutil.which('node')
    if not node or not (FRONTEND / 'node_modules/typescript').exists():
        pytest.skip('Frontend Node/TypeScript runtime required for cross-layer test')
    program = '''
const ts = require('typescript');
const fs = require('fs');
const vm = require('vm');
const bundle = ts.transpileModule(fs.readFileSync('src/services/resourceLibrary.ts','utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const exported = {};
const sandbox = {module:{exports:exported},exports:exported,require:(name)=>{if(name==='./apiClient')return {}; throw new Error(name);}};
vm.runInNewContext(bundle, sandbox);
const {resource,rename} = JSON.parse(fs.readFileSync(0,'utf8'));
const view = sandbox.module.exports.ruleSetView(resource);
if (rename) view.name = '只改名称';
process.stdout.write(JSON.stringify(sandbox.module.exports.rulesContent(view, resource.content)));
'''
    result = subprocess.run([node, '-e', program], cwd=FRONTEND, input=json.dumps({'resource':resource,'rename':rename}),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_browser_serialization_noop_does_not_publish_new_version(service):
    body=rules(); body['categories'].reverse()
    body['categories'][0]['rules'].reverse()
    saved=put(service,'ruleset',body)
    serialized=serialize(saved)
    assert serialized==saved['content']
    again=put(service,'ruleset',serialized,saved['version'],'no-op')
    assert again['version']==saved['version']
    assert again['published_revision_id']==saved['published_revision_id']


def test_browser_rename_only_changes_name_and_one_version(service):
    saved=put(service,'ruleset',rules())
    serialized=serialize(saved,rename=True)
    assert serialized==saved['content'] | {'name':'只改名称'}
    changed=put(service,'ruleset',serialized,saved['version'],'rename')
    assert changed['published_version']==saved['published_version']+1
