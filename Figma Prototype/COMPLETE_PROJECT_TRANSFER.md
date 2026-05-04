# Wanderly - Complete Project Transfer Guide

**Copy this entire file and use it to recreate the project in Cursor**

---

## Step 1: Create New React + TypeScript Project

```bash
# Create new project
npm create vite@latest wanderly -- --template react-ts
cd wanderly

# Install dependencies
pnpm install

# Install all required packages
pnpm add @emotion/react@11.14.0 @emotion/styled@11.14.1 @mui/icons-material@7.3.5 @mui/material@7.3.5 lucide-react@0.487.0 react-icons@5.6.0 zustand clsx tailwind-merge motion@12.23.24

# Install Tailwind CSS v4
pnpm add -D tailwindcss@4.1.12 @tailwindcss/vite@4.1.12

# Install Radix UI components (for shadcn-style components)
pnpm add @radix-ui/react-accordion@1.2.3 @radix-ui/react-dialog@1.1.6 @radix-ui/react-slot@1.1.2
```

---

## Step 2: Project Structure

Create this folder structure:

```
wanderly/
├── src/
│   ├── app/
│   │   ├── App.tsx
│   │   └── components/
│   │       ├── TripStore.tsx
│   │       ├── CommandBar.tsx
│   │       ├── DestinationCanvas.tsx
│   │       ├── AgentResponses.tsx
│   │       ├── MainWorkspace.tsx
│   │       └── ui/
│   │           ├── button.tsx
│   │           └── card.tsx
│   ├── styles/
│   │   ├── index.css
│   │   ├── theme.css
│   │   └── fonts.css
│   └── main.tsx
├── package.json
├── vite.config.ts
├── tsconfig.json
└── DOCUMENTATION/
    ├── WANDERLY_OVERVIEW.md
    └── WANDERLY_DETAILED_GUIDE.md
```

---

## Step 3: Core Configuration Files

### `vite.config.ts`
```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': '/src'
    }
  }
})
```

### `tsconfig.json`
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

---

## Step 4: Documentation Files

### Create `DOCUMENTATION/WANDERLY_OVERVIEW.md`

**Copy the entire content from the file I created earlier** (`WANDERLY_OVERVIEW.md` in this workspace). It contains:
- Core concept
- Design philosophy
- Layout architecture
- Key components
- Data model
- Tech stack

### Create `DOCUMENTATION/WANDERLY_DETAILED_GUIDE.md`

**Copy the entire content from** (`WANDERLY_DETAILED_GUIDE.md` in this workspace). It contains:
- Detailed user flows
- Component specifications
- State management patterns
- UI/UX patterns
- Command parsing logic
- Knowledge card system

---

## Step 5: Cursor Configuration

### Create `.cursorrules` in project root

```
This is Wanderly - a travel planner with a spatial canvas interface (NOT a traditional wizard).

ARCHITECTURE:
- Two-zone layout: sticky destination canvas (top) + scrollable agent responses (bottom)
- Zustand for state management (src/app/components/TripStore.tsx)
- All data is currently mock/static (no real APIs)
- React + TypeScript + Tailwind v4
- Icons: lucide-react, @mui/icons-material, react-icons

CORE PRINCIPLES:
- Spatial context over linear chat
- Parallel AI execution (destinations, flights, hotels load simultaneously)
- Dark, immersive, visually compact UI
- Hybrid AI + direct manipulation interaction
- Living workspace paradigm

WHEN MAKING CHANGES:
- Keep the two-zone layout intact
- Use TripStore for all state updates
- Follow dark theme tokens in src/styles/theme.css
- Don't add external API calls yet (mock data only)
- Maintain spatial canvas concept

DOCUMENTATION:
- Read DOCUMENTATION/WANDERLY_OVERVIEW.md for architecture
- Read DOCUMENTATION/WANDERLY_DETAILED_GUIDE.md for detailed specs
- These files explain the complete interaction model

COMPONENTS:
- TripStore.tsx = central state (Zustand)
- CommandBar.tsx = natural language input
- DestinationCanvas.tsx = sticky card grid (top zone)
- AgentResponses.tsx = scrollable responses (bottom zone)
- MainWorkspace.tsx = orchestrates the two-zone layout
```

---

## Step 6: Tell Cursor

Once you have the files set up, open Cursor and say:

```
I'm building Wanderly, a travel planner prototype with a spatial canvas interface.

Please read:
1. DOCUMENTATION/WANDERLY_OVERVIEW.md - for the core concept and architecture
2. DOCUMENTATION/WANDERLY_DETAILED_GUIDE.md - for detailed interaction flows

This is NOT a traditional step-by-step wizard. It's a living workspace where:
- Destinations appear in a sticky canvas at the top
- Agent responses (itineraries, flights, hotels) appear in a scrollable feed below
- Users steer with natural language via a command bar
- Everything works in parallel, not sequentially

The prototype currently uses mock data. All state is in TripStore.tsx using Zustand.

I need help implementing [your specific task].
```

---

## Step 7: Key Implementation Points for Cursor

### State Management Pattern
```typescript
// TripStore.tsx uses Zustand
import { create } from 'zustand'

interface Destination {
  id: string
  name: string
  country: string
  imageUrl: string
  highlights: string[]
  description: string
}

interface TripStore {
  destinations: Destination[]
  agentResponses: AgentResponse[]
  processCommand: (cmd: string) => void
  // ... other methods
}

const useTripStore = create<TripStore>((set) => ({
  // implementation
}))
```

### Layout Pattern
```typescript
// Two-zone layout
<div className="h-screen flex flex-col">
  {/* Zone 1: Sticky Destination Canvas */}
  <div className="sticky top-0 z-10">
    <DestinationCanvas />
  </div>
  
  {/* Zone 2: Scrollable Agent Responses */}
  <div className="flex-1 overflow-y-auto">
    <AgentResponses />
  </div>
</div>
```

### Command Processing Pattern
```typescript
processCommand: (input: string) => {
  const lower = input.toLowerCase()
  
  if (lower.includes('tell me more')) {
    // Generate knowledge cards
    const dest = extractDestination(input)
    generateKnowledgeCards(dest)
  } else if (lower.includes('plan') || lower.includes('trip')) {
    // Generate trip
    generateDestinations(input)
    generateItineraries()
    generateFlights()
  }
}
```

---

## Step 8: What You'll Get

After following this guide, you'll have:

✅ Complete React + TypeScript + Tailwind v4 setup  
✅ Two-zone spatial layout (sticky canvas + scrollable responses)  
✅ Mock destination generation system  
✅ Command bar with natural language parsing  
✅ "Tell me more" knowledge cards  
✅ Expandable destination cards  
✅ Icon libraries ready to use  
✅ Dark theme system  
✅ Comprehensive documentation for Cursor  

---

## Next Steps in Cursor

1. **Implement remaining UI polish** - animations, transitions, micro-interactions
2. **Add more mock data** - richer destinations, varied itineraries
3. **Prepare API layer** - abstract data fetching for future real APIs
4. **Add Supabase** - backend, auth, persistence
5. **Enhance interactions** - drag-drop, pinning, filters, multi-trip

---

## Important Files to Reference

When working in Cursor, keep these files handy:

1. **DOCUMENTATION/WANDERLY_OVERVIEW.md** - High-level concept
2. **DOCUMENTATION/WANDERLY_DETAILED_GUIDE.md** - Implementation specs
3. **src/app/components/TripStore.tsx** - Central state
4. **src/app/App.tsx** - Main app entry
5. **src/styles/theme.css** - Dark theme tokens

---

## Troubleshooting

**TypeScript errors?**
- Make sure all dependencies are installed
- Restart Cursor's TS server: Cmd+Shift+P → "Restart TS Server"

**Styles not working?**
- Verify Tailwind is imported in src/styles/index.css
- Check that @tailwindcss/vite plugin is in vite.config.ts

**Can't find documentation?**
- Make sure you copied WANDERLY_OVERVIEW.md and WANDERLY_DETAILED_GUIDE.md into the DOCUMENTATION/ folder
- Use @ mentions in Cursor to add them to context

---

## Contact Points for Help

If Cursor gets stuck or doesn't understand the architecture:

**Remind it:**
- "This is a spatial canvas interface, not a linear wizard"
- "Reference WANDERLY_DETAILED_GUIDE.md for the interaction model"
- "All state goes through TripStore.tsx"
- "Keep the two-zone layout intact"

---

You're all set! Copy this file, follow the steps, and Cursor will have everything it needs to continue building Wanderly.
