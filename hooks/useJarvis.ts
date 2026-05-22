"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { ChatMessage } from "@/components/ChatArea";

const BACKEND_URL = "http://127.0.0.1:8000";
const WS_URL = "ws://127.0.0.1:8000/ws";

export interface ActionLogEntry {
  id: string;
  source: string;
  action: string;
  status: "success" | "pending" | "info" | "error";
  timestamp: string;
  icon?: string;
}

function makeLog(source: string, action: string, status: ActionLogEntry["status"] = "info"): ActionLogEntry {
  return {
    id: Math.random().toString(36).slice(2),
    source,
    action,
    status,
    timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
  };
}

export function useJarvis() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [speechTranscript, setSpeechTranscript] = useState("");
  const [agentState, setAgentState] = useState<"idle" | "listening" | "thinking" | "talking" | "transcribing">("idle");
  const [actionLogs, setActionLogs] = useState<ActionLogEntry[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  const pushLog = useCallback((entry: ActionLogEntry) => {
    setActionLogs((prev) => {
      const next = [entry, ...prev]; // newest first
      return next.slice(0, 40);      // keep at most 40 entries
    });
  }, []);

  // ── Initialize WebSocket ──────────────────────────────────────────────────
  useEffect(() => {
    const connectWs = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        pushLog(makeLog("WebSocket", "Connected to Jarvis core", "success"));
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        // ── State updates ──
        if (data.state) {
          setAgentState(data.state as any);
          const stateLabels: Record<string, string> = {
            listening: "Listening for command…",
            thinking: "Processing request…",
            talking: "Generating response…",
            transcribing: "Transcribing audio…",
            idle: "Standby",
          };
          if (data.state !== "idle") {
            pushLog(makeLog("Core Engine", stateLabels[data.state] || data.state, "info"));
          }
        }

        // ── Wake word ──
        if (data.type === "wake_word_detected") {
          pushLog(makeLog("STT", "Wake word detected", "success"));
        }

        // ── System ready ──
        if (data.type === "system_ready") {
          pushLog(makeLog("System", "All systems online", "success"));
        }

        // ── Chat messages from backend ──
        if (data.type === "chat") {
          const newMsg: ChatMessage = {
            id: Math.random().toString(36).substring(7),
            role: data.role || "assistant",
            type: "voice",
            content: data.text,
            time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
          };
          setMessages((prev) => [...prev, newMsg]);
          pushLog(makeLog("LLM", `Response: "${data.text.slice(0, 50)}${data.text.length > 50 ? "…" : ""}"`, "success"));
        }

        // ── Partial transcript / live STT ──
        if (data.type === "transcript" || data.type === "transcript_chunk" || data.type === "partial_transcript") {
          if (data.countdown !== undefined && data.countdown > 0) {
            setSpeechTranscript(data.text + ` … (sending in ${data.countdown}s)`);
          } else {
            setSpeechTranscript(data.text);
          }
        }

        // ── Final transcript → user message ──
        if (data.type === "user_message") {
          const userMsg: ChatMessage = {
            id: Math.random().toString(36).substring(7),
            role: "user",
            type: "voice",
            content: data.text,
            time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
          };
          setMessages((prev) => [...prev, userMsg]);
          setSpeechTranscript("");
          pushLog(makeLog("STT", `Finalised: "${data.text}"`, "success"));
        }

        // ── Clear transcript if speech ended empty ──
        if (data.type === "transcript_clear") {
          setSpeechTranscript("");
        }

        // ── Agent steps (web/OS agent) ──
        if (data.type === "agent_step") {
          const status = data.status === "done" || data.status === "stopped" ? "success" : "pending";
          pushLog(makeLog("Agent", `Step ${data.step}: ${data.action}`, status));
        }

        // ── Focus window ──
        if (data.action === "focus_window") {
          window.focus();
          pushLog(makeLog("UI", "Window focus restored", "info"));
        }
      };

      ws.onclose = () => {
        pushLog(makeLog("WebSocket", "Connection lost — retrying in 3s", "error"));
        setTimeout(connectWs, 3000);
      };

      ws.onerror = () => ws.close();
    };

    connectWs();
    return () => wsRef.current?.close();
  }, [pushLog]);

  // ── Send message ──────────────────────────────────────────────────────────
  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;

    setInputText("");
    setAgentState("thinking");
    pushLog(makeLog("Chat", `Sent: "${trimmed.slice(0, 60)}"`, "info"));

    const userMsgId = "user-" + Date.now();
    setMessages((prev) => [
      ...prev,
      {
        id: userMsgId,
        role: "user",
        type: "text",
        content: trimmed,
        time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
      },
    ]);

    try {
      const response = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: trimmed, id: userMsgId }),
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
    } catch (err) {
      pushLog(makeLog("Chat", `Error: ${String(err)}`, "error"));
      if (agentState === "thinking") setAgentState("idle");
    }
  }, [agentState, pushLog]);

  // ── Mic Toggle ────────────────────────────────────────────────────────────
  const toggleMic = useCallback(async () => {
    try {
      if (agentState !== "idle") {
        await fetch(`${BACKEND_URL}/stop-trigger`, { method: "POST" });
        pushLog(makeLog("Voice", "Stop trigger sent", "info"));
        return;
      }
      const response = await fetch(`${BACKEND_URL}/listen-trigger`, { method: "POST" });
      if (response.ok) {
        setAgentState("listening");
        pushLog(makeLog("Voice", "Listen trigger activated", "success"));
      }
    } catch (err) {
      pushLog(makeLog("Voice", `Mic error: ${String(err)}`, "error"));
    }
  }, [agentState, pushLog]);

  const clearChat = useCallback(() => {
    setMessages([]);
    pushLog(makeLog("UI", "Conversation cleared", "info"));
  }, [pushLog]);

  return {
    messages,
    inputText,
    setInputText,
    settingsOpen,
    setSettingsOpen,
    isListening: agentState === "listening",
    isSpeaking: agentState === "talking",
    streamingText,
    speechTranscript,
    agentState,
    actionLogs,
    sendMessage,
    toggleMic,
    clearChat,
    lastSentence: messages.filter((m) => m.role === "assistant").slice(-1)[0]?.content || "",
  };
}
