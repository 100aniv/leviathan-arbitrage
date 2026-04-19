"""Socket option helpers for low-latency WS order paths (BUG-196)."""
from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)


def set_tcp_nodelay(ws) -> bool:
    """Disable Nagle's algorithm on a connected websockets client.

    Returns True on success, False if the socket cannot be accessed
    (e.g. in tests where `ws` is mocked). Never raises.
    """
    try:
        transport = getattr(ws, "transport", None)
        if transport is None:
            return False
        sock = transport.get_extra_info("socket")
        if sock is None:
            return False
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return True
    except Exception as exc:
        logger.debug("tcp_nodelay_set_failed err=%s", exc)
        return False
