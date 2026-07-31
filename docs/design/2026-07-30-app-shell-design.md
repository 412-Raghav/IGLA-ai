# App Shell Layout — Design (2026-07-30)

## Context
IGLA's frontend grew functionally (auth, threads, notes, uploads) inside a
single centered, max-width `<body>` that scrolls as one document. Two problems
surfaced in use:
- The whole page scrolls together, so on a long tactical answer the sidebar
  (New thread, Recent, My notes, login state) scrolls off the top and is lost.
- The layout is not anchored; it floats in the viewport and its position
  depends on window width.

This is the first block of a two-part frontend effort (Plan A): fix the
**structural shell now**; defer **visual polish** to a later presentation pass
batched with the README. This doc covers the shell only.

## Decision: full-viewport app shell (over centered document)
Adopt an app-shell layout — a fixed full-window frame with a pinned sidebar and
an independently scrolling main area — the pattern used by Claude / ChatGPT /
Slack.

Rejected: keep the centered max-width document and only make the sidebar sticky.
Smaller change, but leaves the layout floating and doesn't give the "it's a
tool" feel. The app-shell solves the scroll problem outright, and every future
control (9e focus, 9g refresh) sits inside it cleanly.

## Scope
IN (this block):
- App-shell frame; sidebar pinned, main area scrolls independently.
- Collapsible sidebar: labels when open, icon rail when closed.
- Sticky thread header (title · opponent).
- Composer pinned to the bottom of the main column, aligned to message width.
- User + Log out pinned to the bottom of the sidebar.
- Favicon (replace default globe) + dynamic tab title.

OUT (deferred to the polish / README pass):
- Colour system (semantic palette + contrast work). Shell reuses the current
  dark palette unchanged.
- Per-team accent colour.
- Tactical grid / background motif.
- Team-logo glassy overlay (+ its backend: logo URL + accent per team).
- Fidget / click-pen micro-interaction.

## Design
Two columns inside a full-height frame.
- Sidebar (fixed width, pinned, does not scroll): header (IGLA mark + collapse
  toggle) · `+ New thread` · Recent (thread list) · My notes (add + list) ·
  [pinned bottom] user · Log out.
- Main (fills remaining width, column): sticky thread header (title · opponent
  badge) · message list (the only scrolling region, centered comfortable-width
  column) · composer (pinned bottom).

Behaviours & sub-decisions:
1. Independent scroll. Only the message list scrolls; sidebar, thread header,
   and composer stay put. This is the core fix.
2. Collapse -> icon rail, lists hidden. Collapsed shows logo, +, a threads
   icon, a notes icon, avatar. Rejected: rendering thread names as icons in the
   rail — cramped and unreadable; you expand to browse, collapse to focus.
3. Collapse state persists via localStorage. (Valid here: index.html is a real
   page served by FastAPI, not a sandboxed artifact, so browser storage works.)
4. Colours untouched. Pure layout change reusing the current palette — one
   variable at a time, so any post-change oddity is provably structural.
5. User block bottom-pinned (Claude-style), freeing the top for actions.
6. Narrow screens: sidebar auto-collapses to the rail so it doesn't break small.
7. Dynamic tab title: document.title reflects the open thread (e.g. "IGLA —
   How does PRX attack Lotus?"), which also gives the tab-hover tooltip for
   free. Empty state falls back to "IGLA".
8. Favicon: a simple IGLA mark replaces the default globe.

## Success criteria
- A long answer scrolls while sidebar, thread header, and composer stay visible.
- Collapse/expand works and the state survives a reload.
- The thread header stays visible deep in a thread.
- Favicon shows; tab title tracks the open thread.
- No colour changes vs. the current build.
- All existing behaviour still works: login/register/logout, thread
  create/open/delete, notes list/upload/delete, the origins badge on fresh
  answers, and gate-rejected bubble styling.

## Non-goals / notes
- No backend changes. index.html only (plus a favicon asset).
- No new dependencies.