import { DestinationCanvas } from "./DestinationCanvas";
import { AgentResponses } from "./AgentResponses";

export function MainWorkspace() {
  return (
    <div className="flex flex-col h-full">
      {/* Top: Destination Cards (Fixed, Non-Scrolling) */}
      <div className="flex-shrink-0">
        <DestinationCanvas />
      </div>
      
      {/* Divider */}
      <div className="border-t border-white/[0.08]" />
      
      {/* Bottom: Agent Responses (Scrollable) */}
      <div className="flex-1 min-h-0">
        <AgentResponses />
      </div>
    </div>
  );
}
