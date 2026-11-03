# Chat page design

A redesign of the chat page (`frontend/`, host `PageRoot`, also used by the desk panel). Open
[`chat-page-mockup.html`](chat-page-mockup.html) in a browser for the visual reference (light and dark, `◐` toggles).

## What is wrong today (from the running app)
- **Sidebar:** the Agents and Sessions headers overlap; session titles are raw prompts (`[[search: ...]]`); each row is a
  tall bordered card, so only ~8 sessions fit; the agent list and the sessions list compete for space.
- **Header:** a large agent card, a status chip and three text buttons crowd one row; the page title area repeats a
  second "New chat" card inside the transcript.
- **Messages:** the user message is a solid brand bubble and the answer a bordered card of the same weight, so nothing
  reads as the "voice" of the conversation; the feedback buttons are heavy and always visible; tool use is invisible
  in the flow.
- **Composer:** the text box, the agent chip, attach, mic and Send are scattered across two rows with no shared
  container; Send is far from the text.
- **No clear states** for streaming, waiting for approval, speaking or listening.

## Layout
Three zones; the right one is optional.

| Zone | Width | Content |
|---|---|---|
| Left rail | 272 px, collapsible | Brand, **New chat** (Ctrl K), agent switcher (one dropdown), search, sessions grouped Today / Yesterday / Previous 7 days / Older, user and settings footer |
| Conversation | fluid, content column max 760 px, centred | Slim top bar, transcript, composer docked at the bottom |
| Activity drawer | 320 px, toggled | Run, Tools and Memory tabs |

Breakpoints: under 1180 px the drawer becomes an overlay; under 760 px the rail becomes a slide-over and the content
column uses 14 px gutters.

## Components

**Left rail**
- Agent switcher is one dropdown showing agent name, model and readiness. It replaces the agent list.
- Session row: one line, ellipsis, 32 px tall. Active row has a brand left edge. A dot marks "waiting for approval".
  Hover shows rename, pin, delete. Titles are generated from the first message (short), never the raw prompt.
- Search filters the list as you type and also matches message text.

**Top bar (52 px)**
Editable session title, a status pill (Running / Completed / Waiting for approval / Failed), then on the right the
agent-and-model chip, Activity toggle, full-screen, theme. No other text buttons.

**Transcript**
- No "New chat" card. A new chat shows the empty state instead.
- User message: right-aligned, soft brand tint, max 78 % width.
- Assistant message: no card; a 28 px avatar and plain prose on the page background, so long answers read like a
  document. Markdown gets real styling: headings, lists, tables (scrollable), code blocks with copy, inline citation
  chips that open the source in the drawer.
- **Tool steps** sit above the answer in one collapsible group ("Searched knowledge · 2 sources · 1.4 s"), expanded
  while running, collapsed after. Each step shows name, key argument, duration.
- **Message actions** (copy, helpful, needs work, retry, read aloud) are quiet icon buttons that appear on hover or
  focus (always visible on touch).
- **Streaming:** text appears live (the service now streams), with a soft caret; Stop replaces Send.
- **Approval card:** the most prominent element in the transcript. Warning tint and border, plain-language summary
  ("cancel invoice SINV-0042 and create a credit note"), the exact tool calls in a monospace block, then
  **Approve**, **Deny**, **Suggest a change…**. Approval is click-only, including in voice mode. While pending, the
  composer says "Waiting for your approval" and the rail shows a dot.
- Errors: inline, red-tinted, with the normalized provider message and Retry.

**Composer**
One rounded container: auto-growing text area on top; below it attach, mic, knowledge chip on the left and a single
Send button on the right. Focus ring in brand colour. Helper text under it (Enter, Shift+Enter, AI disclaimer).
Attachments appear as chips inside the container above the text. States:
- Listening: mic turns brand-filled with a live waveform and a "Listening… click to send" label.
- Speaking: a small pill above the composer with a waveform and Stop.
- Running: Send becomes Stop; text area stays editable.
- Disabled by approval: placeholder explains why.

**Empty state**
Centred greeting with the agent name, one line describing the agent, and 4 suggestion cards from the agent's starter
prompts. The composer sits mid-page until the first message, then docks to the bottom.

**Activity drawer**
Tabs: Run (tokens, cost, duration, model calls, timeline), Tools (calls with arguments and results, copyable),
Memory (what was read or saved). Opening a citation or a tool step scrolls to it here.

## Visual system
- **Colour tokens** (CSS variables, both themes): `--bg`, `--surface`, `--sunken`, `--line`, `--text`, `--muted`,
  `--faint`, `--brand` (teal, kept from today), `--brand-soft`, `--warn`, `--ok`, `--danger`. Contrast at least 4.5:1
  for text in both themes.
- **Type:** Inter (system fallback), 14 px body, 1.55 line height; headings 14-16 px semibold; monospace for tool
  names and code. No text smaller than 12 px.
- **Shape and depth:** 8-10 px controls, 14-18 px containers, 1 px borders, one soft shadow only on the composer.
- **Spacing:** 4 px base; transcript gap 22 px; column gutter 24 px (14 px on phones).
- **Motion:** 120-200 ms ease for hover, drawer and collapse; waveform and caret only while active; respect
  `prefers-reduced-motion`.
- **Icons:** one consistent outline set (not emoji; the mockup uses text glyphs as placeholders).

## Accessibility
Keyboard: Ctrl K new chat, `/` focus search, Esc stops speech or closes the drawer, Enter and Shift+Enter in the
composer. Visible focus rings; every icon button has a label and tooltip; transcript is an `aria-live="polite"` log;
the approval card receives focus when it appears; colour is never the only status signal.

## Implementation plan
Work lands as small commits on a branch, each leaving the app working.
1. **Tokens and base:** introduce the CSS variables, light and dark themes, and type scale in `styles/` (replace
   hard-coded colours in `panel.css` and `page.css`).
2. **Rail:** `PageSidebar.tsx` into agent dropdown plus grouped, single-line session list; fix the header overlap;
   short generated titles.
3. **Top bar and shell:** slim header, status pill, agent chip; remove the duplicate "New chat" card
   (`WorkspaceShell.tsx`).
4. **Transcript:** `AssistantMessage`/`UserMessage` restyle, prose styles, collapsible tool steps from the existing
   execution data (`ExecutionCard.tsx`), hover actions (`FeedbackBar.tsx`).
5. **Approval card:** restyle `ConfirmCard.tsx`, composer and rail hints while pending.
6. **Composer:** single container, attachment chips inside, voice states (`Composer.tsx`, `VoiceButton.tsx`).
7. **Empty state and drawer:** suggestions, tabs and citations (`EmptyState.tsx`, `InspectorDrawer.tsx`).
8. **Responsive and accessibility pass:** breakpoints, keyboard map, reduced motion, focus order.
9. **Verify:** `tsc`, the unit tests, then Chromium screenshots at 1440, 1024 and 390 px in both themes, plus the
   voice browser check.

Out of scope for now: new features (image attachments in the composer arrive with the multimodal roadmap), branding
changes, and replacing React or esbuild.

## Open questions for review
- Keep the teal brand colour, or move to a neutral palette with teal only for actions?
- Should the Activity drawer be open by default on wide screens?
- Do you want pinned sessions, or is date grouping enough?
