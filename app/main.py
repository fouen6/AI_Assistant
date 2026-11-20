"""应用入口：注册接口与页面。启动：uv run python -m app.manage serve。"""
from fastapi import FastAPI
from app.errors import install_handlers
from app.routers import auth, chat, admin, profile, pages

def create_app():
    app = FastAPI(title="AI Assistant", version="1.1.0-study")
    install_handlers(app)
    for router in [auth.router, chat.router, admin.router, profile.router]:
        app.include_router(router)
    pages.install_ui(app)
    return app

app = create_app()
