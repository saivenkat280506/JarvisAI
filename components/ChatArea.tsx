"use client";

import { useChatScroll } from "@/hooks/useChatScroll";
import { Message, MessageContent, MessageAvatar } from "@/components/ui/message";
import { Response } from "@/components/ui/response";
import { ShimmeringText } from "@/components/ui/shimmering-text";
import {
  TranscriptViewerAudio,
  TranscriptViewerContainer,
  TranscriptViewerPlayPauseButton,
  TranscriptViewerScrubBar,
  TranscriptViewerWords,
} from "@/components/ui/transcript-viewer";
import { SendHorizontal, Mic, FileUp, PauseIcon, PlayIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  type: "text" | "voice";
  content: string;
  time: string;
  isStreaming?: boolean;
  audioSrc?: string;
  alignment?: any;
}

export interface ChatAreaProps {
  messages: ChatMessage[];
  inputText: string;
  setInputText: (text: string) => void;
  sendMessage: (text: string) => void;
  streamingText?: string;
  speechTranscript?: string;
  agentState?: string;
}

export default function ChatArea({ 
  messages, 
  inputText, 
  setInputText, 
  sendMessage,
  streamingText,
  speechTranscript,
  agentState
}: ChatAreaProps) {
  const scrollRef = useChatScroll([messages, streamingText, speechTranscript]);

  return (
    <div className="flex flex-col flex-1 min-h-0 bg-transparent">
      {/* MESSAGES VIEWPORT */}
      <div 
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-6 py-4 space-y-6 scrollbar-hide"
      >
        <div className="flex flex-col gap-1 pb-4">
          <div className="flex items-center gap-2 px-2 py-4">
            <span className="text-[10px] font-bold text-muted-foreground/60 uppercase tracking-widest">Conversation Log</span>
            <div className="flex-1 h-px bg-border/40" />
          </div>

          {messages.map((msg) => (
            <div key={msg.id} className="msg-enter">
              <Message 
                from={msg.role === "assistant" ? "assistant" : "user"}
                className={msg.role === "assistant" ? "justify-start" : "justify-end"}
              >
                {msg.role === "assistant" && (
                  <MessageAvatar 
                    name="JV" 
                    className="self-end mb-1 bg-white shadow-sm ring-white ring-offset-1 ring-1" 
                  />
                )}
                
                {msg.role === "assistant" && msg.audioSrc && msg.content.length > 180 ? (
                  /* BIG PARAGRAPH TRANSCRIPT VIEWER */
                  <div className="max-w-[85%] w-full">
                    <TranscriptViewerContainer
                      audioSrc={msg.audioSrc}
                      audioType="audio/mpeg"
                      alignment={msg.alignment || { characters: [], characterStartTimesSeconds: [], characterEndTimesSeconds: [] }}
                      className="bg-white/80 border border-white/40 shadow-sm rounded-2xl p-4 backdrop-blur-md"
                    >
                      <TranscriptViewerAudio className="sr-only" />
                      <div className="mb-4">
                         <TranscriptViewerWords className="text-sm !leading-relaxed text-slate-700" />
                      </div>
                      <div className="flex items-center gap-4 border-t border-slate-100 pt-3">
                        <TranscriptViewerPlayPauseButton className="h-8 w-8 rounded-full shrink-0">
                          {({ isPlaying }) => isPlaying ? <PauseIcon size={14} /> : <PlayIcon size={14} />}
                        </TranscriptViewerPlayPauseButton>
                        <TranscriptViewerScrubBar className="flex-1" />
                      </div>
                    </TranscriptViewerContainer>
                    <span className="text-[9px] mt-1.5 opacity-40 font-jetbrains px-2">
                       {msg.time}
                    </span>
                  </div>
                ) : (
                  <MessageContent 
                    variant="contained" 
                    className={cn(
                      "font-inter leading-relaxed text-[14px] shadow-sm",
                      msg.role === "assistant" 
                        ? "rounded-2xl rounded-bl-none bg-white/80 border border-white/40" 
                        : "rounded-2xl rounded-br-none bg-primary text-white"
                    )}
                  >
                    {msg.isStreaming && msg.role === "assistant" ? (
                      <Response>
                        {streamingText || msg.content}
                      </Response>
                    ) : (
                      msg.content
                    )}
                    <span className={cn(
                      "text-[9px] mt-1.5 opacity-40 font-jetbrains",
                      msg.role === "user" ? "text-right" : "text-left"
                    )}>
                      {msg.time}
                    </span>
                  </MessageContent>
                )}
              </Message>
            </div>
          ))}

          {/* LIVE TRANSCRIPTION BUBBLE (Google STT Style) */}
          {speechTranscript && (
            <div className="msg-enter">
              <Message from="user" className="justify-end opacity-70">
                <MessageContent 
                  variant="contained" 
                  className="rounded-2xl rounded-br-none bg-primary/40 text-white font-inter text-[14px] italic border border-white/10"
                >
                  <div className="flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                    {speechTranscript}
                  </div>
                </MessageContent>
              </Message>
            </div>
          )}
        </div>
      </div>

      {/* INPUT AREA - High Fidelity Floating Design */}
      <div className="px-6 pb-6 pt-2">
        {/* Status Indicator (Minimal) */}
        {agentState === "listening" && !speechTranscript && (
          <div className="mb-3 px-4 py-1.5 bg-primary/5 border border-primary/10 rounded-full w-fit mx-auto animate-in fade-in slide-in-from-bottom-2">
             <div className="flex items-center gap-2">
                <div className="flex gap-1">
                  <span className="w-1 h-1 rounded-full bg-primary animate-bounce [animation-delay:-0.3s]" />
                  <span className="w-1 h-1 rounded-full bg-primary animate-bounce [animation-delay:-0.15s]" />
                  <span className="w-1 h-1 rounded-full bg-primary animate-bounce" />
                </div>
                <span className="text-[11px] text-primary/60 font-medium uppercase tracking-tighter">Listening</span>
             </div>
          </div>
        )}

        <div className="relative group glass-input">
          <div className="bg-white/60 border border-white/40 shadow-xl rounded-[24px] p-2 flex items-center transition-all duration-300 focus-within:bg-white focus-within:shadow-2xl focus-within:border-primary/20 backdrop-blur-md">
            <Button variant="ghost" size="icon" className="rounded-full text-muted-foreground hover:bg-zinc-100 h-10 w-10">
              <FileUp className="w-5 h-5" />
            </Button>
            
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendMessage(inputText)}
              placeholder={agentState === "listening" ? "Say something..." : "Message Jarvis..."}
              className="flex-1 bg-transparent px-4 py-2 text-[14px] focus:outline-none placeholder:text-muted-foreground/50 font-inter"
            />

            <div className="flex items-center gap-1">
              <Button variant="ghost" size="icon" className="rounded-full text-muted-foreground hover:bg-zinc-100 h-10 w-10">
                <Mic className="w-5 h-5" />
              </Button>
              <Button 
                onClick={() => sendMessage(inputText)}
                disabled={!inputText.trim()}
                className="bg-primary text-white rounded-full h-10 w-10 p-0 shadow-lg hover:bg-zinc-800 disabled:opacity-30 disabled:hover:bg-primary transition-all active:scale-95"
              >
                <SendHorizontal className="w-5 h-5" />
              </Button>
            </div>
          </div>
        </div>
        <p className="text-[10px] text-center text-muted-foreground/40 mt-3 font-inter">
          Jarvis v2.4 powered by ElevenLabs & Groq LPU
        </p>
      </div>
    </div>
  );
}
