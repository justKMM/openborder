"""One error shape for the whole API: {"error": {"code": "...", "message": "..."}}.

`message` is fit to show a user. `code` is for the frontend to branch on. Request bodies are never echoed back
(FastAPI's default 422 includes the offending input, which could be a key).
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openborder.apply import PlanError
from openborder.explain import ExplainError
from openborder.service import ServiceError
from openborder.sources.datadog.client import DatadogError
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _respond(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, e: ApiError):
        return _respond(e.status, e.code, e.message)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, e: ServiceError):
        return _respond(400, "bad_request", str(e))

    @app.exception_handler(DatadogError)
    async def datadog_error(request: Request, e: DatadogError):
        return _respond(502, "datadog_error", str(e))

    @app.exception_handler(PlanError)
    async def plan_error(request: Request, e: PlanError):
        return _respond(409, "plan_not_applicable", str(e))

    @app.exception_handler(ExplainError)
    async def explain_error(request: Request, e: ExplainError):
        return _respond(400, "explain_error", str(e))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, e: RequestValidationError):
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        return _respond(422, "invalid_request", problems)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException):
        codes = {404: "not_found", 405: "method_not_allowed"}
        return _respond(e.status_code, codes.get(e.status_code, "http_error"), str(e.detail))
