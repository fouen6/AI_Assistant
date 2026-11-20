"""统一异常和HTTP错误响应，避免把内部异常直接返回前端。"""
import logging
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status, code, message, headers=None):
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}
        super().__init__(message)


def install_handlers(app):
    def app_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=exc.headers,
        )

    def bad_request(request: Request, exc: RequestValidationError):
        fields = [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
                  for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {
            "code": "invalid_request", "message": "请求参数不合法。", "fields": fields,
        }})

    def unexpected(request: Request, exc: Exception):
        logging.getLogger(__name__).exception("未预期接口错误")
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error", "message": "服务内部错误，请保留操作编号并检查日志。",
        }})

    app.add_exception_handler(AppError, app_error)
    app.add_exception_handler(RequestValidationError, bad_request)
    app.add_exception_handler(Exception, unexpected)


class ChatServiceError(RuntimeError):
    """单轮聊天后端没有返回可用结果。"""
