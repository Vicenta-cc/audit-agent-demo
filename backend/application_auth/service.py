from __future__ import annotations

from typing import Any

from backend.investigation_creation.principal import Principal

from .store import AuthStore, AuthorizationError


class ApplicationAuthService:
    def __init__(self, store: AuthStore) -> None:
        self.store = store

    def require_admin(self, principal: Principal) -> None:
        if not principal.is_admin:
            raise AuthorizationError("administrator access is required")

    def can_access_owned_resource(
        self,
        principal: Principal,
        *,
        owner_user_id: str | None,
        resource_type: str,
        resource_id: str,
        permission: str = "read",
    ) -> bool:
        if resource_type == "crawler-account":
            return self.store.owns_crawler_account(principal.id, str(resource_id))
        if principal.is_admin:
            return True
        owner = str(owner_user_id or "").strip()
        if owner and owner == principal.id:
            return True
        return self.store.has_grant(
            principal.id, resource_type, str(resource_id), permission
        )

    def require_owned_resource(
        self,
        principal: Principal,
        *,
        owner_user_id: str | None,
        resource_type: str,
        resource_id: str,
        permission: str = "read",
    ) -> None:
        if not self.can_access_owned_resource(
            principal,
            owner_user_id=owner_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            permission=permission,
        ):
            raise AuthorizationError("resource was not found")

    def can_use_crawler_account(self, principal: Principal, account_id: str) -> bool:
        return self.store.owns_crawler_account(principal.id, account_id)

    def can_manage_crawler_account(self, principal: Principal, account_id: str) -> bool:
        return self.store.owns_crawler_account(principal.id, account_id)

    def can_use_crawler_proxy(self, principal: Principal, proxy_id: str) -> bool:
        return principal.is_admin or self.store.has_grant(principal.id, "crawler-proxy", str(proxy_id), "use")

    def require_crawler_proxy_use(self, principal: Principal, proxy_id: str) -> None:
        if not self.can_use_crawler_proxy(principal, proxy_id):
            raise AuthorizationError("resource was not found")

    def can_manage_crawler_proxy(self, principal: Principal, proxy_id: str = "") -> bool:
        return principal.is_admin

    @staticmethod
    def public_crawler_account(account: dict[str, Any]) -> dict[str, Any]:
        fields = {
            "id", "platform", "display_name", "platform_account_id", "access_scope", "status",
            "last_validated_at", "last_used_at", "last_error", "cooldown_until", "failure_kind",
            "auth_state_revision", "has_auth_state", "auth_state_updated_at", "risk_count",
            "last_risk_at", "created_at", "updated_at", "proxy_id", "binding_revision", "proxy_required",
            "validated_proxy_binding_revision", "validated_proxy_config_revision", "needs_proxy_login_validation",
            "route_mode", "route_state", "shared_exit_generation", "paused_until", "pause_reason", "recovery_state", "skip_count",
        }
        return {key: value for key, value in account.items() if key in fields}

    @staticmethod
    def public_crawler_proxy(resource: dict[str, Any]) -> dict[str, Any]:
        from backend.audit_agent.crawler_proxy_store import PUBLIC_PROXY_FIELDS
        return {key: value for key, value in resource.items() if key in PUBLIC_PROXY_FIELDS}
