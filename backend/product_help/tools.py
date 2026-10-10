"""Shared product read tools, usable before and after report publication."""
import base64
from hashlib import sha256
import json
from urllib.parse import quote, urlencode
from pydantic import BaseModel, ConfigDict, Field
from .manual import read_manual

class ManualInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = Field(default='', max_length=500)
    section_id: str = Field(default='', max_length=50)

class ReportsInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    limit: int = Field(default=10, ge=1, le=20)
    cursor: str = Field(default='', max_length=2048)

INPUTS = {'read_user_manual': ManualInput, 'list_completed_reports': ReportsInput}
DESCRIPTIONS = {
 'read_user_manual': '用户问新手教学、怎么使用平台、操作步骤或手册时读取。query为自然语言问题；section_id取返回目录，可继续读章节；均省略返回目录。只读，返回手册原文、章节链接和最多两张可展示截图。不查询实时额度、不创建草案、不启动采集。用返回图片URL配图并保留图注，不编造截图内容。',
 'list_completed_reports': '用户要所有已完成调查的报告、历史报告列表或报告入口时使用；新建会话也可使用。只返回当前账号自己创建的已发布报告目录（管理员也不跨账号），每个调查取最新可读版本，含标题、发布时间、打开链接和完整总数。展示目录时将每个标题链接到返回的url，发布时间保留时区。不读取报告正文、不改变当前报告绑定。默认10条最多20条；要全部时用返回cursor继续直到has_more=false，未读完明确剩余，不能把第一页称为全部。',
}
SCHEMAS = tuple({'name': n, 'description': DESCRIPTIONS[n], 'parameters': c.model_json_schema()} for n,c in INPUTS.items())

class ProductReadTools:
    def __init__(self, *, report_store, can_read, principal_for_session):
        self.report_store = report_store; self.can_read = can_read; self.principal_for_session = principal_for_session

    def execute(self, name, arguments, principal):
        parsed = INPUTS[name].model_validate(arguments)
        if name == 'read_user_manual': return read_manual(**parsed.model_dump())
        return self.reports(principal, **parsed.model_dump())

    def reports(self, principal, limit=10, cursor=''):
        # Select only small metadata, never report bodies or account corpora.
        with self.report_store._connect() as db:
            rows = db.execute('''SELECT v.id, r.task_id, v.title, v.version_number, v.published_at
                FROM report_versions v JOIN reports r ON r.id=v.report_id
                WHERE v.status='published' ORDER BY v.version_number DESC, v.published_at DESC, v.id DESC''').fetchall()
        visible = []; seen = set()
        for row in rows:
            item = dict(row)
            if item['task_id'] in seen or not self.can_read(principal, item): continue
            seen.add(item['task_id']); visible.append(item)
        visible.sort(key=lambda p: (p['published_at'] or '', p['id']), reverse=True)
        revision = sha256(json.dumps([principal.id, visible], sort_keys=True).encode()).hexdigest()
        offset = 0
        if cursor:
            try:
                saved = json.loads(base64.urlsafe_b64decode(cursor))
                if saved['revision'] != revision: raise ValueError()
                offset = saved['offset']
                if type(offset) is not int or not 0 <= offset <= len(visible): raise ValueError()
            except Exception as exc: raise ValueError('报告目录或权限已变化，请从第一页重新查询。') from exc
        page = visible[offset:offset + limit]; end = offset + len(page)
        next_cursor = base64.urlsafe_b64encode(json.dumps({'revision': revision, 'offset': end}).encode()).decode() if end < len(visible) else ''
        return {'total_count': len(visible), 'returned_count': len(page), 'start': offset + 1 if page else 0,
            'end': end, 'has_more': end < len(visible), 'remaining_count': len(visible) - end, 'cursor': next_cursor,
            'scope': '当前账号自己创建的已发布报告；每个调查展示最新可读版本，未生成报告的任务不在此目录。',
            'reports': [{'title': p['title'], 'published_at': p['published_at'], 'version': p['version_number'],
                'url': '/investigation/' + quote(p['task_id'], safe='') + '/report?' + urlencode({'report': p['id']})} for p in page]}

_service = None

def configure(service):
    global _service
    _service = service

def execute(name, arguments, principal):
    if _service is None: raise RuntimeError('产品查询工具尚未绑定。')
    return _service.execute(name, arguments, principal)

def dispatch(name, arguments, session_id):
    try:
        if _service is None: raise RuntimeError('产品查询工具尚未绑定。')
        data = execute(name, arguments, _service.principal_for_session(session_id))
        return json.dumps({'status': 'ok', 'data': data}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({'status': 'error', 'error': {'code': 'PRODUCT_READ_FAILED', 'message': str(exc)}}, ensure_ascii=False)
