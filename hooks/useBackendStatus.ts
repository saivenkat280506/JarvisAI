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

  const check = async () => {
    const start = performance.now();
    try {
      const res = await fetch("http://127.0.0.1:8000/health", {
        method: "GET",
        signal: AbortSignal.timeout(3000),
      });
      const ms = Math.round(performance.now() - start);
      if (res.ok || res.status < 500) {
        setStatus("online");
        setLatency(ms);
      } else {
        setStatus("offline");
        setLatency(null);
      }
    } catch {
      setStatus("offline");
      setLatency(null);
    }
  };

  useEffect(() => {
    check();
    timerRef.current = setInterval(check, intervalMs);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [intervalMs]);

  return { status, latency };
}
