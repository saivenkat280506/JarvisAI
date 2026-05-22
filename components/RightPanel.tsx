"use client";

import React from "react";
import ActionLog from "./ActionLog";
import ChatArea, { ChatAreaProps, ChatMessage } from "./ChatArea";
import SpeechStrip from "./SpeechStrip";
import InputRow from "./InputRow";
import { ActionLogEntry } from "@/hooks/useJarvis";

interface RightPanelProps {
  messages: ChatMessage[];
  isListening: boolean;
  onSendMessage: (text: string) => void;
  onToggleMic: () => void;
  speechTranscript: string;
  streamingText: string;
  inputText: string;
  setInputText: (v: string) => void;
  actionLogs: ActionLogEntry[];
}

export default function RightPanel({
  messages,
  isListening,
  onSendMessage,
  onToggleMic,
  speechTranscript,
  streamingText,
  inputText,
  setInputText,
  actionLogs,
}: RightPanelProps) {
  return (
    <main className="flex-1 flex flex-col h-full overflow-hidden">
      {/* Action Log — live runtime events */}
      <ActionLog logs={actionLogs} />

      {/* Chat Area */}
      <ChatArea
        messages={messages}
        streamingText={streamingText}
        inputText={inputText}
        setInputText={setInputText}
        sendMessage={onSendMessage}
      />

      {/* Speech Transcript Strip */}
      <SpeechStrip isListening={isListening} transcript={speechTranscript} />

      {/* Text Input Row */}
      <InputRow
        onSendMessage={onSendMessage}
        onToggleMic={onToggleMic}
        isListening={isListening}
      />
    </main>
  );
}
