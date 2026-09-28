"""Read-only configuration cards for runs that have already been confirmed."""
from .contracts import (
    ConfirmationPreview,
    ConfirmedConfigurationSnapshotV4,
    InvestigationRun,
    InvestigationTaskParameters,
    RecallPlanPreview,
)


def confirmed_run_preview(run: InvestigationRun) -> ConfirmationPreview:
    snapshot = ConfirmedConfigurationSnapshotV4.model_validate(run.confirmed_configuration)
    if (snapshot.draft_id, snapshot.draft_revision) != (run.draft_id, run.draft_revision):
        raise ValueError("Frozen configuration does not match the confirmed run")
    execution = snapshot.execution
    recall = snapshot.recall_plan
    recall_preview = RecallPlanPreview(strategy="none")
    if recall is not None:
        recall_preview = RecallPlanPreview(
            **recall.model_dump(mode="json"),
            enabled_main_term_count=len(recall.enabled_main_terms),
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
    return ConfirmationPreview(
        draft_id=snapshot.draft_id,
        draft_revision=snapshot.draft_revision,
        title=snapshot.title,
        objective=snapshot.objective,
        mode=snapshot.mode,
        platform=snapshot.platform,
        resolved_search_terms=list(snapshot.resolved_search_terms),
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
