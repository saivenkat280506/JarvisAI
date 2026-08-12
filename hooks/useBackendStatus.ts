"use client";

import { useState, useEffect, useRef } from "react";

export type BackendStatus = "online" | "offline" | "checking";

interface BackendHealth {
  status: BackendStatus;
  latency: number | null;
}

export function useBackendStatus(intervalMs = 5000): BackendHealth {
  const [status, setStatus] = useState<BackendStatus>("checking");
  const [latency, setLatency] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const statusRef = useRef<BackendStatus>("checking");

  const check = async () => {
    const start = performance.now();
    try {
      const res = await fetch("http://127.0.0.1:8000/health", {
        method: "GET",
        cache: "no-store",
        signal: AbortSignal.timeout(3000),
      });
      const ms = Math.round(performance.now() - start);
      if (res.ok || res.status < 500) {
        statusRef.current = "online";
        setStatus("online");
        setLatency(ms);
      } else {
        statusRef.current = "offline";
        setStatus("offline");
        setLatency(null);
      }
    } catch {
      statusRef.current = "offline";
      setStatus("offline");
      setLatency(null);
    }
  };

  useEffect(() => {
    check();
    // Poll faster while offline so UI reconnects quickly after backend boots
    const tick = () => {
      const delay = statusRef.current === "online" ? intervalMs : Math.min(intervalMs, 2000);
      timerRef.current = setTimeout(async () => {
        await check();
        tick();
      }, delay);
    };
    tick();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [intervalMs]);

  return { status, latency };
}
