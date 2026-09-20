from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.investigation_creation.principal import Principal, PrincipalProvider

from .passwords import MIN_PASSWORD_LENGTH
from .service import ApplicationAuthService
from .store import (
    AccountExpiredError,
    AuthenticationError,
    AuthorizationError,
)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    role: Literal["admin", "user"] = "user"
    validity_days: int = Field(default=7, ge=1, le=365)
    activation_mode: Literal["first_login", "created_at"] = "first_login"


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "disabled"] | None = None
    role: Literal["admin", "user"] | None = None
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=256)
    renew_days: int | None = Field(default=None, ge=1, le=365)


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission: Literal["read", "use", "manage"]


def create_auth_router(
    service: ApplicationAuthService,
    *,
    principal_provider: PrincipalProvider,
    cookie_name: str,
    cookie_secure: bool,
    cookie_samesite: Literal["lax", "strict", "none"] = "lax",
) -> APIRouter:
    router = APIRouter(tags=["application-auth"])

    @router.post("/api/auth/login")
    def login(payload: LoginRequest, response: Response) -> dict:
        try:
            user, token, csrf_token = service.store.login(
                payload.username, payload.password
            )
        except AccountExpiredError as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": "ACCOUNT_EXPIRED", "message": str(exc)},
            ) from exc
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=401,
                detail={"code": "INVALID_CREDENTIALS", "message": str(exc)},
            ) from exc
        expires_at = datetime.fromisoformat(service.store.authenticate_session(token).expires_at)
        max_age = max(
            0,
            int((expires_at - datetime.now(expires_at.tzinfo)).total_seconds()),
        )
        response.set_cookie(
            cookie_name,
            token,
            max_age=max_age,
            expires=expires_at,
            httponly=True,
            secure=cookie_secure,
            samesite=cookie_samesite,
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return {"user": user, "csrf_token": csrf_token}

    @router.post("/api/auth/logout", status_code=204, response_class=Response)
    def logout(
        request: Request,
        response: Response,
        principal: Principal = Depends(principal_provider),
    ) -> Response:
        service.store.logout(
            request.cookies.get(cookie_name, ""), actor_user_id=principal.id
        )
        response.delete_cookie(
            cookie_name,
            path="/",
            secure=cookie_secure,
            httponly=True,
            samesite=cookie_samesite,
        )
        response.headers["Cache-Control"] = "no-store"
        response.status_code = 204
        return response

    @router.get("/api/auth/me")
    def me(principal: Principal = Depends(principal_provider)) -> dict:
        user = service.store.get_user(principal.id)
        if user is None:
            raise HTTPException(status_code=401, detail="session is invalid")
        return {"user": user}

    @router.get("/api/auth/csrf")
    def csrf_token(
        request: Request,
        response: Response,
        principal: Principal = Depends(principal_provider),
    ) -> dict:
        del principal
        try:
            token = service.store.rotate_csrf(
                request.cookies.get(cookie_name, "")
            )
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail="session is invalid") from exc
        response.headers["Cache-Control"] = "no-store"
        return {"csrf_token": token}

    @router.get("/api/admin/users")
    def list_users(principal: Principal = Depends(principal_provider)) -> dict:
        _require_admin(service, principal)
        return {"items": service.store.list_users()}

    @router.post("/api/admin/users", status_code=201)
    def create_user(
        payload: CreateUserRequest,
        principal: Principal = Depends(principal_provider),
    ) -> dict:
        _require_admin(service, principal)
        try:
            user = service.store.create_user(
                username=payload.username,
                password=payload.password,
                role=payload.role,
                validity_days=payload.validity_days,
                activation_mode=payload.activation_mode,
                actor_user_id=principal.id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"user": user}

    @router.patch("/api/admin/users/{user_id}")
    def update_user(
        user_id: str,
        payload: UpdateUserRequest,
        principal: Principal = Depends(principal_provider),
    ) -> dict:
        _require_admin(service, principal)
        if not payload.model_fields_set:
            raise HTTPException(status_code=400, detail="no user change was requested")
        try:
            user = service.store.update_user(
                user_id,
                actor_user_id=principal.id,
                status=payload.status,
                role=payload.role,
                password=payload.password,
                renew_days=payload.renew_days,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="user was not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"user": user}

    @router.get("/api/admin/users/{user_id}/grants")
    def list_grants(
        user_id: str, principal: Principal = Depends(principal_provider)
    ) -> dict:
        _require_admin(service, principal)
        if service.store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="user was not found")
        return {"items": service.store.list_grants(user_id)}

    @router.put(
        "/api/admin/users/{user_id}/grants/{resource_type}/{resource_id}"
    )
    def grant_resource(
        user_id: str,
        resource_type: str,
        resource_id: str,
        payload: GrantRequest,
        principal: Principal = Depends(principal_provider),
    ) -> dict:
        _require_admin(service, principal)
        try:
            grant = service.store.grant_resource(
                user_id=user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                permission=payload.permission,
                actor_user_id=principal.id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="user was not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"grant": grant}

    @router.delete(
        "/api/admin/users/{user_id}/grants/{resource_type}/{resource_id}/{permission}",
        status_code=204,
        response_class=Response,
    )
    def revoke_resource(
        user_id: str,
        resource_type: str,
        resource_id: str,
        permission: Literal["read", "use", "manage"],
        principal: Principal = Depends(principal_provider),
    ) -> Response:
        _require_admin(service, principal)
        if not service.store.revoke_resource(
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            permission=permission,
            actor_user_id=principal.id,
        ):
            raise HTTPException(status_code=404, detail="grant was not found")
        return Response(status_code=204)

    return router


def _require_admin(
    service: ApplicationAuthService, principal: Principal
) -> None:
    try:
        service.require_admin(principal)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
