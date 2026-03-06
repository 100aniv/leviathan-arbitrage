"""LEVIATHAN Structlog Configuration.

JSON output, correlation IDs, ISO 8601 timestamps, module-aware context.
Log levels from config (DEBUG/INFO/WARNING/ERROR/CRITICAL).
"""
from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Correlation ID context variable (per-request tracing)
# ---------------------------------------------------------------------------
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Get the current correlation ID, or a placeholder if not set."""
    return _correlation_id.get() or "no-correlation-id"


def set_correlation_id(cid: str | None = None) -> str:
    """Set or generate a correlation ID for the current async context."""
    if cid is None:
        cid = str(uuid.uuid4())
    _correlation_id.set(cid)
    return cid


def _add_correlation_id(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor: inject correlation_id into every log record."""
    cid = _correlation_id.get()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    add_caller_info: bool = True,
) -> None:
    """
    Configure structlog for the LEVIATHAN engine.

    Args:
        log_level: Python log level string (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        log_format: "json" for production, "console" for development
        add_caller_info: Include module, function, and line number in records
    """
    level_value = getattr(logging, log_level.upper(), None)
    if level_value is None:
        msg = f"Invalid log level: {log_level!r}"
        raise ValueError(msg)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_correlation_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    if add_caller_info:
        shared_processors.append(
            structlog.processors.CallsiteParameterAdder(
                [
                    structlog.processors.CallsiteParameter.MODULE,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                    structlog.processors.CallsiteParameter.LINENO,
                ]
            )
        )

    if log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level_value)

    # Quiet noisy libraries
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def get_logger(name: str) -> Any:
    """Get a module-aware structlog logger."""
    return structlog.get_logger(name)
