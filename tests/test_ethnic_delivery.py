import json

import pytest

from scripts.ethnic_resources import install, TERMS, LEXICON_ID
from backend.audit_agent.lexicon_store import LexiconStore


def test_install_is_idempotent_and_compiles_k(tmp_path):
    first = install(tmp_path)
    assert install(tmp_path) == first
    assert len(TERMS) == 17
    lexicons = LexiconStore(tmp_path / 'audit_index.sqlite3')
    assert lexicons.enabled_main_terms(LEXICON_ID) == TERMS
    assert lexicons.enabled_search_keywords(LEXICON_ID) == TERMS
    assert first['inference_settings']['comment_audit']['enable_thinking'] is True
    assert first['inference_settings']['fusion_audit']['enable_thinking'] is True
    assert json.loads((tmp_path / 'ethnic-resources.json').read_text()) == first


def test_import_preserves_changed_existing_lexicon(tmp_path):
    store = LexiconStore(tmp_path / 'audit_index.sqlite3')
    store.upsert_category(category_id=LEXICON_ID, title='User lexicon', terms=['自定义'])
    with pytest.raises(RuntimeError, match='no overwrite'):
        install(tmp_path)
    assert store.enabled_main_terms(LEXICON_ID) == ['自定义']
