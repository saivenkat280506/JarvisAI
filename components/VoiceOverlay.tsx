"use client";

import { Orb } from "@/components/ui/orb";
import { Waveform } from "@/components/ui/waveform";
import { MicOff, X } from "lucide-react";
import { Button } from "@/components/ui/button";

interface VoiceOverlayProps {
  transcript: string;
  onClose: () => void;
}

export default function VoiceOverlay({ transcript, onClose }: VoiceOverlayProps) {
  return (
    <div className="fixed inset-0 z-[100] flex flex-col items-center justify-center p-8 backdrop-blur-2xl bg-white/20 animate-in fade-in duration-500">
      <Button 
        variant="ghost" 
        size="icon" 
        onClick={onClose}
        className="absolute top-8 right-8 rounded-full h-12 w-12 hover:bg-white/20 active:scale-95 transition-all"
      >
        <X className="w-6 h-6 text-zinc-800" />
      </Button>

      <div className="flex flex-col items-center gap-16 max-w-2xl w-full">
        {/* LARGE CENTRAL ORB */}
        <div className="relative w-64 h-64 flex items-center justify-center rounded-full bg-white shadow-[0_0_80px_rgba(255,255,255,0.8)] orb-float">
          <Orb
            agentState="listening"
            className="w-full h-full p-4"
            colors={["#88A9D1", "#5C84B1"]}
          />
        </div>

        {/* TRANSCRIPT VIEW */}
        <div className="text-center space-y-6">
          <div className="h-12 overflow-hidden">
             <Waveform 
              data={Array.from({ length: 60 }, () => Math.random() * 0.8 + 0.1)} 
              barWidth={4} 
              barGap={3} 
              barRadius={10}
              fadeEdges={true}
              height={48}
              className="w-full max-w-sm mx-auto opacity-40"
              barColor="#5C84B1"
            />
          </div>
          
          <p className="text-2xl font-medium tracking-tight text-zinc-900 leading-relaxed font-inter">
            {transcript || "Listening to your request..."}
          </p>
          <p className="text-[14px] text-zinc-500 font-inter tracking-wide animate-pulse uppercase">
             Jarvis is processing
          </p>
        </div>

        <Button 
          onClick={onClose}
          className="h-16 px-10 rounded-3xl bg-red-500 hover:bg-red-600 text-white shadow-xl hover:shadow-2xl transition-all duration-300 gap-3 active:scale-95"
        >
          <MicOff className="w-6 h-6" />
          <span className="text-lg font-medium">End Voice Session</span>
        </Button>
      </div>
    </div>
  );
}
