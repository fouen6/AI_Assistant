"""用户页、管理员页、注册页及静态资源，不处理模型业务。"""
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.errors import AppError
STATIC = Path(__file__).resolve().parents[1] / "static"
router = APIRouter()


def page(name):
    target = STATIC / name
    if not target.is_file():
        raise AppError(404, "page_not_ready", "该教学页尚未创建，请按当前任务添加文件。")
    return FileResponse(target)


@router.get("/", include_in_schema=False)
def index():
    return page("index.html")


@router.get("/admin", include_in_schema=False)
def admin_page():
    return page("admin.html")


@router.get("/auth-lab", include_in_schema=False)
def auth_lab():
    return page("auth-lab.html")


def install_ui(app):
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
