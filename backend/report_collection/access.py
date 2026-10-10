"""Collection membership requires proven ownership even in local auth mode."""

def owns_report(store, principal, item, historical_service=None):
    if historical_service and historical_service.is_historical_report_version(item['id']):
        try:
            historical_service.require_report_access(item['id'],principal_id=principal.id)
            return True
        except Exception:
            return False
    owners=store.owner_principals_for_report(report_version_id=item['id'],task_id=item['task_id'])
    admission=store.admission.for_task(item['task_id'])
    return principal.id in owners or bool(admission and admission['owner_id']==principal.id)
