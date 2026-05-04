# Conversation Panel Implementation Guide

This document describes the implementation of the unified conversation panel for destination-specific interactions in Wanderly.

---

## Overview

**Goal**: Create a single, unified input area within expanded destination cards where users can ask questions, request changes, and refine itineraries. The conversation is destination-specific and persists across sessions.

**Key UX Principle**: When a destination card is expanded, hide the global command bar and show only the conversation panel. This eliminates confusion about which input to use.

---

## Architecture

### Three Conversation Modes

1. **Minimized** - Just an input bar at the bottom
2. **Footer** - Input bar + last message preview
3. **Panel** - Full side panel with entire conversation thread (desktop only)

### State Management

- Each destination has its own conversation thread
- Conversations are stored in `TripStore` as part of the destination object
- Messages persist across expand/collapse cycles

---

## Implementation Steps

### Step 1: Update TripStore.tsx

#### Add Conversation Types

```typescript
export interface ConversationMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  ts: number;
}
```

#### Update DestCard Interface

```typescript
export interface DestCard {
  // ... existing fields ...
  conversation?: ConversationMessage[]; // Add this line
}
```

#### Update TripStoreType Interface

Add these methods to the interface:

```typescript
interface TripStoreType {
  // ... existing methods ...
  
  // Destination-specific conversation
  addConversationMessage: (destId: string, role: "user" | "agent", text: string) => void;
  getConversation: (destId: string) => ConversationMessage[];
}
```

#### Implement Conversation Methods

Add these to the TripProvider component:

```typescript
const addConversationMessage = useCallback((destId: string, role: "user" | "agent", text: string) => {
  setDestinations((prev) => prev.map(d => {
    if (d.id === destId) {
      const newMessage: ConversationMessage = {
        id: crypto.randomUUID(),
        role,
        text,
        ts: Date.now()
      };
      return {
        ...d,
        conversation: [...(d.conversation || []), newMessage]
      };
    }
    return d;
  }));
}, []);

const getConversation = useCallback((destId: string): ConversationMessage[] => {
  const dest = destinations.find(d => d.id === destId);
  return dest?.conversation || [];
}, [destinations]);
```

#### Update Provider Value

Add the new methods to the context provider:

```typescript
return (
  <Ctx.Provider
    value={{
      // ... existing values ...
      addConversationMessage,
      getConversation,
    }}
  >
    {children}
  </Ctx.Provider>
);
```

---

### Step 2: Create ConversationPanel.tsx

Create a new file: `src/app/components/ConversationPanel.tsx`

**Key Features:**
- Three rendering modes: minimized, footer, panel
- Auto-scroll to latest messages
- Typing indicators
- Smart mock response generation
- Keyboard shortcuts (Enter to send, Shift+Enter for new line)

**Component Structure:**

```typescript
interface ConversationPanelProps {
  destId: string;
  destName: string;
  mode: "minimized" | "footer" | "panel";
  onModeChange: (mode: "minimized" | "footer" | "panel") => void;
}
```

**Behavior:**
- **Minimized**: Shows input bar + optional "See chat" button if messages exist
- **Footer**: Shows last message preview + input bar + collapse button
- **Panel**: Full conversation thread in side panel with scrolling

**Mock Response Logic:**

The component includes a `generateMockResponse()` function that intelligently responds to:
- Weather/climate questions
- Food/restaurant queries
- Budget/cost adjustments
- Hotel/accommodation changes
- Day-specific modifications
- Activity/pace adjustments
- Transportation questions
- Family-friendly requests
- Neighborhood/area questions
- Culture/history inquiries

See the full implementation in the created file.

---

### Step 3: Update DestinationCanvas.tsx

#### Import ConversationPanel

```typescript
import { ConversationPanel } from "./ConversationPanel";
```

#### Add Conversation State to ExpandedCard

In the `ExpandedCard` component, add:

```typescript
const [conversationMode, setConversationMode] = useState<"minimized" | "footer" | "panel">("minimized");
```

#### Restructure Layout for Conversation Panel

Replace the existing layout structure with:

```typescript
<div className="flex flex-1 min-h-0">
  {/* Main content area */}
  <div className={`flex flex-col flex-1 min-h-0 transition-all ${
    conversationMode === "panel" ? "lg:w-[60%]" : "w-full"
  }`}>
    <div className="flex flex-col lg:flex-row flex-1 min-h-0">
      {/* ... existing image and content sections ... */}
    </div>

    {/* Conversation footer - shows when not in panel mode */}
    {conversationMode !== "panel" && (
      <ConversationPanel
        destId={dest.id}
        destName={dest.name}
        mode={conversationMode}
        onModeChange={setConversationMode}
      />
    )}
  </div>

  {/* Conversation panel - slides in from right */}
  <AnimatePresence>
    {conversationMode === "panel" && (
      <div className="hidden lg:block w-[40%] shrink-0">
        <ConversationPanel
          destId={dest.id}
          destName={dest.name}
          mode={conversationMode}
          onModeChange={setConversationMode}
        />
      </div>
    )}
  </AnimatePresence>
</div>
```

#### Hide Action Hint When Conversation is Active

Wrap the existing action hint with:

```typescript
{conversationMode === "minimized" && (
  <div className="mt-4 flex items-center gap-2 text-[12px] text-white/25">
    <Zap className="w-3 h-3" />
    <span>Say "let's do {dest.name}" to lock this in, or "no {dest.name}" to remove it</span>
  </div>
)}
```

---

### Step 4: Update App.tsx

#### Hide Global Command Bar When Destination is Expanded

Change this line:

```typescript
{/* Command bar */}
<CommandBar />
```

To:

```typescript
{/* Command bar - hide when destination is expanded */}
{!expandedDest && <CommandBar />}
```

This ensures only one input area is visible at a time.

---

## User Flow

### When No Destination is Expanded
- Global command bar is visible at the bottom
- Used for general trip planning commands
- Example: "Plan a trip to Southeast Asia"

### When Destination is Expanded
- Global command bar disappears
- Conversation panel appears at bottom of expanded card
- Starts in **minimized mode** (just input bar)
- User can:
  - Ask questions: "What's the weather like?"
  - Request changes: "Make day 3 slower"
  - Refine details: "Change to a cheaper hotel"

### Conversation Mode Transitions

1. **Minimized → Footer**:
   - User sends a message
   - Footer expands to show last message + input

2. **Footer → Panel**:
   - User clicks "Full thread" or wants to see history
   - Side panel slides in from right (desktop)
   - 60/40 split: content (60%) + conversation (40%)

3. **Panel → Footer**:
   - User clicks close button (X)
   - Panel slides out, returns to footer mode

4. **Footer → Minimized**:
   - User clicks collapse button (chevron down)
   - Returns to minimal input bar

---

## Visual Design

### Minimized Mode
```
┌─────────────────────────────────────────────┐
│ [💬] Ask questions, request changes...  [→] │ ← Input bar
│           [See chat (3)] if messages exist   │
└─────────────────────────────────────────────┘
```

### Footer Mode
```
┌─────────────────────────────────────────────┐
│ ✨ Last agent message preview...  [Full →]  │ ← Preview
├─────────────────────────────────────────────┤
│ [💬] Ask a question... [→] [˅]              │ ← Input + collapse
└─────────────────────────────────────────────┘
```

### Panel Mode (Desktop)
```
┌──────────────────────┬────────────────┐
│  Destination         │ 💬 Conversation│
│  Content             │ ┌────────────┐ │
│  (Itinerary,         │ │ User msg   │ │
│   flights,           │ │ Agent msg  │ │
│   hotels)            │ │ User msg   │ │
│                      │ └────────────┘ │
│  60% width           │ [Input...] [→] │
│                      │  40% width     │
└──────────────────────┴────────────────┘
```

---

## Key Features

### Destination-Specific Conversations
- Each destination maintains its own conversation thread
- Conversations persist when you collapse/expand cards
- Switching between destinations shows different conversations

### Smart Responses
Mock AI responses understand:
- **Weather queries**: Climate, best time to visit
- **Food questions**: Local cuisine, restaurants
- **Budget concerns**: Cost reduction strategies
- **Hotel preferences**: Alternatives, upgrades
- **Day changes**: Specific day modifications
- **Pace adjustments**: Slow down, add free time
- **Activities**: Add/remove/swap experiences
- **Transportation**: Getting around info
- **Family needs**: Kid-friendly adaptations
- **Cultural interests**: History, traditions

### Smooth Animations
- Messages fade in from bottom
- Panel slides in/out from right
- Typing indicators with bouncing dots
- Auto-scroll to latest message

### Keyboard Support
- **Enter**: Send message
- **Shift+Enter**: New line (in panel mode textarea)
- **Escape**: Close panel (future enhancement)

---

## Mobile Considerations

- Panel mode is desktop-only (hidden on mobile via `lg:block`)
- Mobile shows footer and minimized modes only
- Could enhance with bottom sheet for full conversation on mobile

---

## Future Enhancements

1. **Persist conversations** to Supabase or local storage
2. **Real AI integration** (OpenAI, Claude API)
3. **Actually modify itinerary** based on requests
4. **Voice input** for questions
5. **Suggested questions** based on itinerary
6. **Export conversation** as PDF or text
7. **Share conversation** with travel companions
8. **Mobile bottom sheet** for full conversation view

---

## Testing Checklist

- [ ] Conversation panel appears when destination expanded
- [ ] Global command bar hides when destination expanded
- [ ] Messages persist when collapsing/expanding card
- [ ] Switching destinations shows different conversations
- [ ] Minimized → Footer → Panel transitions work smoothly
- [ ] Enter key sends message
- [ ] Typing indicator appears during mock response
- [ ] Auto-scroll works when new messages arrive
- [ ] Panel closes properly (returns to footer)
- [ ] Footer collapses properly (returns to minimized)
- [ ] Desktop shows 60/40 split in panel mode
- [ ] Mobile hides panel mode, shows footer/minimized only

---

## Files Modified

1. **`src/app/components/TripStore.tsx`**
   - Added `ConversationMessage` interface
   - Updated `DestCard` with `conversation` field
   - Added `addConversationMessage()` and `getConversation()` methods

2. **`src/app/components/ConversationPanel.tsx`** *(NEW FILE)*
   - Full conversation panel component
   - Three rendering modes
   - Mock response generation

3. **`src/app/components/DestinationCanvas.tsx`**
   - Imported `ConversationPanel`
   - Added `conversationMode` state to `ExpandedCard`
   - Restructured layout for panel integration
   - Conditionally hide action hint

4. **`src/app/App.tsx`**
   - Hide global command bar when `expandedDest` is set

---

## Summary

This implementation creates a unified, context-aware conversation interface that feels like chatting with a personal travel agent about a specific destination. The three-mode system (minimized → footer → panel) provides flexibility while maintaining a clean, uncluttered interface.

The key insight: **one input area per context**. When focused on a destination, everything happens in the conversation panel. When planning broadly, use the global command bar. Never both at once.
