"""Install pinned, opt-in ethnic discussion resources through native stores."""
import json
from pathlib import Path

LEXICON_ID = 'ethnic_discussion_recall_v1'
TERMS = ['维汉通婚', '维汉婚姻', '维汉情侣', '维汉夫妻', '维族能和汉族结婚吗',
         '维族为什么不和汉族结婚', '维族外嫁', '维族嫁汉族', '汉族娶维族',
         '维族找对象', '维族只找本民族', '维族家里不同意', '维汉父母不同意',
         '维汉孩子', '维汉混血', '维族血统', '维族血脉']


def install(data: Path):
    from backend.audit_agent.lexicon_store import LexiconStore
    from backend.rulesets.contracts import RuleSetContent
    from backend.rulesets.compiler import content_hash, compile_ruleset_revision
    from backend.rulesets.errors import RuleSetNotFoundError
    from backend.rulesets.store import RuleSetStore
    from backend.rulesets.service import RuleSetService
    from backend.rulesets.trial_profiles import BUNDLE_DIR, K_RULESET_ID
    from backend.investigation_creation.principal import Principal

    content = json.loads((BUNDLE_DIR / 'ruleset.json').read_text())
    expected = content_hash(content)
    # Validate all pinned files before registering a resource.
    compile_ruleset_revision({'id': 'install-validation', 'ruleset_id': K_RULESET_ID,
        'status': 'published', 'version': 1, 'snapshot': content, 'content_hash': expected})
    principal = Principal('local-user')
    service = RuleSetService(RuleSetStore(data / 'audit_index.sqlite3'))
    lexicons = LexiconStore(data / 'audit_index.sqlite3')
    try:
        category = lexicons.get_category(LEXICON_ID)
    except KeyError:
        category = None
    if category is not None:
        rows = category['keywords']
        if (len(rows) != len(TERMS) or
            {r['keyword'] for r in rows} != set(TERMS) or
            any(not r['enabled'] or r['match_type'] != '平台搜索词' for r in rows)):
            raise RuntimeError('Existing ethnic lexicon differs; no overwrite. Use a new data directory or review it manually.')
    try:
        aggregate = service.get(K_RULESET_ID, principal=principal)
    except RuleSetNotFoundError:
        aggregate = None
    if aggregate and aggregate['draft']['content_hash'] != expected:
        raise RuntimeError('Existing K draft differs; no overwrite.')
    if aggregate and aggregate['published_revision_id']:
        published = service.get_published(aggregate['published_revision_id'], principal=principal)
        if published['content_hash'] != expected:
            raise RuntimeError('Existing published K differs; no overwrite.')
    else:
        if aggregate is None:
            aggregate = service.create_draft(RuleSetContent.model_validate(content),
                ruleset_id=K_RULESET_ID, principal=principal)
        published = service.publish(K_RULESET_ID, expected_revision=aggregate['draft_revision'],
            idempotency_key='install-ethnic-k-v1', principal=principal)
    compiled = service.compile_for_execution(published['id'], principal=principal)
    if category is None:
        lexicons.upsert_category(category_id=LEXICON_ID, title='民族关系公开讨论召回词库',
            risk_label='主题召回，命中不代表风险', platform_keywords=TERMS)
    receipt = {'ruleset_id': K_RULESET_ID, 'ruleset_revision_id': published['id'],
        'lexicon_id': LEXICON_ID, 'keywords': TERMS, 'config_hash': compiled['config_hash'],
        'prompt_version': compiled['prompt_profile_snapshot']['prompt_version'],
        'inference_settings': compiled['prompt_profile_snapshot']['inference_settings']}
    (data / 'ethnic-resources.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    return receipt
