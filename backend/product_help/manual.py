"""Read the same HTML manual shipped to the browser; never send base64 to an LLM."""
from functools import lru_cache
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import base64
import re

MANUAL = Path(__file__).resolve().parents[2] / 'Audit_assistant/public/manual.html'
# Query vocabulary only; actual instructions always come from the shipped manual.
TOPICS = {
 's1': '新手 入门 第一次 快速 上手 教学 教程 怎么用',
 's2': '限制 额度 次数 有效期 权限 可见',
 's3': '登录 登陆 密码 退出 账号有效期',
 's4': '界面 工作区 侧栏',
 's5': '发起 创建 新建 开始 调查 配置 搜索词 确认',
 's6': '进度 等待 排队 多久 耗时',
 's7': '报告 证据 查看 下载 导出 分析记录 OCR ASR',
 's8': '采集账号 扫码 登录失效 公共账号 私有账号',
 's9': '审核规则 修改规则 保存规则',
 's10': '黑话库 词库 修改词 搜索词库',
 's11': '重点用户', 's12': '错误 失败 提示 处理 无法',
}

class ManualParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.sections = []; self.assets = {}; self.section = None
        self.heading = None; self.caption = False; self.ignore = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'): self.ignore += 1
        if tag == 'section':
            self.section = {'section_id': attrs['id'], 'title': '', 'parts': [], 'images': []}
            self.sections.append(self.section)
        if not self.section: return
        if tag == 'h2': self.heading = 'h2'
        if tag == 'figcaption': self.caption = True
        if tag in ('p', 'h2', 'h3', 'li', 'tr', 'br', 'figcaption'): self.section['parts'].append('\n')
        if tag in ('td', 'th'): self.section['parts'].append(' | ')
        if tag == 'img' and attrs.get('src', '').startswith('data:image/jpeg;base64,'):
            data = base64.b64decode(attrs['src'].split(',', 1)[1], validate=True)
            key = sha256(data).hexdigest(); self.assets[key] = data
            self.section['images'].append({'image_id': key, 'alt': attrs.get('alt', ''),
                'caption': '', 'url': '/api/user-manual/images/' + key})

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.ignore = max(0, self.ignore - 1)
        if tag == 'h2': self.heading = None
        if tag == 'figcaption': self.caption = False
        if tag == 'section': self.section = None

    def handle_data(self, text):
        if not self.section or self.ignore: return
        self.section['parts'].append(text)
        if self.heading: self.section['title'] += text
        if self.caption and self.section['images']: self.section['images'][-1]['caption'] += text

@lru_cache(maxsize=2)
def _load(path, mtime):
    data = Path(path).read_bytes(); parser = ManualParser(); parser.feed(data.decode('utf-8'))
    for section in parser.sections:
        section['text'] = re.sub(r'\n\s*\n+', '\n', ''.join(section.pop('parts'))).strip()
        section['source_url'] = '/manual.html#' + section['section_id']
    return sha256(data).hexdigest()[:16], parser.sections, parser.assets

def load_manual():
    return _load(str(MANUAL), MANUAL.stat().st_mtime_ns)

def read_manual(query='', section_id=''):
    version, sections, _ = load_manual()
    directory = [{k: s[k] for k in ('section_id', 'title', 'source_url')} for s in sections]
    if section_id:
        selected = [s for s in sections if s['section_id'] == section_id]
        if not selected: raise ValueError('章节不存在，请使用目录中的 section_id。')
    elif query:
        q = query.lower()
        def score(s):
            words = TOPICS.get(s['section_id'], '').lower().split()
            return sum(len(w) for w in words if w in q) + (8 if s['title'] in query else 0)
        selected = sorted((s for s in sections if score(s)), key=lambda s: -score(s))[:3]
    else: selected = []
    results = []; budget = 2
    for s in selected:
        pictures = s['images'][:budget]; budget -= len(pictures)
        results.append({**s, 'images': pictures})
    return {'manual_version': version, 'source_url': '/manual.html', 'sections': results,
            'directory': directory if not selected else [], 'matched': bool(selected),
            'scope': '平台通用操作说明，不代表当前账号额度、任务状态或采集账号可用性。',
            'image_usage': '可用 ![图注](url) 展示返回的截图，保留示意/演示数据说明；只解释手册文字，不能声称看过图片内部细节。'}
