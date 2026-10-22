from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from netops.errors import ConflictError, NetOpsError, NotFoundError, ServiceUnavailableError
from netops.intent import IntentValidationError
from netops.schemas.intent import IntentIssueRead

_logger = logging.getLogger("netops.api.errors")

_STATUS_CODES: tuple[tuple[type[NetOpsError], int], ...] = (
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
    (ServiceUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE),
    (IntentValidationError, status.HTTP_422_UNPROCESSABLE_CONTENT),
)


async def _handle_domain_error(_: Request, exc: Exception) -> JSONResponse:
    code = next(
        (code for error_type, code in _STATUS_CODES if isinstance(exc, error_type)),
        status.HTTP_400_BAD_REQUEST,
    )
    content: dict[str, object] = {"detail": str(exc)}
    if isinstance(exc, IntentValidationError):
        content["issues"] = [
            IntentIssueRead.model_validate(issue).model_dump() for issue in exc.issues
        ]
    return JSONResponse(status_code=code, content=content)


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    _logger.error(
        "422 Validation error on %s %s: errors=%s", request.method, request.url, exc.errors()
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(exc.errors())},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(NetOpsError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
