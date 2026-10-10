"""Returned account evidence must not include records rejected by the intersection."""
import pytest
from test_investigation_creation_conversation import creation_stack
from test_report_collection import collection, begin, call


@pytest.mark.parametrize('agent_driven',[False,True])
def test_account_evidence_count_uses_matching_entities_before_paging(collection,agent_driven):
    s=collection;s['collection'].agent_driven=agent_driven;t=begin(s)
    common=call(s,t,'query_conversation_reports',kind='accounts',scope='all',common_only=True,limit=1)
    assert common['total_count']==2 and common['has_more']
    assert common['unique_evidence_count']==2 # one shared post and one shared comment
    assert common['unresolved_identity_comments']==1 # still disclose unresolved coverage
    rest=call(s,t,'read_conversation_result',result_ref=common['result_ref'],offset=1,limit=1)
    assert rest['unique_evidence_count']==2 and len(rest['items'])==1
    all_accounts=call(s,t,'query_conversation_reports',kind='accounts',scope='all')
    assert all_accounts['total_count']==4 and all_accounts['unique_evidence_count']==4
    assert all_accounts['unresolved_identity_comments']==1
    high=call(s,t,'query_conversation_reports',kind='accounts',scope='all',risk_level='high')
    assert high['total_count']==3 and high['unique_evidence_count']==3
    assert high['unresolved_identity_comments']==1
    comments=call(s,t,'query_conversation_reports',kind='comments',scope='all')
    assert comments['total_count']==5 and comments['unique_evidence_count']==4


def test_account_count_uses_full_evidence_not_observation_preview(collection,monkeypatch):
    s=collection;s['collection'].agent_driven=True;t=begin(s)
    rows=[{'account_ref':'account-x','account_name':'X','kind':'comment_author',
           'evidence_key':f'evidence-{i}','risk_level':'high','audit_status':'completed',
           'source':{'report_ref':source['ref']}}
          for source in s['collection'].sources(s['sid'],t) for i in range(25)]
    monkeypatch.setattr(s['collection'],'observations',lambda sources:rows)
    result=call(s,t,'query_conversation_reports',kind='accounts',scope='all',common_only=True)
    assert result['unique_evidence_count']==25
    account=result['items'][0]
    assert account['observations_truncated'] and len(account['observations'])==20
    assert account['unique_comment_count']==25 and account['observation_count']==50
