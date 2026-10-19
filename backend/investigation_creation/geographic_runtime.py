"""Server-owned geographic confirmation bound to one dedicated runtime."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import json
import sqlite3
import tempfile

from backend.audit_agent import triage
from backend.audit_agent.config import settings
from backend.audit_agent.task_settings import default_task_parameters
from backend.audit_agent.triage_matcher import terms_from_rows
from backend.audit_agent.frozen_risk import engine_fingerprint, freeze_risk_inputs
from backend.audit_agent.geographic_p3_runtime import freeze_p3_inputs, derive_scope_context
from . import rounds
from .contracts import InvestigationDraftConfiguration
from .scope_search_plan import P1_COMMIT, digest, build_confirmed_scope_search_plan


@contextmanager
def hold_geographic_claim(store, run_id, claim_token):
    """Serialize a short commit with claim replacement; never wrap network I/O."""
    from .errors import LeaseLostError
    connection = store._connect()
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            owned = connection.execute(
                "SELECT 1 FROM investigation_runs WHERE id=? AND claim_token=? "
                "AND status IN ('RUNNING','REPORT_GENERATING')", (run_id, claim_token)).fetchone()
            if not claim_token or owned is None:
                raise LeaseLostError('geographic execution has no current Run claim')
            yield
    finally:
        connection.close()


class IsolatedGeographicCapability:
    """Explicit server dependency; never accepted from request JSON or settings."""
    def __init__(self, root, *, runtime_manifest=None):
        self.root = Path(root).resolve()
        temporary = Path(tempfile.gettempdir()).resolve()
        if runtime_manifest is not None:
            # Only the trusted server entry supplies this file. Request JSON
            # cannot opt a shared/production database into geographic execution.
            manifest = Path(runtime_manifest).resolve()
            config = json.loads(manifest.read_text())
            repository = Path(config['repository']).resolve()
            if (manifest != self.root / 'runtime.json' or config.get('schema_version') != 1
                    or config.get('geographic_runtime') is not True
                    or Path(config['runtime_root']).resolve() != self.root
                    or self.root in (Path('/'), Path.home().resolve(), temporary)
                    or self.root.is_relative_to(repository) or repository.is_relative_to(self.root)):
                raise ValueError('a dedicated manifest-owned geographic isolation root is required')
        elif self.root == temporary or not self.root.is_relative_to(temporary):
            raise ValueError('a dedicated temporary isolation root is required')
        self._confirmation = ContextVar('geographic_confirmation', default=None)

    @classmethod
    def from_release_manifest(cls, path):
        """Opt in from a pinned release, without legacy overlays.

        The launcher verifies all artifact hashes before starting API or worker.
        Turn subprocesses validate the same schema and source/root binding here;
        they must not rehash the browser and wheelhouse on every model turn.
        """
        from deploy.camoufox.control import read_json, validate

        manifest = Path(path).absolute()
        if manifest != manifest.resolve():
            raise ValueError('geographic release manifest must not use symlinks')
        config = validate(read_json(manifest))
        if not config.get('geographic_runtime', False):
            return None
        root = manifest.parent
        repository = Path(__file__).resolve().parents[2]
        source = Path(config['artifacts']['app']['path'])
        runtime = config['runtime']
        if (source != repository or source != source.resolve()
                or root in (Path('/'), Path.home().resolve(), Path(tempfile.gettempdir()).resolve())
                or root.is_relative_to(repository) or repository.is_relative_to(root)
                or Path(runtime['state_root']).resolve() != root / 'state'
                or not all(Path(runtime[key]).resolve().is_relative_to(root)
                           for key in ('scheduler_db', 'account_db', 'owner_db', 'archive_root'))):
            raise ValueError('a dedicated source-bound release geographic isolation root is required')
        capability = cls.__new__(cls)
        capability.root = root
        capability._confirmation = ContextVar('geographic_confirmation', default=None)
        return capability

    def allows_path(self, path):
        return Path(path).resolve().is_relative_to(self.root)

    def install(self, application):
        paths = (application.store.db_path, application.conversation_store.db_path,
                 application.resource_service.resource_db_path)
        if not all(self.allows_path(p) for p in paths):
            raise ValueError('geographic capability database outside isolation root')
        application.geographic_capability = self
        application.store.geographic_capability = self
        application.resource_service.geographic_capability = self

    @contextmanager
    def confirmation(self, verify):
        token = self._confirmation.set(verify)
        try:
            yield
        finally:
            self._confirmation.reset(token)

    def verify_confirmation(self, draft, raw_plan):
        verify = self._confirmation.get()
        if verify is None:
            raise ValueError('geographic confirmation requires server authority fence')
        expected = verify(draft)
        if expected != raw_plan:
            raise ValueError('geographic plan differs from current authoritative capture')


def capture_confirmed_authority(application, draft, *, principal, resource_connection):
    session_id = draft.configuration.relevance_scope_state.session_id
    resources=application.resource_service
    cfg=draft.configuration
    if not isinstance(cfg,InvestigationDraftConfiguration): raise ValueError('internal v4 draft required')
    provenance=_provenance(application,draft,principal,session_id)
    conn = resource_connection
    saved=resources.task_settings.get(conn)
    # Actual resolver validates access, published identities, adopted contents,
    # compiler output and configured crawler accounts, without creating a Run.
    resolution=resources.resolve_confirmation(draft,principal=principal,resource_connection=conn)
    judgement=cfg.judgement
    if judgement.strategy=='temporary_ruleset':
        ruleset_content=judgement.content
        ruleset_source=judgement.model_dump(mode='json',exclude={'content'})
    else:
        revision=resources.ruleset_service.get_published(judgement.ruleset_revision_id,principal=principal,connection=conn)
        _,ruleset_content=resources._ruleset_revision_resource(revision)
        ruleset_source=resolution.ruleset_revision.model_dump(mode='json')
    plan=cfg.investigation.recall_plan
    lexicon=(resources.lexicon_editor_content(plan.lexicon_id,connection=conn) if plan.strategy=='existing_lexicon' else plan.lexicon_content)
    runtime_terms=(resources.lexicon_store.enabled_search_terms(plan.lexicon_id,connection=conn) if plan.strategy=='existing_lexicon' else [])
    adoption = _adoption_bindings(application, cfg, provenance, principal, session_id)
    category_ids=list(resolution.execution.library_ids)
    # Keep risk reads within the same resource writer fence as resolution.
    risk_rows=resources.lexicon_store.triage_terms(category_ids) if category_ids else []
    risk_groups=resources.lexicon_store.search_word_groups(category_ids) if category_ids else {}
    frozen_risk=freeze_risk_inputs(category_ids=category_ids,terms=terms_from_rows(risk_rows),search_groups=risk_groups,
        rules=resolution.execution.prompt_profile_snapshot,
        model={'name':settings.triage_model,'max_comments':settings.triage_candidate_comments,'request_timeout':settings.triage_request_timeout},
        prompt={'version':'p1-triage-031e0cd','engine_sha256':engine_fingerprint()},
        scoring={**{k:getattr(settings,'triage_'+k) for k in ('rule_score_high','rule_score_medium','rule_score_low','rule_score_cap')},
            'model_scores':dict(triage.MODEL_SCORES),'prompt_hit_limit':triage.PROMPT_HIT_LIMIT,'discard_score':triage.DISCARD_SCORE},
        identity={'max_followers_personal_verified':settings.triage_max_followers_personal_verified,
            'max_followers_unverified':settings.triage_max_followers_unverified,'official_verify_patterns':list(settings.triage_official_verify_patterns)})
    profile=freeze_p3_inputs(model=settings.triage_model,
        model_config_source={'origin':'confirmed_run','reference':'P1 settings.triage_model (identifier only; no credentials)'})
    context=derive_scope_context(cfg.relevance_scope_state.model_dump(mode='json'),profile)
    return dict(identity=provenance['identity'],title=draft.title,objective=draft.objective,
        configuration=cfg.model_dump(mode='json'),provenance=provenance,resolution=resolution.model_dump(mode='json'),
        resources=dict(ruleset_content=ruleset_content.model_dump(mode='json'),ruleset_source=ruleset_source,
            lexicon_content=lexicon.model_dump(mode='json') if lexicon else None,recall_source=plan.model_dump(mode='json'),
            lexicon_runtime_terms=runtime_terms,adoption_bindings=adoption),
        settings=dict(revision=saved['revision'],saved=saved['parameters'],defaults=default_task_parameters().model_dump(mode='json'),
            limits={**{k:getattr(settings,k) for k in ('investigation_max_posts','m3_comments_per_post','crawler_max_concurrency','search_terms_max','triage_candidates_per_keyword','triage_candidate_comments','triage_mode')},
                'analysis_batch_size':5,'picks_per_search_term':1},derivation_version='p1-task-settings-031e0cd',configuration_source='confirmed_run:unified-task-settings'),
        risk_inputs=frozen_risk,p3_inputs=profile,scope_context=context,p1_commit=P1_COMMIT)


@contextmanager
def _read(path):
    connection=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _scope_key(raw):
    raw=json.loads(json.dumps(raw)); state=raw.get('relevance_scope_state',{})
    state.pop('draft_id',None);state.pop('copied_from_draft_id',None)
    return {'scope':raw.get('relevance_scope'),'state':state}


def _adoption_bindings(application, cfg, provenance, principal, session_id):
    """Read adoption receipts, never require the live editor to stay unchanged."""
    from backend.investigation_creation.approval import published_presentations

    bindings = []
    with _read(application.store.db_path) as db, _read(application.conversation_store.db_path) as conv:
        db.execute('BEGIN')
        conv.execute('BEGIN')
        recall = cfg.investigation.recall_plan
        if recall.strategy == 'temporary_terms' and recall.source_edit_ref:
            row = db.execute('SELECT r.*, o.principal_id AS editor_owner, o.session_id AS editor_session, '
                'h.content_json FROM lexicon_edit_refs r '
                'JOIN resource_edit_origins o ON o.edit_id=r.edit_id '
                'JOIN resource_edit_history h ON h.edit_id=r.edit_id AND h.version=r.version '
                'WHERE r.ref=?', (recall.source_edit_ref,)).fetchone()
            if (row is None or row['principal_id'] != principal.id or row['session_id'] != session_id
                    or row['editor_owner'] != principal.id or row['editor_session'] != session_id
                    or recall.lexicon_content is None
                    or digest(json.loads(row['content_json'])) != row['content_hash']
                    or digest(recall.lexicon_content.storage_dict()) != row['content_hash']
                    or json.loads(row['source_ids_json']) != recall.source_lexicon_ids):
                raise ValueError('temporary lexicon adoption reference is not bound to this snapshot')
            bindings.append(dict(kind='temporary_lexicon', source_edit_ref=row['ref'],
                principal_id=row['principal_id'], session_id=row['session_id'], edit_id=row['edit_id'],
                version=row['version'], content_hash=row['content_hash'],
                source_lexicon_ids=json.loads(row['source_ids_json'])))
        rule = cfg.judgement
        if rule.strategy == 'temporary_ruleset':
            history = {(r['draft_id'], r['revision']): r['configuration'] for r in provenance['history']}
            approvals = db.execute('SELECT a.* FROM ruleset_proposal_approvals a '
                'JOIN investigation_drafts d ON d.id=a.draft_id AND d.owner_principal=? '
                'WHERE a.session_id=? AND a.proposal_id=? AND a.proposal_version=? AND a.content_hash=? '
                'ORDER BY a.rowid DESC', (principal.id, session_id, rule.proposal_id,
                    rule.proposal_version, rule.content_hash)).fetchall()
            approval = next((a for a in approvals if history.get((a['draft_id'], a['draft_revision']), {})
                .get('judgement') == rule.model_dump(mode='json')), None)
            if approval is None:
                raise ValueError('missing durable temporary ruleset adoption')
            message = conv.execute('SELECT m.* FROM investigation_messages m '
                'JOIN investigation_turns t ON t.id=m.turn_id AND t.user_message_id=m.id '
                'JOIN investigation_sessions s ON s.id=t.session_id '
                "WHERE m.id=? AND t.id=? AND m.session_id=? AND t.session_id=m.session_id "
                "AND m.role='user' AND s.owner_principal=? AND s.scope_type='creation'",
                (approval['approving_user_message_id'], approval['approving_user_turn_id'],
                    session_id, principal.id)).fetchone()
            if message is None:
                raise ValueError('missing actual adopting user message')
            presentations, _ = published_presentations(conv, session_id=session_id,
                before_sequence=message['sequence'])
            matches = [p for p in presentations if p['presentation_id'] == approval['presentation_id']]
            if (len(matches) != 1 or matches[0]['assistant_message_id'] != approval['presentation_assistant_message_id']
                    or matches[0]['proposal_id'] != rule.proposal_id
                    or matches[0]['proposal_version'] != rule.proposal_version
                    or matches[0]['content_hash'] != rule.content_hash
                    or matches[0]['snapshot']['content'] != rule.content.model_dump(mode='json')):
                raise ValueError('temporary ruleset presentation/adoption mismatch')
            bindings.append(dict(kind='temporary_ruleset', principal_id=principal.id,
                **{k:approval[k] for k in ('approval_id', 'session_id', 'draft_id', 'draft_revision',
                    'proposal_id', 'proposal_version', 'content_hash', 'presentation_id',
                    'approving_user_message_id', 'approving_user_turn_id', 'presentation_assistant_message_id')},
                presentation=matches[0]))
    return bindings


def _provenance(application,draft,principal,session_id):
    cfg=draft.configuration.model_dump(mode='json'); state=cfg['relevance_scope_state']
    identity=dict(owner=principal.id,session_id=session_id,draft_id=draft.id,revision=draft.current_revision)
    with _read(application.conversation_store.db_path) as conv, _read(application.store.db_path) as db:
        session=conv.execute("SELECT * FROM investigation_sessions WHERE id=? AND owner_principal=? AND scope_type='creation'",(session_id,principal.id)).fetchone()
        if session is None: raise ValueError('session owner/scope mismatch')
        members=rounds.draft_ids(db,session_id,principal.id)
        if not members or members[-1]!=draft.id: raise ValueError('not current task draft')
        rows=[dict(r) for r in db.execute('SELECT * FROM investigation_draft_revisions WHERE draft_id IN ('+','.join('?' for _ in members)+') ORDER BY rowid',members)]
        history=[dict(draft_id=r['draft_id'],revision=r['revision'],configuration=json.loads(r['configuration_json'])) for r in rows]
        selection=None
        if state['source_kind']=='ui_selection':
            event=db.execute('SELECT * FROM investigation_region_selections WHERE principal=? AND session_id=? AND operation_id=?',(principal.id,session_id,state['source_operation_id'])).fetchone()
            if event is None: raise ValueError('missing actual selection record')
            origin_id,origin_rev=event['draft_id'],event['committed_revision']
            selected=next(r for r in history if r['draft_id']==origin_id and r['revision']==origin_rev)
            selection={k:event[k] for k in ('principal','session_id','draft_id','operation_id','expected_revision','committed_revision','target_text','region_code','catalog_version','conversation_sequence')}
            selection['selection_policy']=selected['configuration']['relevance_scope_state']['selection_policy']
        else:
            matching=[r for r in history if _scope_key(r['configuration'])==_scope_key(cfg)]
            if not matching: raise ValueError('missing source revision')
            origin_id,origin_rev=matching[0]['draft_id'],matching[0]['revision']
        origin=next(r['configuration'] for r in history if (r['draft_id'],r['revision'])==(origin_id,origin_rev))
        lineage=[]; current_id=draft.id; seen=set()
        while current_id!=origin_id:
            if current_id in seen: raise ValueError('cyclic copy lineage')
            seen.add(current_id)
            target=next(r['configuration'] for r in history if r['draft_id']==current_id and r['revision']==1)
            source_id=target['relevance_scope_state']['copied_from_draft_id']
            source=db.execute('SELECT * FROM investigation_drafts WHERE id=? AND owner_principal=?',(source_id,principal.id)).fetchone()
            operation=db.execute("SELECT o.id FROM draft_creation_operations o JOIN draft_creation_attempts a ON a.operation_id=o.id JOIN investigation_creation_tool_receipts r ON r.receipt_id=a.receipt_id WHERE o.draft_id=? AND o.principal=? AND o.session_id=? AND o.state='CREATED' AND r.tool_name='copy_investigation_draft' AND r.status='SUCCEEDED'",(current_id,principal.id,session_id)).fetchone()
            if source is None or operation is None: raise ValueError('unverified copy operation')
            lineage.insert(0,dict(principal=principal.id,session_id=session_id,operation_id=operation['id'],
                source_draft_id=source_id,target_draft_id=current_id,source_revision=source['confirmed_revision'],
                source_status=source['status'],source_configuration=json.loads(source['configuration_json']),target_configuration=target))
            current_id=source_id
        def message(turn_id):
            row=conv.execute("SELECT m.* FROM investigation_messages m JOIN investigation_turns t ON t.user_message_id=m.id AND t.id=m.turn_id WHERE t.id=? AND t.session_id=? AND m.session_id=t.session_id AND m.role='user'",(turn_id,session_id)).fetchone()
            if row is None: raise ValueError('missing real source user message')
            return {**{k:row[k] for k in ('id','turn_id','sequence','content','role','session_id')},'owner':session['owner_principal']}
        messages=([None]*3 if selection else [message(state[k]) for k in ('source_turn_id','application_turn_id','task_anchor_turn_id')])
        return dict(authority_origin='confirmed_run',identity=identity,session_owner=session['owner_principal'],session_scope_type=session['scope_type'],
            task_draft_ids=members,origin_draft_id=origin_id,origin_revision=origin_rev,origin_configuration=origin,
            history=history,lineage=lineage,selection=selection,message=messages[0],application_message=messages[1],anchor_message=messages[2])


def install_runtime_geographic_capability(application, conversation_store, *, environ=None):
    """Shared API/turn composition; release configuration has one authority."""
    import os

    env = os.environ if environ is None else environ
    release = env.get('CAMOUFOX_RELEASE_MANIFEST')
    legacy = env.get('AUDIT_RUNTIME_MANIFEST')
    if release and legacy:
        raise ValueError('release and legacy runtime manifests cannot be combined')
    capability = None
    if release:
        capability = IsolatedGeographicCapability.from_release_manifest(release)
    elif legacy:
        manifest = Path(legacy)
        config = json.loads(manifest.read_text())
        if config.get('geographic_runtime') is True:
            capability = IsolatedGeographicCapability(config['runtime_root'], runtime_manifest=manifest)
    if capability is not None:
        application.conversation_store = conversation_store
        capability.install(application)
    return capability
