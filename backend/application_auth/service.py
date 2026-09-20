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
        return principal.is_admin or self.store.has_grant(
            principal.id, "crawler-account", account_id, "use"
        )

    def can_manage_crawler_account(self, principal: Principal, account_id: str) -> bool:
        return principal.is_admin or self.store.has_grant(
            principal.id, "crawler-account", account_id, "manage"
        )

    @staticmethod
    def public_crawler_account(account: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in account.items()
            if key
            not in {
                "auth_state",
                "auth_state_ciphertext",
                "storage_state",
                "cookie",
                "cookies",
            }
        }
