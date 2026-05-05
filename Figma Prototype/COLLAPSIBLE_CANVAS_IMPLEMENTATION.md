# Collapsible Destination Canvas Implementation Guide

This document describes the implementation of the collapsible destination canvas feature to maximize agent chat space in Wanderly.

---

## Overview

**Goal**: Allow users to manually collapse destination cards into compact chips, giving the agent chat area 90-95% of vertical screen space for better readability of summaries, guidance, and warnings.

**Key UX Principle**: User control - no auto-collapse. The button is positioned in the Agent Chat header and labeled as "Maximize chat" to reflect the user's mental model (expanding chat space, not collapsing cards).

---

## Problem Being Solved

### Current Issue:
- Tight vertical space on the main screen
- Destination cards take ~40% of screen height
- Agent responses cramped in ~60% of remaining space
- Hard to read long agent summaries, comparisons, and "tell me more" content

### Solution:
- Add manual collapse/expand toggle
- Button in Agent Chat header (not destinations)
- Labeled as "Maximize chat" / "Show cards" (user-centric language)
- Collapsed view shows compact destination chips with key info
- Gives agent responses 90-95% of vertical space when maximized

---

## Visual Design

### Normal State (Cards Visible)
```
┌─────────────────────────────────┐
│ Destinations                    │
│ [Card] [Card] [Card]           │
│  ~40% of screen height          │
├─────────────────────────────────┤
│ Agent Chat    [Maximize chat ⬆]│ ← Button here
│ Agent responses...              │
│  ~60% of screen height          │
└─────────────────────────────────┘
```

### Maximized State (Cards Collapsed)
```
┌─────────────────────────────────┐
│ [Bali 94%] [Santorini 82%] ... │ ← Compact chips
│           [Show cards ⬇]        │
├─────────────────────────────────┤
│ Agent Chat      [Show cards ⬇] │ ← Same button
│ Agent responses...              │
│                                 │
│  ~95% of screen height!         │
│                                 │
└─────────────────────────────────┘
```

---

## Implementation Steps

### Step 1: Update TripStore.tsx

Add global state for the collapsed/expanded state of the destination canvas.

#### Add to Interface

```typescript
interface TripStoreType {
  // ... existing fields ...
  
  // UI state for collapsible canvas
  isCanvasCollapsed: boolean;
  setIsCanvasCollapsed: (v: boolean) => void;
}
```

#### Add State Variable

In the `TripProvider` component, add:

```typescript
const [isCanvasCollapsed, setIsCanvasCollapsed] = useState(false);
```

#### Update Provider Value

Add to the context provider value:

```typescript
return (
  <Ctx.Provider
    value={{
      // ... existing values ...
      isCanvasCollapsed,
      setIsCanvasCollapsed,
    }}
  >
    {children}
  </Ctx.Provider>
);
```

---

### Step 2: Create Collapsed View Component in DestinationCanvas.tsx

#### Import New Icons

```typescript
import {
  // ... existing imports ...
  ChevronDown,
  ChevronUp,
} from "lucide-react";
```

#### Add CollapsedDestinationStrip Component

Add this component BEFORE the main `DestinationCanvas` component:

```typescript
/* ── Collapsed View (Compact Chips) ── */
function CollapsedDestinationStrip({ 
  destinations, 
  onExpand 
}: { 
  destinations: DestCard[], 
  onExpand: () => void 
}) {
  const { setExpandedDest } = useTrip();

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      className="mb-4"
    >
      <div className="flex items-center gap-3 flex-wrap">
        {/* Destination chips */}
        {destinations.map((dest) => (
          <motion.button
            key={dest.id}
            layout
            onClick={() => setExpandedDest(dest.id)}
            className={`group flex items-center gap-2 px-3 py-2 rounded-xl transition-all ${
              dest.pinned
                ? "bg-indigo-500/20 border border-indigo-500/40 text-white/90"
                : "bg-white/[0.05] border border-white/[0.08] text-white/60 hover:bg-white/[0.08] hover:text-white/80"
            }`}
          >
            {/* Mini image */}
            <div className="w-6 h-6 rounded-md overflow-hidden shrink-0">
              <ImageWithFallback src={dest.image} alt={dest.name} className="w-full h-full object-cover" />
            </div>

            {/* Name and score */}
            <span className="text-[13px] font-medium">{dest.name}</span>
            <span className={`text-[11px] ${dest.pinned ? "text-indigo-300" : "text-white/30"}`}>
              {dest.score}%
            </span>

            {/* Pinned indicator */}
            {dest.pinned && <Pin className="w-3 h-3 text-indigo-400" />}

            {/* Warning indicator */}
            {dest.warnings && dest.warnings.length > 0 && (
              <AlertTriangle className="w-3 h-3 text-amber-400" />
            )}
          </motion.button>
        ))}

        {/* Expand button */}
        <button
          onClick={onExpand}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white/[0.03] border border-white/[0.06] text-white/40 hover:text-white/60 hover:bg-white/[0.05] transition-all"
        >
          <ChevronDown className="w-3.5 h-3.5" />
          <span className="text-[12px]">Show cards</span>
        </button>
      </div>
    </motion.div>
  );
}
```

**What this component does:**
- Shows destination chips with mini thumbnail, name, score
- Visual indicators for pinned destinations (indigo background)
- Warning icons if destination has issues
- Click chip to expand that destination
- Click "Show cards" to restore full card view

---

### Step 3: Update DestinationCanvas Main Component

#### Update Hook to Use Global State

Change:
```typescript
export function DestinationCanvas() {
  const { destinations, expandedDest, setExpandedDest } = useTrip();
  // ...
```

To:
```typescript
export function DestinationCanvas() {
  const { destinations, expandedDest, setExpandedDest, isCanvasCollapsed, setIsCanvasCollapsed } = useTrip();
  // ...
```

#### Update Header to Show Collapsed View

Replace the header section:

```typescript
return (
  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className={expandedDest ? "h-full flex flex-col" : "mt-6"}>
    {/* Show collapsed view or regular header */}
    {isCanvasCollapsed && !expandedDest ? (
      <CollapsedDestinationStrip destinations={sorted} onExpand={() => setIsCanvasCollapsed(false)} />
    ) : !expandedDest && (
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-white/80 text-[16px]">Destinations</h2>
          <span className="text-[11px] text-white/20">{visible.length} options · {visible.filter(d => d.pinned).length} pinned</span>
        </div>
      </div>
    )}
```

**Logic:**
- If `isCanvasCollapsed && !expandedDest` → Show collapsed chip view
- If `!expandedDest` → Show normal header
- If `expandedDest` → Show nothing (expanded card has its own header)

#### Update Main Content Rendering

Change the main content conditional:

```typescript
{/* Main content - hide when collapsed */}
{!isCanvasCollapsed && expandedDest && expandedCard ? (
  /* ── Expanded: main card + comparison sidebar ── */
  // ... existing expanded card code ...
) : !isCanvasCollapsed ? (
  /* ── Horizontal Carousel view ── */
  // ... existing carousel code ...
) : null}
```

**Logic:**
- If collapsed → Show nothing (chips are already shown in header)
- If expanded destination → Show expanded card
- Otherwise → Show horizontal carousel

---

### Step 4: Create Agent Chat Header Component in App.tsx

#### Import New Icons

```typescript
import { Compass, Sparkles, Maximize2, Minimize2 } from "lucide-react";
```

#### Add AgentResponsesHeader Component

Add this component BEFORE the `Workspace` component:

```typescript
function AgentResponsesHeader() {
  const { isCanvasCollapsed, setIsCanvasCollapsed } = useTrip();

  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.04]">
      <div className="flex items-center gap-2">
        <Sparkles className="w-4 h-4 text-indigo-400" />
        <h3 className="text-[13px] text-white/70">Agent Chat</h3>
      </div>
      <button
        onClick={() => setIsCanvasCollapsed(!isCanvasCollapsed)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/[0.03] border border-white/[0.06] text-white/50 hover:text-white/80 hover:bg-white/[0.05] transition-all"
      >
        {isCanvasCollapsed ? (
          <>
            <Minimize2 className="w-3.5 h-3.5" />
            <span className="text-[11px]">Show cards</span>
          </>
        ) : (
          <>
            <Maximize2 className="w-3.5 h-3.5" />
            <span className="text-[11px]">Maximize chat</span>
          </>
        )}
      </button>
    </div>
  );
}
```

**What this does:**
- Shows "Agent Chat" header with sparkles icon
- Button toggles `isCanvasCollapsed` state
- Label changes based on state:
  - Not collapsed: "Maximize chat ⬆" (Maximize2 icon)
  - Collapsed: "Show cards ⬇" (Minimize2 icon)

#### Update Agent Responses Container

Find the agent responses section and update it:

**Before:**
```typescript
{/* Agent responses at bottom (scrollable) */}
<div className="flex-1 min-h-0 rounded-xl border border-white/[0.04] bg-[#0a0a0f]/30 overflow-hidden">
  <AgentResponses />
</div>
```

**After:**
```typescript
{/* Agent responses at bottom (scrollable) */}
<div className="flex-1 min-h-0 rounded-xl border border-white/[0.04] bg-[#0a0a0f]/30 overflow-hidden flex flex-col">
  {/* Agent responses header with maximize button */}
  <AgentResponsesHeader />
  <AgentResponses />
</div>
```

**Changes:**
- Add `flex flex-col` to container
- Insert `<AgentResponsesHeader />` before `<AgentResponses />`

---

### Step 5: Update AgentResponses.tsx

#### Change Container Class

Find the main container div and update its className:

**Before:**
```typescript
<div
  ref={scrollRef}
  className="h-full overflow-y-auto px-6 py-6 bg-[#0a0a0f]/50"
  // ...
>
```

**After:**
```typescript
<div
  ref={scrollRef}
  className="flex-1 overflow-y-auto px-6 py-6 bg-[#0a0a0f]/50"
  // ...
>
```

**Change:** `h-full` → `flex-1`

**Why:** The parent container is now `flex flex-col` with a header, so AgentResponses needs to be `flex-1` to take remaining space.

---

## Complete Flow

### User Journey:

1. **User starts trip planning**
   - Destinations load as full cards
   - Agent responses appear below
   - Agent Chat header shows "Maximize chat ⬆" button

2. **User needs more chat space**
   - Clicks "Maximize chat ⬆" button in Agent Chat header
   - Destination cards collapse to compact chips
   - Agent chat area expands to ~95% of vertical space
   - Button changes to "Show cards ⬇"

3. **User wants to see full cards again**
   - Clicks "Show cards ⬇" button
   - Chips expand back to full destination cards
   - Agent chat returns to normal size
   - Button changes to "Maximize chat ⬆"

### State Flow:

```
isCanvasCollapsed: false
↓ (User clicks "Maximize chat")
isCanvasCollapsed: true
  → DestinationCanvas shows chips
  → Button label changes to "Show cards"
↓ (User clicks "Show cards")
isCanvasCollapsed: false
  → DestinationCanvas shows full cards
  → Button label changes to "Maximize chat"
```

---

## Detailed Chip Design

Each destination chip shows:
- **Mini thumbnail** (6x6 rounded image)
- **Destination name** (13px, medium weight)
- **Match score** (11px, muted)
- **Pin icon** (if destination is pinned)
- **Warning icon** (if destination has warnings)

**Visual states:**
- **Pinned**: Indigo background (`bg-indigo-500/20`), indigo border
- **Normal**: Subtle white background (`bg-white/[0.05]`), gray border
- **Hover**: Slightly brighter background and text

**Interaction:**
- Click chip → Expands that specific destination
- Click "Show cards" → Restores all cards to carousel view

---

## Key UX Principles

### 1. User-Centric Language
- ✅ "Maximize chat" (what user wants)
- ❌ "Collapse destinations" (implementation detail)

### 2. Button Placement
- ✅ In Agent Chat header (action affects chat space)
- ❌ In Destinations header (confusing ownership)

### 3. No Auto-Behavior
- ✅ Manual toggle only
- ❌ Auto-collapse after timer (jarring)

### 4. Preserve Context
- Chips show key info: name, score, status
- User can still access any destination with one click
- No information loss, just more compact

---

## Responsive Behavior

### Desktop (>1024px):
- Chips wrap horizontally
- "Show cards" button on same line
- Full carousel when expanded

### Tablet (768-1024px):
- Chips may wrap to 2 rows
- Still functional and readable

### Mobile (<768px):
- Chips stack vertically if needed
- "Show cards" button moves below chips
- Maximize feature is especially valuable on small screens

---

## Files Modified Summary

### 1. `src/app/components/TripStore.tsx`
**Changes:**
- Added `isCanvasCollapsed: boolean` to interface
- Added `setIsCanvasCollapsed` function to interface
- Added state variable and setter
- Exposed via context provider

**Lines added:** ~10

---

### 2. `src/app/components/DestinationCanvas.tsx`
**Changes:**
- Added `ChevronDown`, `ChevronUp` imports
- Created `CollapsedDestinationStrip` component (~60 lines)
- Updated `DestinationCanvas` to use global state
- Updated header logic to show chips when collapsed
- Updated main content rendering to hide when collapsed

**Lines added:** ~80

---

### 3. `src/app/App.tsx`
**Changes:**
- Added `Maximize2`, `Minimize2` imports
- Created `AgentResponsesHeader` component (~30 lines)
- Updated agent responses container to include header
- Changed container to `flex flex-col`

**Lines added:** ~35

---

### 4. `src/app/components/AgentResponses.tsx`
**Changes:**
- Changed main container className from `h-full` to `flex-1`

**Lines changed:** 1

---

## Testing Checklist

- [ ] Button appears in Agent Chat header with correct label
- [ ] Clicking "Maximize chat" collapses cards to chips
- [ ] Chips show: thumbnail, name, score, pin icon, warning icon
- [ ] Clicking a chip expands that specific destination
- [ ] Clicking "Show cards" restores full carousel view
- [ ] Button label changes between "Maximize chat" and "Show cards"
- [ ] Icons change (Maximize2 ↔ Minimize2)
- [ ] Agent chat area expands to fill available space when maximized
- [ ] No auto-collapse behavior (manual only)
- [ ] Pinned destinations have indigo styling in chips
- [ ] Warning icons appear in chips when relevant
- [ ] State persists while navigating (doesn't reset unexpectedly)
- [ ] Expanded destination view is not affected by collapse state
- [ ] Responsive: chips wrap properly on smaller screens

---

## Common Issues & Solutions

### Issue: Button doesn't appear
**Solution:** Make sure `AgentResponsesHeader` is added to App.tsx before `<AgentResponses />`

### Issue: Chips don't show when collapsed
**Solution:** Check that `isCanvasCollapsed` state is properly connected in DestinationCanvas

### Issue: Agent responses don't expand
**Solution:** Verify AgentResponses container is `flex-1` not `h-full`

### Issue: Button label doesn't change
**Solution:** Check conditional rendering in `AgentResponsesHeader` uses `isCanvasCollapsed` correctly

### Issue: State resets unexpectedly
**Solution:** State is in TripStore (global), not local component state

---

## Future Enhancements

1. **Persist preference** to localStorage
2. **Keyboard shortcut** (e.g., `Cmd+M` to maximize)
3. **Animation polish** (cards → chips transition)
4. **Chip customization** (show flight time, cost in chips)
5. **Mobile bottom sheet** for expanded destinations
6. **Accessibility**: Screen reader announcements when toggling

---

## Summary

This implementation solves the vertical space problem by:

1. **Adding manual collapse toggle** in Agent Chat header
2. **Using user-centric language** ("Maximize chat" not "Collapse")
3. **Preserving information** in compact chips (thumbnail, name, score, status)
4. **Giving chat 90-95% of screen** when maximized
5. **Maintaining full functionality** (click chip → expand destination)

The result: Users can comfortably read long agent summaries, guidance, and warnings while keeping destinations accessible with one click.
