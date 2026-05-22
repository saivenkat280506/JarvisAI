"use client";

import { useEffect, useRef } from "react";
import {
  Layers,
  Mic,
  Bot,
  Zap,
  Globe,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Wifi,
} from "lucide-react";
import { ActionLogEntry } from "@/hooks/useJarvis";
import { useBackendStatus } from "@/hooks/useBackendStatus";

interface ActionLogProps {
  logs: ActionLogEntry[];
}

const SOURCE_ICON: Record<string, React.ReactNode> = {
  STT:       <Mic className="w-3 h-3 text-blue-500" />,
  "Core Engine": <Zap className="w-3 h-3 text-amber-500" />,
  LLM:       <Bot className="w-3 h-3 text-purple-500" />,
  Agent:     <Globe className="w-3 h-3 text-emerald-500" />,
  Chat:      <Bot className="w-3 h-3 text-zinc-500" />,
  Voice:     <Mic className="w-3 h-3 text-rose-500" />,
  WebSocket: <Wifi className="w-3 h-3 text-cyan-500" />,
  System:    <CheckCircle2 className="w-3 h-3 text-emerald-500" />,
  UI:        <Layers className="w-3 h-3 text-zinc-400" />,
};

const STATUS_DOT: Record<ActionLogEntry["status"], string> = {
  success: "bg-emerald-500",
  pending: "bg-amber-400 animate-pulse",
  info:    "bg-blue-400",
  error:   "bg-red-500",
};

export default function ActionLog({ logs }: ActionLogProps) {
  const { status: backendStatus, latency } = useBackendStatus(5000);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to show the newest (left-most) entry
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollLeft = 0;
    }
  }, [logs]);

  const isOnline = backendStatus === "online";

  return (
    <div className="bg-white/40 border-b border-border/20 px-6 py-4 backdrop-blur-sm flex-shrink-0">
      {/* Header row */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Layers className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-[10px] font-bold tracking-[0.2em] text-muted-foreground uppercase mt-0.5">
            Runtime Logs
          </span>
          {logs.length > 0 && (
            <span className="text-[8px] font-jetbrains text-zinc-400 ml-1">
              ({logs.length})
            </span>
          )}
        </div>

        <div className="flex items-center gap-4">
          {/* Live NODE latency */}
          <div className="flex items-center gap-1.5">
            <div className={`w-1.5 h-1.5 rounded-full ${isOnline ? "bg-emerald-500" : "bg-red-500"}`} />
            <span className="text-[9px] font-jetbrains text-muted-foreground">
              NODE: {latency !== null ? `${latency}ms` : "—"}
            </span>
          </div>
          {/* Last event timestamp */}
          {logs[0] && (
            <div className="flex items-center gap-1.5">
              <Clock className="w-2.5 h-2.5 text-muted-foreground" />
              <span className="text-[9px] font-jetbrains text-muted-foreground">
                {logs[0].timestamp}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Scrollable log chips — newest left */}
      <div
        ref={scrollRef}
        className="flex gap-3 overflow-x-auto pb-1 scrollbar-hide"
      >
        {logs.length === 0 ? (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-white/30 border border-white/60 text-[10px] text-zinc-400 font-jetbrains">
            <div className="w-1.5 h-1.5 rounded-full bg-zinc-300 animate-pulse" />
            Awaiting system events…
          </div>
        ) : (
          logs.map((log) => (
            <div
              key={log.id}
              className="flex-shrink-0 flex items-center gap-2.5 px-3 py-1.5 rounded-xl bg-white/50 border border-white/80 shadow-sm"
            >
              {/* Source icon */}
              <div className="p-1 rounded-md bg-zinc-100/80">
                {SOURCE_ICON[log.source] ?? <Layers className="w-3 h-3 text-zinc-400" />}
              </div>

              <div className="flex flex-col max-w-[180px]">
                <span className="text-[10px] font-medium text-zinc-700 leading-tight truncate">
                  {log.action}
                </span>
                <span className="text-[8px] text-zinc-400 font-jetbrains tracking-tight uppercase">
                  {log.source} • {log.timestamp}
                </span>
              </div>

              {/* Status dot */}
              <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${STATUS_DOT[log.status]}`} />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
