"use client";

import React, { useState } from "react";
import { cn } from "@/lib/utils";

interface InputRowProps {
  onSendMessage: (text: string) => void;
  onToggleMic: () => void;
  isListening: boolean;
}

export default function InputRow({ onSendMessage, onToggleMic, isListening }: InputRowProps) {
  const [text, setText] = useState("");

  const handleSend = () => {
    if (!text.trim()) return;
    onSendMessage(text);
    setText("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="h-[52px] flex-shrink-0 bg-[#0a0a10] border-top border-[rgba(0,180,255,0.12)] p-[8px_12px] flex items-center gap-[8px]">
      {/* MIC BUTTON */}
      <button
        onClick={onToggleMic}
        className={cn(
          "w-8 h-8 rounded-[7px] border flex items-center justify-center transition-colors",
          isListening 
            ? "bg-[rgba(0,255,204,0.15)] border-[rgba(0,255,204,0.3)]" 
            : "bg-[rgba(0,180,255,0.06)] border-[rgba(0,180,255,0.22)] hover:bg-[rgba(0,180,255,0.15)]"
        )}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={isListening ? "#00ffcc" : "#00b4ff"} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>
        </svg>
      </button>

      {/* TEXT INPUT */}
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Type a command or ask Jarvis anything..."
        className="flex-1 bg-white/4 border border-[rgba(0,180,255,0.22)] rounded-[7px] h-8 px-[13px] text-[12px] text-[#e8f4ff] font-inter placeholder-[#3d5a72] focus:outline-none focus:border-[rgba(0,180,255,0.35)] transition-colors"
      />

      {/* ATTACH BUTTON */}
      <button className="w-8 h-8 rounded-[7px] border border-[rgba(0,180,255,0.22)] bg-[rgba(0,180,255,0.06)] flex items-center justify-center hover:bg-[rgba(0,180,255,0.15)] transition-colors">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#00b4ff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.51a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
        </svg>
      </button>

      {/* SEND BUTTON */}
      <button
        onClick={handleSend}
        className="w-8 h-8 rounded-[7px] bg-[rgba(0,180,255,0.12)] flex items-center justify-center hover:bg-[rgba(0,180,255,0.25)] transition-all group"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#00b4ff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className=" group-active:translate-x-1 group-active:-translate-y-1 transition-transform">
          <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>
      </button>
    </div>
  );
}
