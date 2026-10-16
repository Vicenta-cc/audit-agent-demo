"""Owner-side migration proof; observations never come from user JSON flags."""

from __future__ import annotations

from .crawler_browser_migration import MigrationError, get_browser_migration_service


def verify_migrated_account(migration_id, *, actor_id, service=None, commit=True):
    """Verify two terminated browser processes on one frozen route, then commit.

    The existing migration login operation authorizes this narrow continuation.
    It never creates a manual risk-recovery claim or clears account pauses.
    """
    from . import crawler_route as route

    service = service or get_browser_migration_service()
    row = service.reconcile(migration_id, actor_id=actor_id)["migration"]
    if row["phase"] == "COMMITTED":
        return row
    if row["phase"] == "ROUTE_VERIFIED":
        return service.commit(migration_id, actor_id=actor_id) if commit else row
    if row["phase"] != "IDENTITY_VERIFIED":
        raise MigrationError("IDENTITY_NOT_VERIFIED")
    operation = row["login_operation_id"]
    if not service.authorized_login(
        row["account_id"],
        owner_id=actor_id,
        migration_id=migration_id,
        operation_id=operation,
    ):
        raise MigrationError("MIGRATION_LOGIN_OPERATION_CONFLICT")
    account = service.store.get(row["account_id"])
    if (
        not account
        or account.get("profile_engine") != "camoufox"
        or account.get("profile_generation") != row["generation"]
        or account.get("auth_source_generation") != row["generation"]
        or account.get("platform_account_id") != row["platform_account_id"]
    ):
        raise MigrationError("AUTH_SOURCE_GENERATION_CONFLICT")
    bundle = route.acquire_route_execution(
        row["account_id"],
        operation,
        actor_id,
        account_store=service.store,
        migration_id=migration_id,
    )
    try:
        snapshot = bundle.snapshot
        if not snapshot or not snapshot.managed or not snapshot.observed_exit_ip:
            raise MigrationError("ROUTE_RECEIPT_INCOMPLETE")
        if snapshot.binding_revision != row["binding_revision"]:
            raise MigrationError("ROUTE_REVISION_CONFLICT")
        # Unbound accounts have no provider config revision in the ledger (0),
        # while their transport contract uses an explicit direct revision (1).
        if (
            snapshot.route_mode != "legacy_direct"
            and snapshot.config_revision != row["config_revision"]
        ):
            raise MigrationError("ROUTE_REVISION_CONFLICT")
        observations = []
        for _ in range(2):
            result = route._run_maintenance_probe(
                bundle,
                check_login=True,
                expected_identity=row["platform_account_id"],
                verified_result=True,
            )
            if result.get("login_result") != "success":
                raise MigrationError(
                    "MIGRATION_LOGIN_CHALLENGE"
                    if result.get("login_result") == "challenge"
                    else "MIGRATION_LOGIN_UNCERTAIN"
                )
            if (
                result.get("shutdown_verified") is not True
                or result.get("authority_verified") is not True
            ):
                raise MigrationError("ROUTE_RECEIPT_INCOMPLETE")
            receipt = result["receipt"]
            route.route_contract().require_probe_receipt(
                snapshot, receipt, snapshot.context_id
            )
            if receipt.ip != snapshot.observed_exit_ip:
                raise MigrationError("ROUTE_RECEIPT_CONFLICT")
            process = (result["child_pid"], result["process_start"])
            if not process[1] or any(
                prior["process"] == process for prior in observations
            ):
                raise MigrationError("RESTART_NOT_VERIFIED")
            observations.append(
                {"process": process, "observed_at": receipt.observed_at}
            )
        # Reconcile rechecks owner, auth/route versions and fixed identity after
        # both processes have exited. verify_route repeats the DB CAS checks.
        fresh = service.reconcile(migration_id, actor_id=actor_id)["migration"]
        if any(
            fresh[field] != row[field]
            for field in (
                "identity_hash",
                "generation",
                "expected_auth_revision",
                "binding_revision",
                "config_revision",
                "platform_account_id",
            )
        ):
            raise MigrationError("ROUTE_REVISION_CONFLICT")
        proof = {
            "browser_exit": snapshot.observed_exit_ip,
            "http_exit": snapshot.observed_exit_ip,
            "expected_exit": snapshot.observed_exit_ip,
            "restart_verified": True,
            "authority_verified": True,
            "identity_hash": row["identity_hash"],
            "generation": row["generation"],
            "binding_revision": row["binding_revision"],
            "config_revision": row["config_revision"],
            "process_observations": observations,
            "route_operation_id": snapshot.operation_id,
            "route_snapshot_digest": snapshot.snapshot_digest,
        }
        verified = service.verify_route(migration_id, actor_id=actor_id, receipt=proof)
    finally:
        bundle.close()
    return service.commit(migration_id, actor_id=actor_id) if commit else verified
