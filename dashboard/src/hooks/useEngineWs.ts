"use client";

import { useEffect, useRef, useState } from "react";
import type { StateUpdateData } from "@/types";

// ─── Return type ──────────────────────────────────────────────────────────────

export interface UseEngineWsReturn {
  connected: boolean;
  data: StateUpdateData | null;
}

// Exponential backoff: 1s, 2s, 4s, … capped at 30s
const INITIAL_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS     = 30_000;

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  // localStorage 우선 (api.ts와 동일한 소스), cookie fallback
  return localStorage.getItem("leviathan_token") ||
    (document.cookie.match(/(?:^|;\s*)leviathan_token=([^;]+)/)?.[1]
      ? decodeURIComponent(document.cookie.match(/(?:^|;\s*)leviathan_token=([^;]+)/)![1])
      : null);
}

function getWsUrl(): string {
  const engineUrl =
    (typeof process !== "undefined" &&
      process.env.NEXT_PUBLIC_ENGINE_URL) ||
    "http://localhost:8000";
  const base = engineUrl.replace(/^http/, "ws") + "/ws";
  const token = getToken();
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useEngineWs(): UseEngineWsReturn {
  const [connected, setConnected] = useState(false);
  const [data, setData] = useState<StateUpdateData | null>(null);

  const wsRef        = useRef<WebSocket | null>(null);
  const backoffRef   = useRef(INITIAL_BACKOFF_MS);
  const timerRef     = useRef<ReturnType<typeof setTimeout> | null>(null);
  const destroyedRef = useRef(false);

  useEffect(() => {
    destroyedRef.current = false;

    function connect() {
      if (destroyedRef.current) return;

      const baseUrl = getWsUrl();
      // JWT passed as ?token= query param (cookie not forwarded cross-port by browsers)
      const url = baseUrl;
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        backoffRef.current = INITIAL_BACKOFF_MS;
        setConnected(true);
      };

      ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data as string) as {
            type: string;
            data?: StateUpdateData;
            ts?: number;
          };
          if (msg.type === "state_update" && msg.data) {
            setData(msg.data);
          }
          // heartbeat: ignored — presence of any frame keeps the connection alive
        } catch {
          // ignore malformed frames
        }
      };

      ws.onerror = () => {
        setConnected(false);
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        if (!destroyedRef.current) {
          scheduleReconnect();
        }
      };
    }

    function scheduleReconnect() {
      const delay = backoffRef.current;
      backoffRef.current = Math.min(delay * 2, MAX_BACKOFF_MS);
      timerRef.current = setTimeout(connect, delay);
    }

    connect();

    return () => {
      destroyedRef.current = true;
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, []);

  return { connected, data };
}
