"use client";

import { ConversationProvider } from "@elevenlabs/react";
import { TooltipProvider } from "@/components/ui/tooltip";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ConversationProvider>
      <TooltipProvider>
        {children}
      </TooltipProvider>
    </ConversationProvider>
  );
}
