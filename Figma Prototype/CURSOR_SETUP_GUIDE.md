# Transfer Wanderly Prototype to Cursor - Setup Guide

## Step 1: Download the Project

I've created a compressed archive: **`wanderly-prototype.tar.gz`** (284KB)

**Download it from this workspace:**
- If you're in Claude Code web app, download the file from the file explorer
- If you're in Claude Code desktop/CLI, the file is at: `/workspaces/default/code/wanderly-prototype.tar.gz`

## Step 2: Extract Locally

```bash
# Navigate to where you want the project
cd ~/Projects  # or wherever you keep your code

# Extract the archive
tar -xzf wanderly-prototype.tar.gz -C wanderly

# Or if you're on Windows, use a tool like 7-Zip or WinRAR
```

## Step 3: Open in Cursor

1. Open Cursor
2. File → Open Folder
3. Select the extracted `wanderly` folder
4. Cursor will index the project

## Step 4: Point Cursor to Documentation

Once opened, add these files to Cursor's context using **⌘/Ctrl + Shift + @**:

### Primary Documentation (in order):
1. **`WANDERLY_OVERVIEW.md`** - High-level concept and architecture
2. **`WANDERLY_DETAILED_GUIDE.md`** - Detailed interaction flows and implementation specs
3. **`WANDERLY_UI_SPEC.md`** - UI/UX specifications (if you created this earlier)

### Supporting Documentation:
- **`IMPLEMENTATION_GUIDE.md`** - Original implementation notes
- **`QUICK_REFERENCE.md`** - Quick reference for key patterns

### You can also tell Cursor:
```
I'm working on Wanderly, a travel planner prototype. Read WANDERLY_OVERVIEW.md 
and WANDERLY_DETAILED_GUIDE.md to understand the project architecture and 
interaction model. This is a living workspace / AI canvas interface, not a 
traditional step-by-step wizard. The core components are in src/app/components/ 
and state management is in TripStore.tsx.
```

## Step 5: Install Dependencies

```bash
# Install packages
pnpm install

# Or if you don't have pnpm
npm install -g pnpm
pnpm install
```

## Step 6: Start Development

```bash
# Start the dev server
pnpm dev

# Or
npm run dev
```

The app will be available at `http://localhost:5173`

## Key Files for Cursor to Reference

### Core Application:
- **`src/app/App.tsx`** - Main app component with two-zone layout
- **`src/app/components/TripStore.tsx`** - Zustand state management
- **`src/app/components/CommandBar.tsx`** - Natural language input
- **`src/app/components/DestinationCanvas.tsx`** - Sticky destination cards
- **`src/app/components/AgentResponses.tsx`** - Chat-style responses

### Styling:
- **`src/styles/theme.css`** - Dark theme tokens
- **`src/styles/fonts.css`** - Font imports

### UI Components:
- All in `src/app/components/ui/` (shadcn-based)

## What's Included

✅ Complete React + TypeScript setup  
✅ Tailwind CSS v4 dark theme  
✅ Zustand state management  
✅ Mock data for destinations, itineraries, flights, hotels  
✅ "Tell me more" knowledge card system  
✅ Expandable destination cards  
✅ Icon libraries (lucide-react, @mui/icons-material, react-icons)  
✅ Comprehensive documentation  

## What's NOT Included (Next Steps)

❌ Real APIs (Google Places, Amadeus, Booking.com)  
❌ Backend / Supabase integration  
❌ User authentication  
❌ Trip persistence  
❌ Real-time collaboration  

## Tips for Working with Cursor

1. **Use Cursor Composer** for complex refactoring across multiple files
2. **Add documentation to chat context** with @ mentions
3. **Reference specific components** when asking questions
4. **Use Cursor's inline edits** for quick changes
5. **Create a `.cursorrules` file** with project-specific instructions

### Suggested `.cursorrules` content:

```
This is Wanderly, a travel planner with a spatial canvas interface.

Architecture:
- Two-zone layout: sticky destination canvas (top) + scrollable agent responses (bottom)
- Zustand for state management (TripStore.tsx)
- All data is currently mock/static
- React + TypeScript + Tailwind v4

Key Principles:
- Spatial context over linear chat
- Parallel AI execution, not sequential steps
- Dark, immersive, visually compact UI
- Hybrid AI + direct manipulation interaction

When making changes:
- Keep the two-zone layout intact
- Use TripStore for all state updates
- Follow the dark theme in theme.css
- Don't add external API calls yet (mock data only)
- Maintain the living workspace paradigm

Documentation:
- Read WANDERLY_OVERVIEW.md for high-level architecture
- Read WANDERLY_DETAILED_GUIDE.md for detailed specs
```

## Next Development Steps

Once you're in Cursor, you can:

1. **Enhance the mock data** - Add more destinations, richer content
2. **Improve animations** - Polish transitions and micro-interactions
3. **Add features** - Drag-drop, pinning, filters
4. **Prepare for APIs** - Abstract data layer, create API interfaces
5. **Add Supabase** - Set up backend, authentication, persistence

## Troubleshooting

**If Cursor doesn't recognize the project structure:**
- Make sure you opened the root folder (where package.json is)
- Restart Cursor's TypeScript server: Cmd+Shift+P → "Restart TS Server"

**If imports are broken:**
- Run `pnpm install` to ensure all dependencies are installed
- Check that node_modules exists

**If styles look wrong:**
- Make sure Tailwind CSS IntelliSense extension is installed
- Check that theme.css is being imported in App.tsx

---

You're all set! Open the project in Cursor and reference the documentation files to continue building Wanderly.
