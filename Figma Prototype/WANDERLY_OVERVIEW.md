# Wanderly - Travel Planner Prototype Overview

## Core Concept

Wanderly is a **living workspace AI canvas** for trip planning, not a traditional step-by-step wizard. The agent works in parallel on all trip aspects simultaneously, materializing results as cards on a spatial canvas that users steer through natural language and direct manipulation.

---

## Design Philosophy

- **Dark, immersive, visually compact** interface
- **Hybrid AI + direct manipulation** - context lives spatially, not buried in chat
- **Parallel agent execution** - destinations, itineraries, flights, hotels appear simultaneously
- **Spatial interaction model** - users react by expanding, dismissing, pinning cards
- **Natural language steering** - refinements via command bar, not rigid forms

---

## Layout Architecture

### Two-Zone Spatial Layout

1. **Sticky Destination Canvas (Top)**
   - Horizontally scrollable grid of destination cards
   - Persists at top of screen
   - Cards can be expanded to full-screen detail view
   - Pins to show selected destinations across sessions

2. **Unified Agent Responses (Bottom)**
   - Scrollable vertical area
   - Shows all agent responses chronologically
   - Includes itineraries, flights, hotels, activities
   - "Tell me more" knowledge cards appear here

---

## Key Components

### Core State Management
- **`TripStore.tsx`** - Central state store for:
  - Destinations
  - Itineraries
  - Agent responses
  - User commands
  - Mock data generation

### UI Components
- **`CommandBar.tsx`** - Natural language input
  - Handles trip descriptions
  - Processes "tell me more" queries
  - Triggers agent responses

- **`DestinationCard.tsx`** - Compact destination cards
  - Image, name, highlights
  - Click to expand to full-screen
  - Show/hide in canvas

- **`ExpandedDestinationCard.tsx`** - Full-screen destination view
  - Uses full vertical space
  - Rich details, images, highlights
  - Close button to return to canvas

- **`AgentResponse.tsx`** - Chat-style responses
  - Displays itineraries, flights, hotels
  - "Tell me more" knowledge cards
  - Expandable sections

---

## User Flows

### Primary Flow: Trip Creation
1. User types trip description in command bar
   - Example: "Plan a 2-week trip to Japan and Thailand"
2. Agent parses intent, generates parallel responses
3. Destinations appear in top canvas
4. Itineraries, flights, hotels appear in bottom scroll area
5. User refines via natural language or card interactions

### Secondary Flow: Destination Exploration
1. User clicks destination card in canvas
2. Card expands to full-screen view
3. Shows comprehensive destination details
4. User closes to return to canvas

### Tertiary Flow: "Tell Me More"
1. User types "Tell me more about [destination]"
2. Agent generates comprehensive knowledge cards
3. Cards appear in bottom scroll area
4. Cover culture, neighborhoods, food, landmarks, tips
5. Expandable sections for deep dive

---

## Data Model

### Destination
```typescript
{
  id: string
  name: string
  country: string
  imageUrl: string
  highlights: string[]
  description: string
  bestTimeToVisit: string
  coordinates: { lat, lng }
}
```

### Agent Response
```typescript
{
  id: string
  type: 'itinerary' | 'flight' | 'hotel' | 'knowledge'
  timestamp: Date
  content: ResponseContent
}
```

---

## Tech Stack

- **React** + **TypeScript**
- **Tailwind CSS v4**
- **Zustand** (via TripStore)
- **Mock/Static Data** (no real APIs yet)
- **Icon Libraries**: lucide-react, @mui/icons-material, react-icons

---

## Key Differentiators

1. **Spatial context** - Trip elements exist in 2D canvas, not linear chat
2. **Parallel execution** - All trip aspects load simultaneously
3. **Natural steering** - Conversational refinement, not forms
4. **Hybrid interaction** - Both AI-generated and user-manipulated
5. **Living workspace** - Canvas evolves with user intent

---

## Current Implementation Status

✅ Two-zone layout (sticky canvas + scrollable responses)  
✅ Mock destination generation  
✅ Destination card expand/collapse  
✅ Command bar with natural language parsing  
✅ "Tell me more" knowledge cards  
✅ Expandable response sections  
✅ Full vertical space for expanded cards  
✅ Comprehensive icon library support  

🔄 Next: Transfer to Cursor for production development

---

## Development Notes

- Uses `figma:asset` for imported images (virtual module scheme)
- All components in `src/app/components/`
- Main app in `src/app/App.tsx`
- Styles in `src/styles/theme.css` and `src/styles/fonts.css`
- No standalone HTML files - React-only architecture
