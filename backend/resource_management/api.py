from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StrictInt
from backend.investigation_creation.principal import LocalPrincipalProvider, Principal
from backend.rulesets.contracts import StrictModel
from backend.rulesets.errors import RuleSetNotFoundError, RuleSetForbiddenError, RuleSetRevisionConflictError
from .contracts import CreateLexiconInput, OpenResourceInput, UpdateEditInput, EditChange


class SaveLibraryBody(StrictModel):
    expected_version: StrictInt = Field(ge=0)
    operation_id: str = Field(min_length=1, max_length=200)
    content: dict


class DeleteLibraryBody(StrictModel):
    expected_version: StrictInt = Field(ge=1)


class SaveEditBody(StrictModel):
    expected_version: StrictInt = Field(ge=1)
    mode: Literal['new', 'update', 'copy'] = 'new'
    operation_id: str = Field(min_length=1, max_length=200)


class UpdateEditBody(StrictModel):
    expected_version: StrictInt = Field(ge=1)
    changes: list[EditChange] = Field(min_length=1, max_length=100)


def create_resource_router(application, conversation, principal_provider=None):
    router = APIRouter(tags=['resource-management'])
    provide = principal_provider or LocalPrincipalProvider()

    def invoke(method, *, session_id=None, principal, **kwargs):
        try:
            if session_id:
                conversation.get_session(session_id, principal=principal)
                kwargs['session_id'] = session_id
            return getattr(application.resource_management, method)(principal=principal, **kwargs)
        except Exception as exc:
            code = getattr(exc, 'code', '').upper()
            if isinstance(exc, RuleSetNotFoundError): code = 'RESOURCE_NOT_FOUND'
            elif isinstance(exc, RuleSetForbiddenError): code = 'RESOURCE_FORBIDDEN'
            elif isinstance(exc, RuleSetRevisionConflictError): code = 'RESOURCE_VERSION_CONFLICT'
            status = 409 if 'CONFLICT' in code or 'STALE' in code else 403 if 'FORBIDDEN' in code or 'ACCESS' in code else 404 if 'NOT_FOUND' in code or isinstance(exc, KeyError) else 422
            raise HTTPException(status, detail={'code': code or 'RESOURCE_INVALID', 'message': str(exc), 'details': getattr(exc, 'details', {})}) from exc

    @router.put('/api/resource-library/{kind}/{resource_id}')
    def save_library(kind: Literal['ruleset','lexicon'], resource_id: str, body: SaveLibraryBody, principal: Principal=Depends(provide)):
        return invoke('save_library', kind=kind, resource_id=resource_id, principal=principal, **body.model_dump())

    @router.delete('/api/resource-library/{kind}/{resource_id}')
    def delete_library(kind: Literal['ruleset','lexicon'], resource_id: str, body: DeleteLibraryBody, principal: Principal=Depends(provide)):
        return invoke('delete_library', kind=kind, resource_id=resource_id, principal=principal, **body.model_dump())

    @router.get('/api/resource-library/{kind}')
    def list_resources(kind: Literal['ruleset','lexicon'], query: str='', offset: int=0, limit: int=20, principal: Principal=Depends(provide)):
        return invoke('read', kind=kind, query=query, offset=max(0,offset), limit=max(1,min(limit,100)), principal=principal)

    @router.get('/api/resource-library/{kind}/{resource_id}')
    def get_resource(kind: Literal['ruleset','lexicon'], resource_id: str, principal: Principal=Depends(provide)):
        return invoke('read', kind=kind, resource_id=resource_id, principal=principal)

    @router.get('/api/investigation-workspaces/{session_id}/resource-edits')
    def list_edits(session_id: str, principal: Principal=Depends(provide)):
        return invoke('list_edits', session_id=session_id, principal=principal)

    @router.post('/api/investigation-workspaces/{session_id}/resource-edits/open')
    def open_edit(session_id: str, body: OpenResourceInput, principal: Principal=Depends(provide)):
        return invoke('open', session_id=session_id, principal=principal, **body.model_dump())

    @router.post('/api/investigation-workspaces/{session_id}/resource-edits/lexicon')
    def create_lexicon(session_id: str, body: CreateLexiconInput, principal: Principal=Depends(provide)):
        return invoke('create_lexicon', session_id=session_id, principal=principal, content=body.content.model_dump(mode='json'))

    @router.get('/api/investigation-workspaces/{session_id}/resource-edits/{edit_id}')
    def get_edit(session_id: str, edit_id: str, principal: Principal=Depends(provide)):
        return invoke('get_edit', session_id=session_id, principal=principal, edit_id=edit_id)

    @router.patch('/api/investigation-workspaces/{session_id}/resource-edits/{edit_id}')
    def update_edit(session_id: str, edit_id: str, body: UpdateEditBody, principal: Principal=Depends(provide)):
        parsed = UpdateEditInput(edit_id=edit_id, **body.model_dump())
        return invoke('update', session_id=session_id, principal=principal, **parsed.model_dump(mode='json'))

    @router.post('/api/investigation-workspaces/{session_id}/resource-edits/{edit_id}/save')
    def save_edit(session_id: str, edit_id: str, body: SaveEditBody, principal: Principal=Depends(provide)):
        return invoke('save', session_id=session_id, principal=principal, edit_id=edit_id, **body.model_dump())

    @router.get('/api/investigation-workspaces/{session_id}/resource-saves/{operation_id}')
    def get_save(session_id: str, operation_id: str, principal: Principal=Depends(provide)):
        return invoke('get_save', session_id=session_id, principal=principal, operation_id=operation_id)

    return router
