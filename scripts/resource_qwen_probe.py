"""One small opt-in provider probe; secret is read without echo and never saved."""
import argparse
import getpass
import json
from pathlib import Path
import requests

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url',required=True)
parser.add_argument('--model',default='qwen3.7-plus')
args=parser.parse_args()
key=getpass.getpass('Experiment API key (hidden): ').replace('\\_','_').strip()
try:
    response=requests.post(args.base_url.rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':args.model,'messages':[{'role':'user','content':'只回复 OK。'}],'max_tokens':32,'enable_thinking':True},timeout=(10,45))
    body=response.json()
    evidence={'model':args.model,'http_status':response.status_code,'usage':body.get('usage'), 'reply':body.get('choices',[{}])[0].get('message',{}).get('content')}
    if not response.ok:evidence['error']=body.get('error',{})
except Exception as exc:
    evidence={'model':args.model,'failure_type':type(exc).__name__}
encoded=json.dumps(evidence,ensure_ascii=False,indent=2).replace(key,'[redacted]')
Path('docs/evidence/m3-resource-lifecycle/qwen-provider-probe.json').write_text(encoded+'\n')
print(encoded)
