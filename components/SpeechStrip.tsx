"use client";

import React from "react";
import { cn } from "@/lib/utils";

interface SpeechStripProps {
  isListening: boolean;
  transcript: string;
}

export default function SpeechStrip({ isListening, transcript }: SpeechStripProps) {
  return (
    <div className="h-[34px] flex-shrink-0 bg-[#0a0a10] border-top border-[rgba(0,180,255,0.07)] flex items-center gap-[8px] px-4">
      {/* LEFT: Mic icon circle */}
      <div className={cn(
        "w-[18px] h-[18px] rounded-full flex items-center justify-center border",
        isListening ? "bg-[rgba(0,255,204,0.1)] border-[rgba(0,255,204,0.2)]" : "bg-transparent border-[rgba(0,180,255,0.1)]"
      )}>
        <svg 
          width="9" 
          height="14" 
          viewBox="0 0 24 24" 
          fill={isListening ? "#00ffcc" : "#3d5a72"} 
          className="opacity-70"
        >
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>
        </svg>
      </div>

      {/* CENTER: Transcript */}
      <span className={cn(
        "font-jetbrains text-[10px] italic flex-1 truncate",
        isListening ? "text-[#00ffcc]" : "text-[#3d5a72]"
      )}>
        {transcript || (isListening ? "listening..." : "listening for voice command...")}
      </span>

      {/* RIGHT: LISTENING badge */}
      {isListening && (
        <div className="font-jetbrains text-[8px] px-1.5 py-0.5 rounded-[10px] bg-[rgba(0,255,136,0.08)] text-[#00ff88] border border-[rgba(0,255,136,0.2)] animate-pulse uppercase">
          LISTENING
        </div>
      )}
    </div>
  );
}
