"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { WebSocketManager } from "@/lib/websocket";
import type { ConnectionState, WsMessage } from "@/types";

interface UseWebSocketOptions {
  /** Pass a pre-constructed manager or factory to avoid singletons where needed */
  manager: WebSocketManager;
  /** Auto-connect on mount (default: true) */
  autoConnect?: boolean;
}

interface UseWebSocketReturn {
  connected: boolean;
  connectionState: ConnectionState;
  lastMessage: WsMessage | null;
  send: (data: unknown) => void;
}

export function useWebSocket({
  manager,
  autoConnect = true,
}: UseWebSocketOptions): UseWebSocketReturn {
  const [connectionState, setConnectionState] = useState<ConnectionState>(
    manager.getState()
  );
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null);
  const managerRef = useRef(manager);
  managerRef.current = manager;

  useEffect(() => {
    const unsubState   = manager.onStateChange(setConnectionState);
    const unsubMessage = manager.onMessage(setLastMessage);

    if (autoConnect) {
      manager.connect();
    }

    return () => {
      unsubState();
      unsubMessage();
    };
  }, [manager, autoConnect]);

  const send = useCallback((data: unknown) => {
    managerRef.current.send(data);
  }, []);

  return {
    connected: connectionState === "connected",
    connectionState,
    lastMessage,
    send,
  };
}
