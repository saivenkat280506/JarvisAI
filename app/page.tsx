"use client";
// Modern J.A.R.V.I.S UI Rebuild Trigger

import { useEffect } from "react";
import TopBar from "@/components/TopBar";
import LeftPanel from "@/components/LeftPanel";
import ChatArea from "@/components/ChatArea";
import QuickActions from "@/components/QuickActions";
import AgentStepTracker from "@/components/AgentStepTracker";
import SettingsSheet from "@/components/SettingsSheet";
import { useJarvis } from "@/hooks/useJarvis";

export default function JarvisWorkspace() {
  const jarvis = useJarvis();

  // Load and apply initial theme settings on startup
  useEffect(() => {
    fetch("http://127.0.0.1:8000/settings")
      .then((r) => r.json())
      .then((data) => {
        if (data && data.theme === "dark") {
          document.documentElement.classList.add("dark");
        } else {
          document.documentElement.classList.remove("dark");
        }
      })
      .catch(() => {});
  }, []);

  return (
    <main className="relative flex h-screen w-screen bg-background overflow-hidden p-4 gap-4">
      {/* Texture Layer */}
      <div className="noise" />

      {/* LEFT PANEL - Glassy & Floating */}
      <LeftPanel 
        agentState={jarvis.agentState} 
        isListening={jarvis.isListening}
        toggleMic={jarvis.toggleMic}
        speechTranscript={jarvis.speechTranscript}
        onSettingsClick={() => jarvis.setSettingsOpen(true)}
      />

      {/* MAIN WORKSPACE - Unified Chat & Actions */}
      <div className="relative flex flex-col flex-1 gap-4 min-w-0">
        <TopBar 
          onSettingsClick={() => jarvis.setSettingsOpen(true)} 
          onRefreshChat={jarvis.clearChat} 
        />
        
        <div className="flex-1 flex gap-4 min-h-0">
          <div className="flex-1 flex flex-col glass rounded-3xl overflow-hidden shadow-sm">
            <QuickActions 
              onSendMessage={jarvis.sendMessage} 
              onClearChat={jarvis.clearChat} 
            />
            <AgentStepTracker logs={jarvis.actionLogs} />
            <ChatArea 
              messages={jarvis.messages} 
              inputText={jarvis.inputText}
              setInputText={jarvis.setInputText}
              sendMessage={jarvis.sendMessage}
              streamingText={jarvis.streamingText}
              speechTranscript={jarvis.speechTranscript}
              agentState={jarvis.agentState}
              toggleMic={jarvis.toggleMic}
            />
          </div>
        </div>
      </div>

      <SettingsSheet 
        open={jarvis.settingsOpen} 
        onOpenChange={jarvis.setSettingsOpen} 
      />

    </main>
  );
}
