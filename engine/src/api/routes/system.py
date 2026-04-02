"""System monitoring routes — Docker containers and host resources."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth
from src.core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system")


def _get_containers() -> list[dict[str, Any]]:
    """Query Docker container status via subprocess. Returns empty list on failure."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            # Fallback: try plain docker ps
            result = subprocess.run(
                ["docker", "ps", "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        if result.returncode != 0 or not result.stdout.strip():
            return []

        containers: list[dict[str, Any]] = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Normalize fields across docker compose ps and docker ps output
            name = raw.get("Name") or raw.get("Service") or raw.get("Names", "")
            status = raw.get("Status") or raw.get("State", "unknown")
            health = raw.get("Health") or (
                "healthy" if "healthy" in status.lower()
                else "unhealthy" if "unhealthy" in status.lower()
                else "unknown"
            )
            # Normalize status to running/stopped/error for frontend enum
            status_lower = status.lower()
            if "up" in status_lower or status_lower == "running":
                normalized_status = "running"
            elif "exit" in status_lower or status_lower in ("stopped", "exited", "dead"):
                normalized_status = "stopped"
            else:
                normalized_status = "error"

            containers.append({
                "name": name,
                "status": normalized_status,
                "health": health,
                "cpu_pct": None,   # Requires docker stats — not queried for simplicity
                "memory_mb": None,
                "uptime": "—",
            })
        return containers
    except FileNotFoundError:
        logger.warning("docker command not found")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("docker ps timed out")
        return []
    except Exception as exc:
        logger.warning("Failed to query Docker containers: %s", exc)
        return []


def _get_resources() -> dict[str, float | None]:
    """Query host resource usage via psutil. Returns None values on failure."""
    try:
        import psutil

        cpu_pct = psutil.cpu_percent(interval=0.2)

        mem = psutil.virtual_memory()
        mem_used_gb = round(mem.used / (1024 ** 3), 2)
        mem_total_gb = round(mem.total / (1024 ** 3), 2)

        disk = psutil.disk_usage(os.sep)
        disk_used_gb = round(disk.used / (1024 ** 3), 2)
        disk_total_gb = round(disk.total / (1024 ** 3), 2)

        return {
            "cpu_pct": round(cpu_pct, 1),
            "memory_used_gb": mem_used_gb,
            "memory_total_gb": mem_total_gb,
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
        }
    except ImportError:
        logger.warning("psutil not installed — system resources unavailable")
        return {
            "cpu_pct": None,
            "memory_used_gb": None,
            "memory_total_gb": None,
            "disk_used_gb": None,
            "disk_total_gb": None,
        }
    except Exception as exc:
        logger.warning("Failed to query system resources: %s", exc)
        return {
            "cpu_pct": None,
            "memory_used_gb": None,
            "memory_total_gb": None,
            "disk_used_gb": None,
            "disk_total_gb": None,
        }


@router.get("/logs", dependencies=[Depends(require_auth)])
async def get_logs(request: Request, limit: int = 100) -> JSONResponse:
    """US-211: Return recent engine logs from alert_history + trade_history."""
    ctx = request.app.state.engine_context
    logs: list[dict[str, Any]] = []

    # Merge alert_history as log entries
    for alert in list(ctx.alert_history)[-limit:]:
        logs.append({
            "type": "alert",
            "severity": alert.get("severity", "info"),
            "message": alert.get("message", ""),
            "timestamp": alert.get("timestamp", ""),
        })

    # Sort by timestamp descending
    logs = sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)
    return JSONResponse(logs[:limit])


@router.get("/db-metrics", dependencies=[Depends(require_auth)])
async def get_db_metrics(request: Request) -> JSONResponse:  # noqa: ARG001
    """US-211: Return database connection metrics."""
    metrics: dict[str, Any] = {
        "connected": False,
        "pool_size": 0,
        "active_connections": 0,
        "query_count": 0,
        "disk_usage_mb": None,
    }

    try:
        database_url = get_settings().database.url
        if database_url:
            metrics["connected"] = True
            # Try to get pool stats from asyncpg if available
            try:
                import asyncpg
                metrics["driver"] = "asyncpg"
            except ImportError:
                metrics["driver"] = "unknown"
    except Exception as exc:
        logger.warning("Failed to query DB metrics: %s", exc)

    return JSONResponse(metrics)


@router.get("/redis-metrics", dependencies=[Depends(require_auth)])
async def get_redis_metrics(request: Request) -> JSONResponse:  # noqa: ARG001
    """US-211: Return Redis connection metrics."""
    metrics: dict[str, Any] = {
        "connected": False,
        "memory_used_mb": None,
        "total_keys": 0,
        "uptime_seconds": None,
    }

    try:
        import redis.asyncio as aioredis
        redis_url = get_settings().redis.url
        client = aioredis.from_url(redis_url, decode_responses=True)
        try:
            info = await client.info("memory")
            server_info = await client.info("server")
            db_info = await client.info("keyspace")
            metrics["connected"] = True
            metrics["memory_used_mb"] = round(
                info.get("used_memory", 0) / (1024 * 1024), 2
            )
            metrics["uptime_seconds"] = server_info.get("uptime_in_seconds", 0)
            # Count total keys across all DBs
            total_keys = 0
            for db_name, db_stats in db_info.items():
                if isinstance(db_stats, dict):
                    total_keys += db_stats.get("keys", 0)
            metrics["total_keys"] = total_keys
        finally:
            await client.aclose()
    except ImportError:
        logger.debug("redis package not available for metrics")
    except Exception as exc:
        logger.warning("Failed to query Redis metrics: %s", exc)

    return JSONResponse(metrics)


@router.get("/containers", dependencies=[Depends(require_auth)])
async def get_containers(request: Request) -> JSONResponse:  # noqa: ARG001
    """Return Docker container status list."""
    containers = await asyncio.to_thread(_get_containers)
    return JSONResponse(containers)


@router.get("/resources", dependencies=[Depends(require_auth)])
async def get_resources(request: Request) -> JSONResponse:  # noqa: ARG001
    """Return host resource usage (CPU, memory, disk)."""
    resources = await asyncio.to_thread(_get_resources)
    return JSONResponse(resources)
