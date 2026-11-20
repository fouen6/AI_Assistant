"""管理员审批、会话查询、文档管理及用户管理；保持现有HTTP地址。"""
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query, File, Response, UploadFile
from app.auth import require_admin, get_accounts
from app.errors import AppError
from app.db import execution_guard
from app.schemas.api import UserChange, ApprovalBody
from app.services.chat import SessionService, get_sessions, describe
from app.services.agent import ApprovalService
from app.services.documents import DocumentService, MAX_BYTES
from app.repositories.users import UserRepository
from app.repositories.documents import DocumentRepository
from app.repositories.approvals import list_approvals
router = APIRouter(tags=["管理员"])


@router.get("/admin/users")
def users(user: Annotated[dict, Depends(require_admin)],
          store: Annotated[UserRepository, Depends(get_accounts)]):
    return store.list_users()


@router.patch("/admin/users/{user_id}")
def change_user(user_id: int, req: UserChange,
                user: Annotated[dict, Depends(require_admin)],
                store: Annotated[UserRepository, Depends(get_accounts)]):
    return store.change_user(user, user_id, req.role, req.is_active)


@router.get("/admin/sessions/{sid}")
def admin_read(sid: UUID, user: Annotated[dict, Depends(require_admin)],
               service: Annotated[SessionService, Depends(get_sessions)]):
    session = service.store.any_session(sid)
    result = describe(service.engine.read(session), sid)
    result["write_blocked"] = service.store.unfinished(sid)
    return result


@router.get("/admin/sessions/{sid}/operations/{rid}")
def admin_operation(sid: UUID, rid: UUID, user: Annotated[dict, Depends(require_admin)],
                    service: Annotated[SessionService, Depends(get_sessions)]):
    service.store.any_session(sid)
    row = service.store.operation(sid, rid)
    if row is None:
        raise AppError(404, "operation_not_found", "操作记录不存在。")
    return row


@router.get("/admin/approvals")
def approvals(user: Annotated[dict, Depends(require_admin)],
              decision: Literal["pending", "approved", "rejected"] | None = None,
              limit: Annotated[int, Query(ge=1, le=100)] = 50):
    return list_approvals(decision, limit)



@router.post("/admin/sessions/{sid}/approvals/{interrupt_id}")
def resume(sid: UUID, interrupt_id: str, req: ApprovalBody,
           user: Annotated[dict, Depends(require_admin)],
           service: Annotated[SessionService, Depends(get_sessions)]):
    return ApprovalService(service).resume(
        user, sid, req.request_id, interrupt_id, req.approved,
    )


def get_documents():
    return DocumentRepository()


@router.get("/admin/documents")
def list_documents(user: Annotated[dict, Depends(require_admin)],
                   store: Annotated[DocumentRepository, Depends(get_documents)]):
    return store.list()


@router.post("/admin/documents", status_code=201)
def upload(file: Annotated[UploadFile, File()],
           user: Annotated[dict, Depends(require_admin)],
           store: Annotated[DocumentRepository, Depends(get_documents)]):
    with execution_guard():
        UserRepository().actor(user, admin=True)
        raw = file.file.read(MAX_BYTES + 1)
        return DocumentService(store).upload(file.filename, raw)


@router.delete("/admin/documents/{did}", status_code=204)
def delete(did: UUID, user: Annotated[dict, Depends(require_admin)],
           store: Annotated[DocumentRepository, Depends(get_documents)]):
    with execution_guard():
        UserRepository().actor(user, admin=True)
        store.delete(did)
    return Response(status_code=204)
