"""Account-bound historical queries; no natural-language routing or model retry."""
from urllib.parse import unquote, urlsplit

from backend.audit_agent.creator_url import validate_creator_url
from .service import reference


def profile_account(value):
    """Reuse creator URL validation and the observation identity convention."""
    value = validate_creator_url('dy', value)
    key = unquote(urlsplit(value).path.strip('/').split('/')[-1])
    if not key or any(c.isspace() or c in '/\\?#' or ord(c) < 32 for c in key):
        raise ValueError('账号主页中的平台身份无效。')
    return reference('account', ['douyin', key])


def resolve_identity(args):
    args = dict(args)
    if args.get('account_url'):
        ref = profile_account(args['account_url'])
        if args.get('account_ref') and args['account_ref'] != ref:
            raise ValueError('account_url 与 account_ref 指向不同账号；未执行查询。')
        args['account_ref'] = ref
    return args


def historical(args, selected):
    return args.get('scope') == 'authorized_history' or any(
        s.get('domain') == 'authorized_history' for s in selected)


def constrain(service, name, args, session, sources, selected):
    args = resolve_identity(args)
    if not historical(args, selected):
        return args
    if name not in {'query_conversation_reports', 'query_account_activity'}:
        raise ValueError('跨会话历史仅支持账号穿透，不能展开整份报告或其帖子证据；'
                         '请用账号评论查询或 query_account_activity，当前会话报告仍可深入读取。未执行查询。')
    # A continuation may reuse a previously explicit account, never a guessed
    # identity or the sole account that happened to occur on a preview page.
    parent = args.get('from_result_ref')
    previous = (service.result(parent, session, sources) if parent else
                service.previous(session, sources) if args.get('scope') == 'inherit' else None)
    if previous and not args.get('account_ref') and not args.get('name_contains'):
        args['account_ref'] = previous.get('history_contract', {}).get('account_ref', '')
        if parent and previous['kind'] == 'account_candidates' and len(previous['items']) == 1:
            args['account_ref'] = previous['items'][0]['account_ref']
    candidate = (name == 'query_conversation_reports' and args['kind'] == 'accounts'
                 and (args.get('name_contains') or args.get('account_url')))
    if not args.get('account_ref') and not candidate:
        raise ValueError('历史内容查询必须限定明确账号；可先用 kind=accounts、name_contains '
                         '或 account_url 找候选，再用 account_ref 查询。不是报告权限不足。未执行查询。')
    return args


def candidates(service, args, session, turn, selected):
    """Nickname discovery returns identity and source metadata, never contents."""
    if (args.get('risk_level') or args.get('common_only') or args.get('top_n')
            or args.get('account_role','any') != 'any' or args.get('text_exact') is not None
            or args.get('sort_by','source_order') != 'source_order' or args.get('from_result_ref')):
        raise ValueError('历史身份候选不接受活动筛选或排名；请先确认候选，再用 account_ref 查询活动。未执行查询。')
    rows = service.observations(selected)
    ref = args.get('account_ref')
    term = args.get('name_contains', '').casefold()
    refs = ({ref} if ref else {r['account_ref'] for r in rows
                              if r['account_ref'] and term in r['account_name'].casefold()})
    items = []
    for account in sorted(refs):
        own = [r for r in rows if r['account_ref'] == account]
        if not own:
            continue
        items.append({'account_ref': account, 'names': sorted({r['account_name'] for r in own}),
                      'profile_urls': sorted({r['links'].get('账号主页', '') for r in own} - {''})})
    return service.save(session, turn, selected, 'account_candidates', items, args['limit'],
                        history_contract={'version': 1, 'mode': 'candidates'},
                        query_filters={k: args.get(k) for k in ('account_url', 'name_contains')},
                        limitation='仅身份候选，不是活动明细。昵称未命中不能证明主页账号没有记录；'
                                   '同名候选须按主页区分。sources 是可进一步限定的授权历史引用。')


def validate_saved(data):
    if not any(s.get('domain') == 'authorized_history' for s in data['sources']):
        return
    contract = data.get('history_contract', {})
    if contract.get('version') != 1 or not (
        contract.get('account_ref') or
        contract.get('mode') == 'candidates' and data['kind'] == 'account_candidates'
    ):
        raise PermissionError('旧历史结果未记录账号约束；请按明确账号重新查询，不能继续展开整份历史报告。')
