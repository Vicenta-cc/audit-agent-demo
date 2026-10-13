"""Delivery source authorization and turn isolation regressions."""
import json

import pytest

from test_report_collection import collection, creation_stack, begin, finish
from test_report_collection_analysis import make_collection
from test_report_scope_repair import ask, setup, delivered
from backend.report_collection.batches import load
from backend.report_collection.delivery import pending_delivery


def test_history_access_note_uses_reauthorized_result_sources(collection):
    s=setup(collection);t=begin(s)
    history=ask(s,t,scope='authorized_history')
    raw=s['collection'].result(history['result_ref'],s['sid'],s['collection'].sources(s['sid'],t))
    labels=[r['label'] for r in raw['sources']]
    result=ask(s,t,'deliver_conversation_results',result_refs=[history['result_ref']],report_labels=labels+['D'])
    assert 'C\\-SECRET' in result['text'] and 'D\\-SECRET' not in result['text']
    assert '未读取、未参与统计' not in result['text']
    assert all(f'- **{label}**：' not in result['text'] for label in labels)
    assert '不能据此判断其他历史报告的权限' in result['text']
    assert {r['label'] for r in ask(s,t,'list_conversation_reports')['reports']}=={'A','B'}
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id=?",(s['reports']['C'].task,))
    with pytest.raises(PermissionError):pending_delivery(s['collection'],s['sid'],t)


def test_new_turn_does_not_append_old_delivery_or_unselected_query(make_collection):
    s=setup(make_collection('paging'));service=s['collection'];t=begin(s)
    result=ask(s,t)
    first=ask(s,t,'deliver_conversation_results',result_refs=[result['result_ref']],batch_size=5)
    delivered(s,t,first);t=begin(s)
    ask(s,t,risk_level='high')
    assert pending_delivery(service,s['sid'],t) is None
    following=ask(s,t,'deliver_conversation_results',mode='continue')
    assert following['ranges'][0]['start']==5 and following['row_counts']==[5]
    assert pending_delivery(service,s['sid'],t)['delivery_ref']==following['delivery_ref']
    assert pending_delivery(service,s['sid'],t)['result_refs']==first['result_refs']
