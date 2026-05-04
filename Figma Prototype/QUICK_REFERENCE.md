# Quick Reference: Component Mapping

## Before → After

### Layout Structure

**BEFORE (3 zones):**
```
ProfilePanel (280px left)      DestinationCanvas (center)    AgentFeed (280px right)
├─ Profile Pills               ├─ Destination Cards          ├─ User Messages
└─ Advisor Section                 (scrollable grid)         └─ Agent Messages
   ├─ Insights
   ├─ Warnings
   └─ Actions
```

**AFTER (2 zones):**
```
ProfilePanel (280px left)      MainWorkspace (full width center)
└─ Profile Pills               ├─ DestinationCards (top, fixed)
                               │  └─ Grid of cards (non-scrolling)
                               │
                               ├─ [Divider Line]
                               │
                               └─ AgentResponses (bottom, scrollable)
                                  ├─ User Messages
                                  ├─ Agent Messages
                                  ├─ Insights
                                  ├─ Warnings
                                  ├─ Comparisons
                                  └─ Actions
```

---

## File Structure Changes

### Components to Create:
```
✅ MainWorkspace.tsx         - New parent container for two sections
✅ AgentResponses.tsx        - New unified response renderer
```

### Components to Modify:
```
🔧 ProfilePanel.tsx         - Remove Advisor section
🔧 TripStore.tsx            - Replace messages/insights with responses[]
🔧 CommandBar.tsx           - Route all output to responses
🔧 App.tsx                  - Update layout to two zones
```

### Components to Remove:
```
❌ AgentFeed.tsx            - Functionality moved to AgentResponses
```

---

## State Changes

### Old State:
```typescript
interface TripState {
  messages: Message[];        // Right sidebar
  insights: Insight[];        // Left sidebar (advisor)
  destinations: DestCard[];
  // ...
}
```

### New State:
```typescript
interface TripState {
  responses: AgentResponse[]; // Everything unified
  destinations: DestCard[];
  // ...
}

interface AgentResponse {
  id: string;
  type: "message" | "insight" | "comparison" | "warning" | "success" | "tip";
  role?: "user" | "agent";
  text: string;
  timestamp: Date;
  actions?: Action[];
  undoId?: string;
}
```

---

## API Changes

### Old Functions:
```typescript
addMessage(role: "user" | "agent", text: string)
addInsight(type: string, text: string, actions?: Action[])
dismissInsight(id: string)
```

### New Functions:
```typescript
addResponse(response: AgentResponse)
dismissResponse(id: string)
```

### Example Usage:

**Old Way:**
```typescript
// User message
store.addMessage("user", "Show me something cheaper");

// Agent response
store.addMessage("agent", "Here are more affordable options...");

// Insight with actions
store.addInsight("warning", "Budget exceeded", [
  { id: "fix-budget", label: "Adjust budget", ... }
]);
```

**New Way:**
```typescript
// User message
store.addResponse({
  type: "message",
  role: "user",
  text: "Show me something cheaper"
});

// Agent response
store.addResponse({
  type: "message",
  role: "agent",
  text: "Here are more affordable options..."
});

// Insight with actions
store.addResponse({
  type: "warning",
  text: "Budget exceeded",
  actions: [
    { id: "fix-budget", label: "Adjust budget", ... }
  ]
});
```

---

## Visual Hierarchy

```
┌─────────────────────────────────────────────────────┐
│ ALWAYS VISIBLE                                      │
│                                                     │
│ ┌─────────┬──────────────────────────────────────┐ │
│ │         │  🏝️ Bali    🌴 Hawaii   🏖️ Greece   │ │
│ │ Profile │  [Cards fixed at top]                │ │
│ │ Pills   │                                       │ │
│ │         │  Score: 96    Score: 92   Score: 89  │ │
│ └─────────┴──────────────────────────────────────┘ │
│                                                     │
└─────────────────────────────────────────────────────┘
           ─────────────────────────────────
┌─────────────────────────────────────────────────────┐
│ SCROLLABLE AREA                        ↕ Scroll     │
│                                                     │
│           💬 User: "Help me choose"                 │
│                                                     │
│           🤖 Agent: "Let me compare these..."       │
│           ┌──────────────────────────────┐          │
│           │ ## Comparing Bali vs Hawaii  │          │
│           │                              │          │
│           │ ### Budget Breakdown         │          │
│           │ Bali: $4,850                 │          │
│           │ Hawaii: $5,200               │          │
│           │                              │          │
│           │ ### My Recommendation        │          │
│           │ Choose Bali for...           │          │
│           └──────────────────────────────┘          │
│                                                     │
│           ⚡ SUGGESTED ACTIONS                       │
│           [📌 Pin Bali] [🔄 Compare others]         │
│                                                     │
│           💬 User: "Pin Bali"                       │
│                                                     │
│           ✅ Success: "Bali is now pinned!"         │
│           [↩️ Undo]                                 │
│                                                     │
└─────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────┐
│ ✍️  Describe your trip or refine your plans...     │
│                                  [Send] │ Working...│
└─────────────────────────────────────────────────────┘
```

---

## Interaction Flow

### User types command → Agent responds

**Old Flow:**
1. Type in CommandBar (bottom)
2. Look at AgentFeed (right) for acknowledgment
3. Look at Advisor (left) for insights
4. Look at DestinationCanvas (center) for card updates

**New Flow:**
1. Type in CommandBar (bottom)
2. Look at AgentResponses (directly above) for everything
3. Glance at destination cards (always visible at top)

---

## CSS Positioning Guide

```css
/* Main Workspace Container */
.main-workspace {
  display: grid;
  grid-template-rows: auto 1fr;
  height: calc(100vh - 80px); /* Subtract command bar height */
}

/* Top Section - Destination Cards */
.destination-cards {
  grid-row: 1;
  overflow: visible; /* No scroll */
  padding: 1.5rem;
  border-bottom: 1px solid rgba(255,255,255,0.08);
}

/* Bottom Section - Agent Responses */
.agent-responses {
  grid-row: 2;
  overflow-y: auto; /* Scrollable */
  padding: 1.5rem;
  background: rgba(10, 10, 20, 0.5);
}
```

---

## Response Type Visual Guide

```
┌─────────────────────────────────────────┐
│ 💬 message (user)        [Right-aligned]│
│    "Show me something cheaper"          │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│🤖 message (agent)                        │
│   "Here are more affordable options..."  │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│⚠️ warning                              ✕ │
│   Budget exceeded for current options    │
│   ⚡ SUGGESTED ACTIONS                   │
│   [🔍 Find cheaper] [✂️ Shorten trip]   │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│💡 tip                                  ✕ │
│   Tip: Pin destinations to lock prices   │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│✅ success                                 │
│   Bali is now pinned!                    │
│   [↩️ Undo this change]                  │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│ℹ️ comparison                           ✕ │
│   ## Comparing Bali vs Hawaii            │
│   ### Budget Breakdown                   │
│   **Bali:** $4,850                       │
│   **Hawaii:** $5,200                     │
│   ### Recommendation                     │
│   Choose Bali if...                      │
└─────────────────────────────────────────┘
```

---

## Testing Checklist

After implementing the new layout:

**Layout Tests:**
- [ ] Destination cards stay fixed at top when scrolling
- [ ] Agent responses scroll independently
- [ ] Divider line visible between sections
- [ ] Profile sidebar stays sticky on left
- [ ] Command bar stays sticky at bottom

**Interaction Tests:**
- [ ] Typing command adds user message to responses
- [ ] Agent replies appear in responses area
- [ ] Auto-scrolls to latest response
- [ ] Can manually scroll up to see history
- [ ] "Scroll to latest" button appears when scrolled up

**Response Type Tests:**
- [ ] User messages render right-aligned
- [ ] Agent messages render left-aligned with icon
- [ ] Insights show with action buttons
- [ ] Warnings display with amber theme
- [ ] Comparisons render markdown correctly
- [ ] Success messages show undo button

**State Tests:**
- [ ] All responses in single array
- [ ] Responses persist correctly
- [ ] Undo restores responses state
- [ ] Dismissing response removes it from view

---

## Quick Copy-Paste Snippets

### MainWorkspace Component Template:
```tsx
export function MainWorkspace() {
  return (
    <div className="flex flex-col h-full">
      {/* Top: Destination Cards (Fixed) */}
      <DestinationCards />
      
      {/* Divider */}
      <div className="border-t border-white/[0.08]" />
      
      {/* Bottom: Agent Responses (Scrollable) */}
      <AgentResponses />
    </div>
  );
}
```

### AgentResponses Component Template:
```tsx
export function AgentResponses() {
  const responses = useTripStore(s => s.responses);
  const scrollRef = useRef<HTMLDivElement>(null);
  
  // Auto-scroll to bottom on new response
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth'
    });
  }, [responses.length]);
  
  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto p-6">
      {responses.map(response => (
        <ResponseCard key={response.id} response={response} />
      ))}
    </div>
  );
}
```

---

## End of Quick Reference

Use this as a cheat sheet when implementing or explaining the new layout structure!
