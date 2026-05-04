# Wanderly Travel Planner - UI Component Specification v1.1 (IMPLEMENTED)

**Status:** ✅ This layout is now live in the prototype!

**Last Updated:** March 3, 2026

**Changes from v1.0:**
- Switched from three-zone to two-zone layout
- Unified agent responses (messages, insights, comparisons) in single scrollable area
- Removed right sidebar (AgentFeed)
- Removed Advisor section from ProfilePanel
- All agent communication now appears below destination cards
- Destination cards fixed at top (non-scrolling) for persistent context

---

## Overview

Wanderly is a living workspace / AI super-agent canvas for travel planning. Unlike traditional step-by-step wizards or chatbots, the agent works on everything in parallel — destinations, itineraries, flights, hotels, and activities all materialize simultaneously as cards on a canvas. The user steers by reacting (expanding, dismissing, pinning cards) or typing natural language refinements in a command bar.

**Design Philosophy:**
- Dark, immersive, visually compact
- Hybrid AI + direct manipulation interaction model
- Context lives spatially in the workspace, not buried in a chat thread
- Progressive disclosure: unpinned cards show placeholders, pinned cards reveal full details
- Natural language commands trigger parallel agent work with visual feedback

---

## Architecture & Layout

### Two-Zone Spatial Layout

```
┌─────────────┬───────────���────────────────────┐
│             │  Destination Cards (Fixed)    │
│             │  [Card] [Card] [Card]         │
│  Profile    ├────────────────────────────────┤
│  Sidebar    │  Agent Responses (Scrollable)  │
│             │                                │
│  (Left)     │  • All advisor insights        │
│  280px      │  • Comparison analysis         │
│  Sticky     │  • Conversation messages       │
│             │  • Action suggestions          │
│             │                                │
│             │  ↕ Scrolls independently       │
│             │                                │
└─────────────┴────────────────────────────────┘
                Command Bar (Sticky Bottom)
```

**Key Layout Principles:**

1. **Left Sidebar (280px, Sticky):** Traveler Profile only - just the editable pills (when, who, vibe, budget, duration)

2. **Main Workspace (Center, Full Height):**
   - **Top Section (Fixed):** Destination cards in grid - does NOT scroll
   - **Bottom Section (Scrollable):** All agent communication - insights, comparisons, messages, suggestions
   - Divider line between the two sections
   - Bottom section scrolls independently while destination cards stay visible at top

3. **Command Bar (Sticky Bottom):** Input field spans full width

**Why This Layout?**
- Destinations always visible for context
- All agent responses in one place, close to where user types
- Natural conversation flow: user types → sees responses below cards → types again
- No confusion about where to look for agent feedback

**Responsive Behavior:**
- Desktop: Two-zone layout as shown
- Tablet/Mobile: Stack vertically, profile collapses to drawer

---

## Component Specifications

---

### 1. Command Bar (`CommandBar.tsx`)

**Location:** Sticky at bottom, spans full width above any workspace chrome

**Visual Design:**
- Dark background `bg-[#0f0f1a]` with subtle top border
- Glassmorphic effect with backdrop blur
- Centered, max-width container with padding
- Input has subtle glow on focus (indigo accent)

**Core Functionality:**

#### Input Field
- Natural language text input
- Placeholder: "Describe your trip or refine your plans..."
- Auto-focus on mount (optional)
- Enter to submit, supports multi-line with Shift+Enter
- Shows agent status while processing

#### Agent Status Indicator
- Appears below input when agent is working
- Shows current phase: "Researching destinations...", "Evaluating options...", "Finalizing plans..."
- Animated dots/spinner
- Fades out when complete

#### Natural Language Processing (Mock/Pattern Matching)

The CommandBar detects user intent through pattern matching and triggers appropriate actions:

**Pattern Categories:**

1. **Initial Trip Request**
   - Pattern: First message, describes trip parameters
   - Example: "I want to go somewhere warm with good food in March for a week with my partner for under $5k"
   - Action: Parse entities (when, who, vibe, budget, duration), initialize profile, generate 3-4 destination cards

2. **Profile Updates**
   - Pattern: "change budget to...", "actually make it...", "we're now..."
   - Action: Update specific profile pill, trigger re-evaluation with insights

3. **Destination-Specific Actions**
   - Pattern: "tell me more about [destination]", "expand [destination]"
   - Action: Set expanded destination ID
   - Pattern: "remove [destination]", "no [destination]", "not interested in [destination]"
   - Action: Mark destination as dismissed
   - Pattern: "pin [destination]", "lock in [destination]"
   - Action: Set destination.pinned = true, trigger progressive loading animation

4. **Comparison Requests**
   - Pattern: "compare [dest1] and [dest2]", "help me choose", "which is better"
   - Detection: Check for pinned destinations (2+)
   - Action: Generate detailed multi-paragraph comparison analysis
   - Output: Add to agent responses section below destination cards

5. **General Refinements**
   - Pattern: "show me something more [adventurous/relaxing/etc]"
   - Action: Adjust vibe, re-score destinations

6. **Undo/Redo**
   - Pattern: "undo", "go back", "revert that"
   - Action: Pop from undo stack, restore previous state

**Implementation Strategy:**
- Use regex + keyword matching for MVP
- Store patterns in a config object with priority ordering
- Extract entities (dates, numbers, destination names) with simple parsers
- For production: integrate with LLM API for true NLU

**State Updates:**
- Add user message to responses array
- Set phase to "working" → "researching" → "complete"
- Update agentStatus with friendly progress text
- Generate agent response after delay (simulated processing)
- All responses appear in the scrollable section below destination cards
- For actions, push undo snapshot to undoStack before mutating state

---

### 2. Profile Panel (`ProfilePanel.tsx`)

**Location:** Left sidebar, sticky, 280px width

**Content:** Traveler Profile pills only (no advisor section)

**Visual Design:**
- Dark background matching main canvas
- Subtle borders and spacing
- Glassmorphic cards with backdrop blur

---

#### Traveler Profile Section

**Header:**
- Small indigo dot indicator
- Label: "TRAVELER PROFILE" (uppercase, tracked)
- Text: `text-[11px] text-white/30`

**Editable Pills:**

Each profile attribute is an interactive pill:

**Profile Fields:**
```javascript
{
  when: { icon: "📅", label: "When", value: "Mar 15–22" },
  who: { icon: "👥", label: "Who", value: "2 travelers" },
  vibe: { icon: "✨", label: "Vibe", value: "Warm, culture, food" },
  budget: { icon: "💰", label: "Budget", value: "~$3-5k total" },
  duration: { icon: "⏱️", label: "Duration", value: "7 days" }
}
```

**Pill Behavior:**

**Collapsed State:**
- Shows icon, label (small, uppercase), and current value
- Background: `bg-white/[0.04]`, border: `border-white/[0.06]`
- Hover: Slightly lighter, border brightens
- Click: Opens dropdown

**Expanded State (Dropdown):**
- Dropdown appears below pill
- Background: `bg-[#1a1a2e]`, border: `border-white/[0.1]`
- Shadow: `shadow-xl shadow-black/40`
- Lists preset options for that field
- Selected option highlighted with indigo background + checkmark
- Click option to select

**Vibe Field - Custom Input:**
- After preset options, shows border separator
- Text input field: "Or type your own vibe..."
- "Set" button next to input
- Helper text: "Any keywords, the AI will understand"
- Submits custom value same as preset

**onChange Behavior:**

When user changes a profile pill:

1. **Update Profile State**: `updateProfilePill(key, newValue)`
2. **Generate Reaction**: Determine warnings and insights
3. **Add Agent Response**: Acknowledgment message appears in the responses section below destination cards
4. **Delay & Animate**: Use setTimeout to stagger updates (300-500ms) for natural feel

---

### 3. Main Workspace (`MainWorkspace.tsx`)

**Location:** Center area, takes remaining width after left sidebar

**Structure:** Two distinct sections stacked vertically

---

#### 3a. Top Section: Destination Cards Area (Fixed, Non-Scrolling)

**Layout:**
- Position: Sticky/fixed at top of main workspace
- Height: Auto-fits content (typically 40-60% of viewport height)
- Background: Dark gradient
- Does NOT scroll - always visible

**Grid Layout:**
- 2-3 columns depending on viewport width
- Gap between cards: 1rem
- Padding: 1.5rem

**Content:**
- Shows all active destination cards as GridCard components
- Cards can be collapsed (GridCard) or expanded (ExpandedCard)
- When one card expands, comparison sidebar slides in

**Visual Divider:**
- Subtle gradient border/line at bottom
- Separates from responses section below
- Semi-transparent: `border-white/[0.08]`

---

#### 3b. Bottom Section: Agent Responses Area (Scrollable)

**Layout:**
- Position: Below destination cards, fills remaining height
- Scrollable: `overflow-y: auto`
- Background: Slightly darker than cards area
- Padding: 1.5rem
- Min-height: 40% of viewport

**Content Types (All Unified Here):**

1. **Conversation Messages:**
   - User messages (right-aligned, lighter bg)
   - Agent messages (left-aligned, bot icon)
   - Chronological order, latest at bottom
   - Timestamps subtle

2. **Insights & Suggestions:**
   - Warning cards (amber theme)
   - Tips (indigo theme)
   - Success messages (emerald theme)
   - Info cards (blue theme)

3. **Comparison Analyses:**
   - Rich markdown rendering
   - Full comparison breakdowns
   - Headers, bold text, bullet points
   - Scrollable within card if very long

4. **Action Suggestions:**
   - Interactive action buttons
   - Shows when agent suggests changes
   - Execute button triggers action + shows undo option

**Message/Card Structure:**

```
┌─────────────────────────────────────────┐
│ [Icon] Message/Insight Content        ✕ │
│                                         │
│ Markdown formatted text...              │
│ - Bullet points                         │
│ - More details                          │
│                                         │
│ ⚡ SUGGESTED ACTIONS (if applicable)    │
│ ┌─────────────────────────────────────┐ │
│ │ [Icon] Action Label          [Play] │ │
│ │        Description text             │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ [Undo Button] (if action was executed) │
└─────────────────────────────────────────┘
```

**Markdown Rendering:**
- `### Header` → styled h3
- `**bold**` → bright color
- `---` → horizontal divider
- `- item` → bullet with `•`
- Code blocks with syntax highlighting (optional)
- Links clickable (if applicable)

**Undo Support:**
- Responses from executed actions show "Undo this change" button
- Click to restore previous state
- After undo: Button shows "✓ Undone" (disabled)

**Scroll Behavior:**
- Auto-scrolls to bottom when new response added
- Smooth scroll animation
- User can scroll up to read history
- "Scroll to latest" button appears when scrolled up

**Empty State:**
- When no responses yet, shows friendly prompt:
  - "The agent will respond here..."
  - Light illustration or icon
  - Subtle animation

---

### 4. Destination Card Components

_(These remain in the top fixed section)_

#### 4a. GridCard Component

**Collapsed View (Grid Mode)**

**Visual Structure:**
```
┌─────────────────────────────────────┐
│ [Hero Image with Gradient Overlay] │
│   [Score Badge]                     │
│   [Pin/Dismiss Controls]            │
│                                     │
│   Name                              │
│   Country                           │
├─────────────────────────────────────┤
│ Hook text description...            │
│                                     │
│ 🌡️ Temp  ✈️ Flight  💰 Cost        │
│                                     │
│ [Vibe] [Tags]                       │
│                                     │
│ [Warning Banner if present]         │
└─────────────────────────────────────┘
```

**Interactions:**
- Click anywhere: Expand card (`setExpandedDest(card.id)`)
- Click pin icon: Toggle `card.pinned`
- Click dismiss (X): Set `card.dismissed = true`
- Hover: Border brightens, slight scale

**Pinned Visual Indicator:**
- Indigo border glow
- Gradient line at top edge

**Warning Badge:**
- Amber alert icon in top-right
- Shows when warnings exist for this destination
- Clicking shows details in responses section below

---

#### 4b. ExpandedCard Component

**Full View (Expanded Mode)**

**Layout:**
```
┌────────────┬───────────────────────────────────────┐
│            │  [Trip Flow: Home → Ubud → Seminyak  │
│  Hero      │                          → Home]      │
│  Image     │                                       │
│  Gallery   │  Hook description...      [← Back]   │
│            │                                       │
│  (L-side)  │  🌡️ Temp  ✈️ Flight  💰 Cost         │
│            │  [Vibe Tags]                          │
│  [Score]   │                                       │
│  [Pin]     │  ┌──────────────┬──────────────────┐ │
│  [Close]   │  │ Itinerary    │ Flights/Hotels   │ │
│            │  │              │                  │ │
│            │  │ N-Day Plan   │ Flight Options   │ │
│            │  │ with dates   │ Hotel Options    │ │
│            │  │ & locations  │                  │ │
│            │  │              │ Total Cost       │ │
│            │  │ Activities   │                  │ │
│            │  └──────────────┴──────────────────┘ │
│            │                                       │
│            │  💡 Say "let's do Bali" to lock in   │
└────────────┴───────────────────────────────────────┘
```

**Hero Image Gallery:**
- Shows multiple images if available (`card.images`)
- Auto-plays through images once (3 sec each)
- Manual navigation: Prev/Next buttons on hover
- Dot indicators at bottom
- Click image area: Collapse card

**Trip Flow Visualization:**
- Shows at top: "Home → Ubud → Seminyak → Home"
- Blue MapPin icons for intermediate locations
- Gray MapPin icons for home start/end
- Parsed from itinerary labels that contain "→"

**Itinerary Display:**

**Header:**
- "N-DAY PLAN" with date range (if profile has "when")
- Example: "7-Day Plan — Mar 15–22"

**Day Cards:**
- Left: Date badge (calendar-style with month + day)
- Right: Day label + top 2 activity items
- Collapsed by default, shows "+N more" if >2 items

**Progressive Disclosure (Unpinned vs Pinned):**

**Unpinned State:**
- Flights: Generic placeholder
  - "Multiple airlines available"
  - "Searching best routes & times"
  - Price range: "$950-$1,200"
  - Dashed border, grayed out
- Hotels: Generic placeholder
  - "Various accommodations"
  - "Comparing hotels & areas"
  - Price range: "$90-$180/n"
  - Dashed border, grayed out
- No navigation arrows
- Total shows range: "$4,200-$6,500"

**Pinned State:**
- Flights: Actual options appear with animation
  - Airline name, route, duration
  - Exact price
  - "Best" badge if applicable
  - Navigation arrows to cycle through options
  - Counter: "1 of 3 options"
- Hotels: Actual options appear
  - Hotel name, area, nights
  - Exact price per night
  - Hotel thumbnail image
  - Navigation arrows + counter
- "Itinerary Confirmed" badge at top
  - Green checkmark icon
  - "All prices locked & bookings finalized"
- Total shows exact cost: "$4,850"

**Animation Sequence (When Pinned):**
1. Show "Itinerary Confirmed" badge (fade in from top)
2. Flight placeholder → actual flight (scale + fade, delay 0.6s)
3. Hotel placeholder → actual hotel (scale + fade, delay 0.8s)
4. Total updates (fade transition, delay 1.0s)

**Flight/Hotel Navigation:**
- Only shows when pinned AND multiple options exist
- Circular buttons on left/right edges
- Click: Cycle through options with smooth transition
- Counter below shows "2 of 4 options"

---

#### 4c. ComparisonMiniCard Component

**Shown in Sidebar (When Another Card is Expanded)**

**Compact Layout:**
```
┌──────────────────────────────┐
│ [Thumbnail Image]            │
│   Name       [Score Badge]   │
│   Country                    │
├──────────────────────────────┤
│ ✈️ Flight  | 🏨 Hotel | 💰    │
│  Price       Price     Total │
├──────────────────────────────┤
│ [Vibe] [Tags]                │
│                              │
│ [Warning if present]         │
│                              │
│       → View full plan       │
└──────────────────────────────┘
```

**Interactions:**
- Click: Expand this card (`setExpandedDest(card.id)`)
- Hover: Border brightens

**Pinned Indicator:**
- Indigo border + gradient top line

**Quick Stats:**
- Shows best flight price, best hotel price/night, total cost
- Icons with abbreviated info
- Compact grid layout

---

### 5. Comparison Sidebar

**Triggered By:** "help me choose" command with 2+ pinned destinations

**Location:** Within DestinationCanvas, slides in from right when expanded card is open

**Visual Design:**
- Same dark styling as main cards
- Slightly narrower (~320px)
- Scrollable independently

**Content Structure:**

Generated as a "comparison" type insight in ProfilePanel, rendered with rich markdown:

```markdown
## Comparing Bali vs Santorini

### Logistics & Convenience
**Flight Time:**
- Bali: 20h with 1 connection (JFK → NRT → DPS)
- Santorini: 12h with 1 connection (JFK → ATH → JTR)

**Winner:** Santorini — 8 hours shorter travel time

---

### Budget Breakdown (2 travelers)
**Bali:**
- Flights: $1,200 × 2 = $2,400
- Hotels: $180/n × 6 nights = $1,080
- Activities: ~$400
- Total: ~$4,850

**Santorini:**
- Flights: $950 × 2 = $1,900
- Hotels: $280/n × 6 nights = $1,680
- Activities: ~$600
- Total: ~$5,600

**Winner:** Bali — $750 cheaper

---

### Experience Highlights

**Bali:**
- Rice terraces, temples, and spiritual atmosphere
- Incredible food scene at all price points
- Mix of cultural immersion and beach relaxation
- More authentic, less touristy feel

**Santorini:**
- Iconic caldera sunsets and white architecture
- World-class wine and fine dining
- Romantic atmosphere, luxury-focused
- Compact island, very walkable

---

### Recommendation

Based on your profile (2 travelers, warm/culture/food vibe, ~$3-5k budget):

**Choose Bali if:** You want deeper cultural immersion, better food value, and don't mind the longer flight. You'll get more bang for your buck and a more diverse experience.

**Choose Santorini if:** You prioritize shorter travel time, romance, and iconic scenery. You're willing to pay premium for that signature caldera view.

**My pick: Bali** — Your "warm, culture, food" vibe is a perfect match, and the budget savings give you room to splurge on amazing experiences.
```

**Rendering:**
- Headers styled with hierarchy (h2, h3, h4)
- Bold text highlighted
- Horizontal rules as visual breaks
- Bullet points with proper spacing
- Scrollable if content exceeds viewport

---

## State Management (`TripStore.tsx`)

**Architecture:** Zustand store (or React Context + useState)

**Core State:**

```typescript
interface TripState {
  // Profile
  profile: ProfilePill[];
  
  // Destinations
  destinations: DestCard[];
  expandedDest: string | null;
  
  // Agent Responses (replaces separate messages/insights)
  responses: AgentResponse[];
  
  // Agent State
  phase: "idle" | "working" | "researching" | "refining" | "complete";
  agentStatus: string;
  
  // Undo Stack
  undoStack: Array<{
    id: string;
    timestamp: Date;
    label: string;
    state: {
      destinations: DestCard[];
      profile: ProfilePill[];
      responses: AgentResponse[];
    };
  }>;
  
  // Actions
  setDestinations: (updater) => void;
  setExpandedDest: (id: string | null) => void;
  updateProfilePill: (key: string, value: string) => void;
  addResponse: (response: AgentResponse) => void;
  dismissResponse: (id: string) => void;
  setPhase: (phase) => void;
  setAgentStatus: (status: string) => void;
  pushUndo: (label: string) => void;
  performUndo: (undoId: string) => void;
}

interface AgentResponse {
  id: string;
  type: "message" | "insight" | "comparison" | "warning" | "success" | "tip";
  role?: "user" | "agent"; // For message types
  text: string;
  timestamp: Date;
  undoId?: string; // For actions that can be undone
  actions?: Array<{
    id: string;
    label: string;
    description: string;
    icon: string;
  }>;
  dismissed?: boolean;
}
```

**Unified Response System:**

All agent communication goes into the `responses` array:
- User messages: `{ type: "message", role: "user", text: "..." }`
- Agent messages: `{ type: "message", role: "agent", text: "..." }`
- Insights: `{ type: "insight", text: "...", actions: [...] }`
- Warnings: `{ type: "warning", text: "...", actions: [...] }`
- Comparisons: `{ type: "comparison", text: "markdown...", actions: [...] }`
- Success: `{ type: "success", text: "..." }`

**Reserve Destinations:**

Store a pool of reserve destinations that can be added when:
- User dismisses many destinations
- Profile changes make current destinations incompatible
- User asks for "more options"

**Initialization:**

On first command (initial trip request):
1. Parse user input for entities
2. Create profile pills with extracted values
3. Generate 3-4 destination cards based on criteria
4. Set phase to "complete"
5. Add welcome response to responses array

---

## Animation Patterns

**Library:** Framer Motion (`motion/react`)

**Key Principles:**
- Smooth, natural motion
- Stagger animations for groups
- Layout animations for repositioning
- Loading states with skeleton/shimmer

**Common Patterns:**

**Card Entry:**
```jsx
initial={{ opacity: 0, y: 30, scale: 0.95 }}
animate={{ opacity: 1, y: 0, scale: 1 }}
transition={{ type: "spring", damping: 25, stiffness: 200 }}
```

**Card Exit:**
```jsx
exit={{ opacity: 0, scale: 0.9, y: -20 }}
```

**Layout Shift:**
```jsx
<motion.div layout>
```

**Staggered List:**
```jsx
{items.map((item, i) => (
  <motion.div
    key={item.id}
    initial={{ opacity: 0, x: -10 }}
    animate={{ opacity: 1, x: 0 }}
    transition={{ delay: i * 0.1 }}
  />
))}
```

**Progressive Loading (Pinned Card):**
```jsx
// Flight appears with delay
<motion.div
  initial={{ opacity: 0, scale: 0.95 }}
  animate={{ opacity: 1, scale: 1 }}
  transition={{ delay: 0.6 }}
/>
```

---

## Visual Design Tokens

**Colors:**
```css
/* Backgrounds */
--bg-primary: #0a0a14;
--bg-elevated: #0f0f1a;
--bg-card: #1a1a2e;

/* Text */
--text-primary: rgba(255, 255, 255, 0.9);
--text-secondary: rgba(255, 255, 255, 0.6);
--text-tertiary: rgba(255, 255, 255, 0.3);
--text-muted: rgba(255, 255, 255, 0.2);

/* Accents */
--accent-indigo: #6366f1;
--accent-purple: #a855f7;
--accent-amber: #fbbf24;
--accent-emerald: #10b981;

/* Borders */
--border-subtle: rgba(255, 255, 255, 0.06);
--border-medium: rgba(255, 255, 255, 0.08);
--border-strong: rgba(255, 255, 255, 0.12);

/* Vibe Colors */
Culture: bg-purple-500/20 text-purple-300
Food: bg-orange-500/20 text-orange-300
Beach: bg-cyan-500/20 text-cyan-300
Romance: bg-pink-500/20 text-pink-300
Adventure: bg-green-500/20 text-green-300
...etc
```

**Typography:**
- System font stack or Inter
- Headers: 14-20px
- Body: 12-13px
- Small: 10-11px
- Uppercase labels: 9-11px with tracking

**Spacing:**
- Base unit: 4px (0.25rem)
- Cards: 12-16px padding
- Sections: 16-24px gaps
- Grid gaps: 16px

**Border Radius:**
- Small: 8px
- Medium: 12px
- Large: 16px
- XL: 20px

**Glassmorphism:**
```css
background: rgba(255, 255, 255, 0.04);
backdrop-filter: blur(12px);
border: 1px solid rgba(255, 255, 255, 0.08);
```

---

## Interaction Details

### Keyboard Shortcuts
- `/` : Focus command bar
- `Esc` : Close expanded card / Close dropdowns
- `Enter` : Submit command
- `Cmd/Ctrl + Z` : Undo last action

### Loading States
- Skeleton placeholders for cards loading
- Shimmer effect on placeholders
- Spinner in command bar while processing
- Progressive loading for pinned card details

### Error Handling
- Show error insight if command fails
- Graceful degradation for missing data
- Fallback images for broken image URLs

### Empty States
- No destinations: Show prompt to describe trip
- All dismissed: Offer to reset or show reserves
- No messages: Show welcome message

---

## Data Flow Example

**Scenario: User pins Bali**

1. User clicks pin icon on Bali card
2. `setDestinations()` updates `bali.pinned = true`
3. Component re-renders with new state
4. Progressive loading animation triggers:
   - "Itinerary Confirmed" badge fades in
   - Flight placeholder morphs to actual flight (0.6s delay)
   - Hotel placeholder morphs to actual hotel (0.8s delay)
   - Navigation arrows appear
   - Total cost updates to exact amount
5. Agent adds insight: "📌 Bali is now pinned. When you adjust your profile, pinned destinations stay while unpinned ones may swap out."

**Scenario: User changes budget to $2-3k**

1. User clicks budget pill, selects "$2-3k total"
2. `updateProfilePill("budget", "~$2-3k total")`
3. `generateReaction("budget", "~$2-3k total")` returns warnings + insights
4. After 300ms delay:
   - Santorini card gets warning banner: "Exceeds budget"
   - Bali card gets warning: "At the edge at $3.2k pp"
5. After 500ms:
   - Insight appears in Advisor with 3 action buttons
6. User clicks "Find cheaper destinations"
7. `pushUndo("Find cheaper destinations")` saves current state
8. Santorini card dismissed (exit animation)
9. Cartagena + Mexico City cards added (enter animation)
10. Agent message added: "I've made some changes to fit your new budget: • Removed Santorini... • Added Cartagena... You can undo this."

---

## File Structure Recommendations

```
src/
├── app/
│   ├── App.tsx                    // Main two-zone layout orchestration
│   ├── components/
│   │   ├── CommandBar.tsx         // Bottom sticky command input + NLP
│   │   ├── ProfilePanel.tsx       // Left sidebar (profile pills only)
│   │   ├── MainWorkspace.tsx      // Center: destination cards + responses
│   │   │   ├── DestinationCards.tsx  // Top fixed section with grid
│   │   │   └── AgentResponses.tsx    // Bottom scrollable section
│   │   ├── DestCard.tsx           // Card components (Grid/Expanded/Mini)
│   │   └── TripStore.tsx          // State management (Zustand/Context)
│   └── styles/
│       ├── globals.css            // Base styles
│       └── theme.css              // Design tokens
└── ...
```

**Component Hierarchy:**

```
App
├── ProfilePanel (left sidebar)
│   └── Editable Pills
├── MainWorkspace (center)
│   ├── DestinationCards (top, fixed)
│   │   ├── GridCard (collapsed state)
│   │   ├── ExpandedCard (expanded state)
│   │   └── ComparisonMiniCard (sidebar)
│   └── AgentResponses (bottom, scrollable)
│       ├── Message (user/agent)
│       ├── Insight (with actions)
│       ├── Warning
│       ├── Comparison (markdown)
│       └── Success
└── CommandBar (bottom sticky)
```

---

## Implementation Priorities

**Phase 1 (Core MVP):**
1. TripStore with basic state
2. CommandBar with pattern matching
3. ProfilePanel with editable pills
4. DestinationCanvas with GridCard
5. Basic message flow

**Phase 2 (Richness):**
1. ExpandedCard with full details
2. Progressive disclosure (pinned vs unpinned)
3. Insights with action buttons
4. Undo/redo support
5. Comparison sidebar

**Phase 3 (Polish):**
1. Full animation suite
2. Keyboard shortcuts
3. Image galleries
4. Custom vibe input
5. Advanced NLP patterns

---

## Testing Scenarios

1. **Initial Trip Request:** "I want somewhere warm with good food in March for a week under $5k for 2 people"
2. **Budget Change:** Change budget to $2-3k, verify warnings and suggested actions
3. **Pin Destination:** Pin Bali, verify progressive loading of flights/hotels
4. **Comparison:** Pin 2 destinations, type "help me choose", verify detailed analysis
5. **Undo:** Execute an action, click undo, verify state restoration
6. **Custom Vibe:** Enter custom vibe text, verify it updates and triggers re-evaluation
7. **Dismiss & Replace:** Dismiss multiple destinations, verify reserves are added

---

## Notes for Implementation

- **Mock Data:** All flight/hotel/activity data is static for prototype. In production, would come from APIs.
- **NLP Patterns:** Use simple regex/keyword matching. For production, integrate OpenAI/Anthropic API.
- **Undo Stack:** Limit to last 10 actions to prevent memory issues.
- **Images:** Use Unsplash API or local assets for destination images.
- **Responsive:** Prioritize desktop first, then adapt for tablet/mobile.
- **Performance:** Use React.memo for cards, virtualize long lists if needed.
- **Accessibility:** Ensure keyboard navigation, ARIA labels, focus management.

---

## End of Specification

This document provides a complete functional and visual specification for Wanderly v1.1. Use it as a reference for implementation, ensuring consistency across all components and interactions.