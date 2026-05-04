# Wanderly - Detailed Interaction & Implementation Guide

## Table of Contents
1. [Detailed User Flows](#detailed-user-flows)
2. [Component Specifications](#component-specifications)
3. [State Management](#state-management)
4. [UI/UX Patterns](#uiux-patterns)
5. [Command Parsing Logic](#command-parsing-logic)
6. [Knowledge Card System](#knowledge-card-system)

---

## Detailed User Flows

### Flow 1: Initial Trip Planning

**Step-by-Step Interaction:**

1. **User arrives at blank canvas**
   - Dark interface with centered command bar
   - Placeholder: "Describe your dream trip..."
   - Empty destination canvas (sticky, top)
   - Empty agent responses area (scrollable, bottom)

2. **User types trip description**
   - Example: "Plan a 2-week adventure in Southeast Asia, focusing on beaches and culture"
   - Command bar captures input
   - User presses Enter or clicks submit

3. **Agent processing begins**
   - Command appears as user message in agent responses
   - Loading indicator appears
   - Store generates mock destinations based on keywords

4. **Destinations materialize**
   - Destination cards fade into top canvas (horizontal scroll)
   - Example destinations: Bali, Phuket, Hoi An, Siem Reap
   - Each card shows:
     - Hero image
     - Destination name + country
     - 3-4 key highlights as bullet points
   - Cards appear with stagger animation (left to right)

5. **Agent responses populate**
   - Itinerary card appears first
   - Flight options card appears second
   - Hotel recommendations appear third
   - All with slight delay between each

6. **User reviews and refines**
   - Scrolls through agent responses
   - Clicks destination card to expand
   - Types refinement: "Actually, let's focus on Indonesia only"

7. **Agent updates**
   - New destinations replace old ones in canvas
   - New itineraries/flights appear in responses
   - Old responses remain in chat history (scrolled up)

---

### Flow 2: Destination Card Expansion

**Step-by-Step Interaction:**

1. **User clicks destination card**
   - Card in sticky canvas (e.g., "Bali, Indonesia")
   - Entire card is clickable area

2. **Expansion animation**
   - Card transitions to full-screen overlay
   - Dark backdrop appears behind
   - Card expands from its position to fill viewport

3. **Expanded view displays**
   - **Header section:**
     - Large hero image (top)
     - Destination name + country
     - Close button (top-right corner, X icon)
   
   - **Content sections (scrollable):**
     - **Overview**: 2-3 paragraph description
     - **Best Time to Visit**: Month-by-month breakdown
     - **Top Highlights**: 5-8 key attractions with icons
     - **Cultural Notes**: Local customs, language tips
     - **Practical Info**: Currency, voltage, safety tips
   
   - **Full vertical space usage:**
     - Uses `h-screen` to fill viewport height
     - Content scrolls within expanded card
     - Fixed header with close button

4. **User closes card**
   - Clicks X button or presses Escape
   - Card collapses back to original position
   - Returns to canvas view

---

### Flow 3: "Tell Me More" Knowledge Request

**Step-by-Step Interaction:**

1. **User types knowledge query**
   - In command bar: "Tell me more about Bali"
   - Query focuses on single destination
   - Can be typed anytime after destinations load

2. **Agent parses intent**
   - Identifies "tell me more" pattern
   - Extracts destination name ("Bali")
   - Checks if destination exists in trip

3. **Knowledge cards generate**
   - Agent creates 5 knowledge cards:
     1. **Culture & History**
     2. **Neighborhoods & Areas**
     3. **Food Scene & Dining**
     4. **Landmarks & Attractions**
     5. **Practical Travel Tips**

4. **Cards appear in agent responses**
   - New agent response with type: 'knowledge'
   - All 5 cards appear together in one response
   - Each card is expandable/collapsible

5. **User explores knowledge**
   - Clicks card header to expand
   - Reads detailed content:
     - **Culture**: Temples, ceremonies, Balinese Hinduism
     - **Neighborhoods**: Ubud (culture), Seminyak (beach), Canggu (surfer)
     - **Food**: Nasi goreng, satay, warungs vs fine dining
     - **Landmarks**: Tanah Lot, rice terraces, waterfalls
     - **Tips**: Visa requirements, transportation, scams to avoid
   
6. **Collapsible interaction**
   - Each card has expand/collapse state
   - Click header again to collapse
   - Multiple cards can be expanded simultaneously
   - Icons change: ChevronDown → ChevronUp

---

## Component Specifications

### CommandBar Component

**Purpose:** Natural language input for trip planning and queries

**Props:**
- None (uses TripStore directly)

**State:**
- `input: string` - Current command text
- `isProcessing: boolean` - Loading state

**Behavior:**
1. **Input handling:**
   - Controlled input field
   - Updates local state on change
   - Submits on Enter key or button click

2. **Command processing:**
   - Calls `TripStore.processCommand(input)`
   - Clears input field
   - Sets loading state during processing

3. **Command types:**
   - **Trip planning**: General trip descriptions
     - "Plan a trip to..."
     - "I want to visit..."
   - **Tell me more**: Destination knowledge
     - "Tell me more about [destination]"
     - "What should I know about [destination]"
   - **Refinements**: Trip modifications
     - "Focus on beaches only"
     - "Remove Japan from the trip"

**Styling:**
- Dark background with subtle border
- Centered when empty, sticky when has content
- Rounded corners, shadow on focus
- Icon button for submit (Send icon)

---

### DestinationCard Component

**Purpose:** Compact destination card in sticky canvas

**Props:**
```typescript
interface DestinationCardProps {
  destination: Destination
  onClick: () => void
}
```

**Layout:**
- Width: 300px
- Height: 200px
- Image: Full width, 60% of height
- Content: Name, country, 3 highlights
- Hover: Slight scale transform, shadow increase

**Interaction:**
- Entire card is clickable
- Hover state shows interactivity
- Click triggers parent-controlled expansion

**Styling:**
- Rounded corners
- Dark card background
- Image with gradient overlay for text readability
- Compact text with ellipsis overflow

---

### ExpandedDestinationCard Component

**Purpose:** Full-screen destination detail view

**Props:**
```typescript
interface ExpandedDestinationCardProps {
  destination: Destination
  onClose: () => void
}
```

**Layout Structure:**
```
┌─────────────────────────────────┐
│ Hero Image + Gradient Overlay   │ 
│ ┌─────────────────────────────┐ │
│ │ Destination Name            X││ ← Fixed header
│ └─────────────────────────────┘ │
├─────────────────────────────────┤
│                                 │
│  Scrollable Content:            │
│  - Overview section             │
│  - Best time to visit           │
│  - Highlights grid              │
│  - Cultural notes               │
│  - Practical info               │
│                                 │
│  (Full vertical scroll)         │
│                                 │
└─────────────────────────────────┘
```

**Key Features:**
- **Full viewport height:** `h-screen`
- **Scrollable content:** `overflow-y-auto`
- **Fixed close button:** Top-right, always visible
- **Dark backdrop:** Dims canvas behind
- **Escape key support:** Closes on Esc press

**Content Sections:**

1. **Overview**
   - 2-3 paragraphs
   - Typography: Readable line height, medium font

2. **Best Time to Visit**
   - Calendar icon + seasonal breakdown
   - High season, low season, monsoon info

3. **Highlights Grid**
   - 2-column grid on desktop
   - Each highlight: Icon + title + description
   - Icons from lucide-react (MapPin, Palmtree, etc.)

4. **Cultural Notes**
   - Customs, etiquette, language basics
   - Important dos and don'ts

5. **Practical Info**
   - Visa requirements
   - Currency, ATMs
   - Transportation options
   - Safety tips

---

### AgentResponse Component

**Purpose:** Display agent-generated responses in chat-style format

**Props:**
```typescript
interface AgentResponseProps {
  response: AgentResponse
}

type AgentResponse = {
  id: string
  type: 'itinerary' | 'flight' | 'hotel' | 'knowledge'
  timestamp: Date
  content: any
}
```

**Rendering by Type:**

1. **Itinerary Response**
   - Day-by-day breakdown
   - Each day: Date, activities, meals
   - Expandable days

2. **Flight Response**
   - Flight options (mock data)
   - Airline, times, price, duration
   - Card-based layout

3. **Hotel Response**
   - Hotel recommendations
   - Name, rating, price, location
   - Image thumbnails

4. **Knowledge Response**
   - 5 expandable knowledge cards
   - Each card: Title, icon, content
   - Collapse/expand interaction

**Styling:**
- Chat bubble style
- Dark background with border
- Timestamp at bottom
- Spacing between responses

---

## State Management

### TripStore (Zustand)

**Store Structure:**
```typescript
interface TripState {
  // Destinations
  destinations: Destination[]
  addDestination: (dest: Destination) => void
  removeDestination: (id: string) => void
  
  // Agent responses
  agentResponses: AgentResponse[]
  addAgentResponse: (response: AgentResponse) => void
  
  // Commands
  userCommands: string[]
  processCommand: (command: string) => void
  
  // UI state
  expandedDestination: string | null
  setExpandedDestination: (id: string | null) => void
}
```

**Key Methods:**

1. **`processCommand(command: string)`**
   - Parses user input
   - Determines intent (trip plan vs tell me more)
   - Generates appropriate responses
   - Updates store state

2. **`generateMockDestinations(query: string)`**
   - Extracts keywords from query
   - Returns relevant mock destinations
   - Example: "Southeast Asia" → Bali, Phuket, etc.

3. **`generateKnowledgeCards(destinationName: string)`**
   - Creates 5 knowledge cards
   - Populates with mock content
   - Returns as single agent response

**Mock Data:**
- Predefined destinations (10-15 popular locations)
- Templated itineraries
- Mock flight/hotel data
- Knowledge card templates

---

## UI/UX Patterns

### Spatial Canvas Interaction

**Sticky Destination Canvas:**
- Always visible at top
- Horizontal scroll only
- Fixed height (~250px)
- Cards add left-to-right
- Scroll snaps to cards on mobile

**Agent Responses Area:**
- Fills remaining vertical space
- Vertical scroll only
- Responses append bottom-up
- Auto-scroll to latest response
- Older responses scroll up (preserved in history)

### Dark Theme System

**Color Palette:**
- Background: Deep navy/charcoal (`#0a0e1a` or similar)
- Cards: Slightly lighter (`#1a1f2e`)
- Text: Light gray (`#e5e7eb`)
- Accents: Teal/cyan for interactive elements
- Borders: Subtle gray (`#2a2f3e`)

**Visual Hierarchy:**
- Large hero images for destinations
- Medium weight for headings
- Regular weight for body text
- Icons for visual anchors

### Interaction Feedback

**Hover States:**
- Card scale: 1.02x
- Shadow increase
- Border brightness increase

**Click States:**
- Slight scale down (0.98x) on press
- Immediate response on release

**Loading States:**
- Skeleton cards while generating
- Spinner in command bar
- Progressive loading of responses

### Responsive Behavior

**Desktop (>1024px):**
- Destination canvas: 4-5 cards visible
- Two-column knowledge cards
- Wide expanded destination view

**Tablet (768-1024px):**
- Destination canvas: 2-3 cards visible
- Single column knowledge cards
- Full-width expanded view

**Mobile (<768px):**
- Destination canvas: 1 card visible, swipeable
- Stacked knowledge cards
- Full-screen expanded view

---

## Command Parsing Logic

### Intent Classification

**Pattern Matching:**
```typescript
function parseCommand(input: string): Intent {
  const lower = input.toLowerCase()
  
  // Tell me more pattern
  if (lower.includes('tell me more') || 
      lower.includes('what about') ||
      lower.includes('info about')) {
    return {
      type: 'knowledge',
      destination: extractDestination(input)
    }
  }
  
  // Trip planning pattern
  if (lower.includes('plan') || 
      lower.includes('trip') ||
      lower.includes('visit') ||
      lower.includes('travel to')) {
    return {
      type: 'trip_plan',
      query: input
    }
  }
  
  // Refinement pattern
  if (lower.includes('focus on') ||
      lower.includes('remove') ||
      lower.includes('add') ||
      lower.includes('change')) {
    return {
      type: 'refinement',
      modification: input
    }
  }
  
  // Default: treat as trip plan
  return {
    type: 'trip_plan',
    query: input
  }
}
```

### Destination Extraction

**From "Tell Me More" Queries:**
```typescript
function extractDestination(query: string): string {
  // Pattern: "tell me more about [destination]"
  const patterns = [
    /tell me (?:more )?about (.+)/i,
    /what (?:about|should I know about) (.+)/i,
    /info (?:on|about) (.+)/i
  ]
  
  for (const pattern of patterns) {
    const match = query.match(pattern)
    if (match) {
      return match[1].trim()
    }
  }
  
  return ''
}
```

### Keyword Extraction for Destinations

**Geographic Keywords:**
- Continents: "Southeast Asia", "Europe", "South America"
- Regions: "Mediterranean", "Caribbean", "Scandinavia"
- Countries: "Japan", "Italy", "Thailand"
- Cities: "Tokyo", "Paris", "Bali"

**Interest Keywords:**
- "beaches" → coastal destinations
- "culture" → historic cities
- "adventure" → nature destinations
- "food" → culinary hotspots

---

## Knowledge Card System

### Card Structure

Each knowledge card follows this pattern:

```typescript
interface KnowledgeCard {
  id: string
  title: string
  icon: IconType
  content: {
    intro: string
    sections: KnowledgeSection[]
  }
  expanded: boolean
}

interface KnowledgeSection {
  heading?: string
  body: string
  bullets?: string[]
}
```

### Card Types & Content

**1. Culture & History**
- Religious background
- Historical significance
- Traditional practices
- Festivals and celebrations
- Local etiquette

**2. Neighborhoods & Areas**
- 4-6 key neighborhoods
- Each: Name, vibe, best for
- Transportation between areas
- Where to stay recommendations

**3. Food Scene & Dining**
- Signature dishes
- Types of restaurants (street food to fine dining)
- Must-try foods
- Dietary considerations
- Price ranges

**4. Landmarks & Attractions**
- Top 8-10 sights
- Each: Name, why visit, location
- Ticketing info
- Best times to visit
- Photography tips

**5. Practical Travel Tips**
- Visa requirements
- Currency and payments
- Transportation options
- Safety considerations
- Common scams to avoid
- Language basics
- Tipping culture

### Expandable Card Interaction

**Default State:**
- Title + icon visible
- Content collapsed
- ChevronDown icon on right
- Hover: slight background change

**Expanded State:**
- Full content visible
- Smooth height transition
- ChevronUp icon rotates
- Content fades in

**Implementation:**
```typescript
const [expandedCards, setExpandedCards] = useState<Set<string>>(new Set())

const toggleCard = (cardId: string) => {
  setExpandedCards(prev => {
    const next = new Set(prev)
    if (next.has(cardId)) {
      next.delete(cardId)
    } else {
      next.add(cardId)
    }
    return next
  })
}
```

---

## Animation & Transitions

### Destination Card Appear
- Fade in + slide from left
- Stagger: 100ms delay between cards
- Duration: 300ms
- Easing: ease-out

### Destination Card Expansion
- Transform from card position to full-screen
- Duration: 400ms
- Backdrop fade in: 200ms
- Easing: ease-in-out

### Agent Response Appear
- Fade in from bottom
- Duration: 250ms
- Easing: ease-out

### Knowledge Card Expand/Collapse
- Height transition: auto
- Duration: 300ms
- Content fade: 200ms
- Icon rotate: 300ms

---

## File Structure

```
src/
├── app/
│   ├── App.tsx                 # Main app component
│   ├── components/
│   │   ├── CommandBar.tsx      # Natural language input
│   │   ├── DestinationCard.tsx # Compact card
│   │   ├── ExpandedDestinationCard.tsx # Full-screen view
│   │   ├── AgentResponse.tsx   # Chat-style responses
│   │   └── KnowledgeCard.tsx   # Expandable knowledge cards
│   └── store/
│       └── TripStore.tsx       # Zustand state management
├── styles/
│   ├── theme.css               # Tailwind tokens, dark theme
│   └── fonts.css               # Font imports
└── imports/                    # Figma assets (if any)
```

---

## Next Steps for Production

1. **Replace mock data with real APIs:**
   - Google Places API for destinations
   - Amadeus/Skyscanner for flights
   - Booking.com API for hotels
   - OpenAI/Claude for knowledge generation

2. **Add persistence:**
   - Supabase for user trips
   - Save/load trip state
   - User authentication

3. **Enhanced interactions:**
   - Drag-and-drop card reordering
   - Pin/unpin destinations
   - Multi-trip management
   - Share trip links

4. **Real-time collaboration:**
   - Multiple users on same trip
   - Live updates
   - Comments on destinations

5. **Mobile optimization:**
   - Touch gestures
   - Bottom sheet for expanded cards
   - Swipe between destinations

---

## Key Implementation Notes

- **No real APIs yet** - all data is mock/static
- **Icons**: Use lucide-react, @mui/icons-material, or react-icons
- **Images**: Use `figma:asset` scheme for imported images, or ImageWithFallback component for new images
- **Routing**: Single-page app, no react-router yet
- **State**: Zustand store handles all state
- **Styling**: Tailwind v4, dark theme in theme.css
- **Type safety**: Full TypeScript throughout

---

This prototype demonstrates the core interaction model and spatial design system. The production version will add real data, persistence, and collaboration features while maintaining this hybrid AI + direct manipulation paradigm.
