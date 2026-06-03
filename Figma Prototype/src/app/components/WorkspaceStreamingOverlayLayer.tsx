import { useWorkspaceStreamingOverlay } from "../hooks/useWorkspaceStreamingOverlay";
import { StreamingOverlay } from "./StreamingOverlay";

/**
 * Always-mounted workspace planner overlay (smoke/refine). Must live outside the
 * ``hasStarted`` gate so the first planning turn can open before DestinationCanvas mounts.
 */
export function WorkspaceStreamingOverlayLayer() {
  const { overlay } = useWorkspaceStreamingOverlay();

  if (!overlay.isVisible && !overlay.isStreaming) return null;

  return (
    <div className="fixed inset-0 z-[100]">
      <StreamingOverlay
        data-testid="wanderly-workspace-streaming-overlay"
        isVisible={overlay.isVisible}
        title={overlay.title}
        subtitle={overlay.subtitle}
        streamText={overlay.streamText}
        isStreaming={overlay.isStreaming}
      />
    </div>
  );
}
