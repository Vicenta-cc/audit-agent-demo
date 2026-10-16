"""Read-only configuration cards for runs that have already been confirmed."""
from backend.audit_agent.search_terms_cap import unused_terms_notice
from .contracts import (
    ConfirmationPreview,
    ConfirmedConfigurationSnapshotV4,
    InvestigationDraftConfiguration,
    InvestigationRun,
    InvestigationTaskParameters,
    RecallPlanPreview,
    parse_confirmed_configuration_snapshot,
)
from .scope_intent import project_state


def confirmed_run_preview(run: InvestigationRun) -> ConfirmationPreview:
    snapshot = parse_confirmed_configuration_snapshot(run.confirmed_configuration)
    if not isinstance(snapshot, ConfirmedConfigurationSnapshotV4):
        raise ValueError("Frozen preview requires a v4-compatible configuration")
    if (snapshot.draft_id, snapshot.draft_revision) != (run.draft_id, run.draft_revision):
        raise ValueError("Frozen configuration does not match the confirmed run")
    execution = snapshot.execution
    recall = snapshot.recall_plan
    recall_preview = RecallPlanPreview(strategy="none")
    unused = list(snapshot.unused_search_terms)
    if recall is not None:
        recall_preview = RecallPlanPreview(
            # The adoption reference belongs to frozen provenance, not the
            # public preview contract. Keep it in the snapshot unchanged.
            **recall.model_dump(mode="json", exclude={"source_edit_ref"}),
            # Searched terms plus the ones cut by the cap: the lexicon's real count.
            enabled_main_term_count=len(recall.enabled_main_terms) + (
                len(unused) if recall.strategy == "existing_lexicon" else 0),
        )

    # Never read today's global settings, account availability or resource library.
    # Older runs may omit optional execution fields; use their saved request where
    # available, keeping effective values from the execution snapshot authoritative.
    parameters = (
        snapshot.requested_parameters.model_dump(mode="json")
        if snapshot.requested_parameters is not None else {}
    )
    parameters.update({
        key: value for key, value in execution.model_dump(mode="json").items()
        if key in InvestigationTaskParameters.model_fields and value is not None
    })
    total = execution.max_total_notes or snapshot.max_notes
    parameters["max_total_notes"] = total
    effective = InvestigationTaskParameters.model_validate(parameters)
    # The snapshot parser has validated the v5 plan and its binding to this run.
    # Restore geography from that frozen input, never the current mutable draft.
    geographic_configuration = (
        InvestigationDraftConfiguration.model_validate(
            execution.geographic_plan["inputs"]["configuration"]
        ) if execution.geographic_plan is not None else None
    )
    return ConfirmationPreview(
        relevance_scope=(geographic_configuration.relevance_scope
                         if geographic_configuration is not None else None),
        relevance_scope_state=(project_state(geographic_configuration.relevance_scope_state)
                               if geographic_configuration is not None else None),
        draft_id=snapshot.draft_id,
        draft_revision=snapshot.draft_revision,
        title=snapshot.title,
        objective=snapshot.objective,
        mode=snapshot.mode,
        platform=snapshot.platform,
        resolved_search_terms=list(snapshot.resolved_search_terms),
        unused_search_terms=unused,
        # The cap in force at confirmation, never today's setting.
        search_terms_notice=unused_terms_notice(unused, len(snapshot.resolved_search_terms)),
        creator_url=snapshot.creator_url,
        recall_plan=recall_preview,
        ruleset_revision=snapshot.ruleset_revision,
        temporary_ruleset=snapshot.temporary_ruleset,
        requested_parameters=snapshot.requested_parameters,
        effective_parameters=effective,
        estimated_max_contents=total,
        max_notes=total,
        max_posts_per_keyword=execution.max_notes,
        max_comments_per_post=execution.max_comments,
        get_sub_comment=execution.get_sub_comment,
        blockers=[],
        can_confirm=False,
    )
