import json
import pytest

from test_investigation_creation_conversation import creation_stack
from test_report_collection import begin, finish
from scripts.report_diverse_cases import cases
from scripts.report_diverse_acceptance import publish
from backend.report_collection.service import ReportCollectionService
from backend.report_collection.access import owns_report
from backend.report_collection import tools
from backend.report_collection.analysis import compare_samples


@pytest.fixture
def make_collection(creation_stack, tmp_path):
    def make(dataset):
        s = creation_stack
        spec = cases()[dataset]
        reports = {name:publish(tmp_path/name,name,value,s['resource_db']) for name,value in spec['reports'].items()}
        with s['creation_store'].admission.connect() as db:
            for name,r in reports.items():
                db.execute("""INSERT INTO task_admissions
                    (id,owner_id,task_id,job_id,kind,day,state,request_key,fingerprint,payload_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,'2026-10-10','SUCCEEDED',?,?,'{}','2026-10-10','2026-10-10')""",
                    (name,'principal-b' if name=='D' else 'principal-a',r.task,r.task,'fixture',name,name))
        service=ReportCollectionService(conversation=s['conversation'],report_store=s['report_store'],
            can_read=lambda p,item:owns_report(s['creation_store'],p,item),path=tmp_path/'collections.sqlite3',agent_driven=True)
        s['conversation'].report_collection=service
        tools.configure(service)
        sid=s['client'].post('/api/investigation-workspaces',json={}).json()['workspace_session_id']
        assert s['client'].post(f'/api/investigation-workspaces/{sid}/report-sources',json={'reports':[
            {'label':name,'report_version_id':reports[name].version} for name in spec['visible']]}).status_code==200
        s.update(collection=service,sid=sid,reports=reports)
        return s
    return make


def invoke(s,t,name='query_conversation_reports',**args):
    result=json.loads(tools.dispatch(name,args,s['sid'],t))
    assert result['ok'],result
    return result['data']


def test_membership_groups_and_exact_text_counts_use_full_filtered_data(make_collection):
    s=make_collection('identity');t=begin(s)
    refs={r['label']:r['report_ref'] for r in invoke(s,t,'list_conversation_reports')['reports']}
    result=invoke(s,t,kind='accounts',limit=1)
    groups=result['report_presence_groups']
    assert {(tuple(g['labels']),g['account_count']) for g in groups['groups']}=={
        (('A','B','C'),2),(('A','C'),1),(('B',),1)}
    assert result['has_more'] and groups['matching_account_count']==4
    all_rows=invoke(s,t,'read_conversation_result',result_ref=result['result_ref'],limit=50)
    bridges=[r for r in all_rows['items'] if '小桥' in r['names']]
    assert len(bridges)==2 and len({r['account_ref'] for r in bridges})==2
    counts=invoke(s,t,report_refs=[refs['A'],refs['B']],text_exact='周末去江边散步',limit=1)
    expected_counts={'original_comments':3,'report_observations':4,'completed_assessments':4}
    assert {k:counts['query_statistics']['comments'][k] for k in expected_counts}==expected_counts
    assert counts['has_more']
    finish(s,t);t=begin(s)
    rest=invoke(s,t,'read_conversation_result',result_ref=counts['result_ref'],offset=1,limit=50)
    assert rest['query_statistics']==counts['query_statistics']
    assert rest['query_coverage']['actual_filters']['text_exact']=='周末去江边散步'
    assert not rest['query_coverage']['page_covers_complete_query']
    same=invoke(s,t,from_result_ref=counts['result_ref'])
    assert {k:same['query_statistics']['comments'][k] for k in expected_counts}==expected_counts
    empty=invoke(s,t,text_exact='周末去江边散步。')
    assert empty['total_count']==0  # exact, not fuzzy matching or text-based identity merging
    invalid=json.loads(tools.dispatch('query_conversation_reports',{'kind':'accounts','text_exact':'x'},s['sid'],t))
    assert not invalid['ok']


def test_sample_comparison_freezes_config_and_preserves_all_counts_across_pages(make_collection):
    s=make_collection('config');t=begin(s)
    refs=[r['report_ref'] for r in invoke(s,t,'list_conversation_reports')['reports']]
    s['reports']['B'].configs.create(job_id=s['reports']['B'].task,audit_config={'rule_snapshot':{'rules':['LIVE-V9']}})
    result=invoke(s,t,'compare_conversation_config',report_refs=refs,include_samples=True,limit=1)
    assert result['kind']=='report_comparison' and result['total_count']==4 and result['has_more']
    assert result['sample_membership_counts']=={'both':1,'only_first':1,'only_second':2}
    a,b=result['report_statistics']
    assert (a['original_post_count'],b['original_post_count'])==(1,2)
    assert (a['high_comment_count'],b['high_comment_count'])==(1,2)
    assert (a['high_fraction_among_completed_comments'],b['high_fraction_among_completed_comments'])==(0.5,2/3)
    assert result['risk_transition_counts']==[{'first_risk':'low','second_risk':'high','original_comment_count':1}]
    assert result['items'][0]['text_changed'] is False
    assert result['configs'][1]['ruleset_revision']['version']=='v2'
    assert 'LIVE-V9' not in json.dumps(result)
    finish(s,t);t=begin(s)
    rest=invoke(s,t,'read_conversation_result',result_ref=result['result_ref'],offset=1,limit=50)
    assert len(rest['items'])==3 and rest['report_statistics']==result['report_statistics']
    denied=json.loads(tools.dispatch('compare_conversation_config',{'report_refs':[refs[0],'outside'],
            'include_samples':True},s['sid'],t))
    assert not denied['ok']
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id=?",(s['reports']['A'].task,))
    assert not json.loads(tools.dispatch('read_conversation_result',{'result_ref':result['result_ref']},s['sid'],t))['ok']


def test_unassessed_comments_and_changed_text_do_not_manufacture_risk_transition():
    sources=[{'report_ref':x,'label':x} for x in ('A','B')]
    rows=[{'evidence_key':'shared','kind':'comment_author','text':text,'risk_level':'high',
           'audit_status':status,'source':source}
          for source,text,status in [(sources[0],'before','pending'),(sources[1],'after','completed')]]
    items,summary=compare_samples(rows,sources)
    assert items[0]['text_changed'] is True and items[0]['risk_transition'] is None
    assert summary['report_statistics'][0]['high_fraction_among_completed_comments'] is None
    assert summary['report_statistics'][0]['uncompleted_comment_count']==1
    assert summary['risk_transition_counts']==[]
