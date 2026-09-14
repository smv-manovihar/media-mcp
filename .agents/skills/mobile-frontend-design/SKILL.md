---
name: mobile-frontend-design
description: >
  Apply this skill whenever the user wants to build, audit, or refactor a UI that will be viewed on a phone. Triggers include: "make this work on mobile", "responsive design", "mobile-first", "looks broken on phone", "drawer on mobile and dialog on desktop", "mobile navigation", "overflow on mobile", "buttons too small", "fix layout for small screens", "touch-friendly UI", or any request to build a web page or React/HTML component that must work across screen sizes. Always apply this skill before writing layout code for any component that will be viewed on a phone — even when the user just says "build a landing page" or "make a settings panel". Covers: the mobile-first mental model, container-query vs media-query decisions, fluid typography, component substitution rules, navigation patterns, overflow prevention, touch targets, safe-area insets, viewport units, forms, data tables, responsive images, and a pre-flight audit checklist.
---

# Mobile-First Design

## Core philosophy

Design for the smallest screen first, then add complexity as the screen grows — never strip it away. Mobile is the foundation; desktop is an enhancement layer. Designing desktop first and squishing it down always breaks; the reverse almost always works. Mobile is now the majority of web traffic and the basis of search ranking, so treat the phone layout as the primary deliverable, not a fallback.

The constraint is the feature: a small viewport forces you to decide what the screen is actually for. Before writing layout code, assign every section a tier:

- **Must show** — primary content + primary action, visible above the fold on mobile.
- **Should show** — supporting context, reachable in one short scroll.
- **Can show** — supplementary content, revealed or expanded only at `md:`/`lg:`.

If a section does not serve the screen's core task, it does not belong in the mobile layout. Carry the same content hierarchy and interaction model up through every breakpoint — do not invent a different IA for desktop.

---

## MANDATORY FIRST STEP: read the existing code

Before changing anything, scan the component for these failure seeds and note each one:

1. Fixed widths (`width: 600px`, `min-width: 400px`) → overflow.
2. `overflow: hidden` on outer wrappers with no `overflow-x` awareness → clipped or scroll-trapped content.
3. Desktop-only nav/sidebar with no mobile equivalent.
4. Dialogs/modals constrained to a pixel width → unusably small or clipped on phones.
5. Text `< 16px` inside `<input>`, `<select>`, `<textarea>` → iOS focus-zoom.
6. `100vh` → wrong height under mobile browser chrome (use `100svh`/`100dvh`).
7. Horizontal flex rows with no `flex-wrap`, `flex-col`, or `min-w-0` → overflow.

Do not touch desktop-specific styles unless explicitly asked. Scope every new rule to the mobile base layer first, then layer desktop overrides on top.

---

## Breakpoints

Tailwind defaults; adjust to the project's tokens if it defines its own.

| Name | Width    | Target       | Prefix |
|------|----------|--------------|--------|
| xs   | < 640px  | Phones       | *(none)* |
| sm   | ≥ 640px  | Large phones | `sm:`  |
| md   | ≥ 768px  | Tablets      | `md:`  |
| lg   | ≥ 1024px | Laptops      | `lg:`  |
| xl   | ≥ 1280px | Desktops     | `xl:`  |

**Rules.** Write base (unprefixed) styles for mobile; add `md:`/`lg:` to override upward. Never use `max-width` media queries — they fight the mobile-first cascade and create specificity wars. Do not chase device-specific breakpoints: add a breakpoint where the *content* starts to look stretched or cramped, not where a named device sits. Keep total breakpoints few.

---

## Container queries vs media queries (decide this first)

This is the single most important modern decision and it governs everything below. Container queries are baseline (Chrome/Firefox/Safari/Edge, ~95% support) — use them in production with no polyfill.

**The rule:**
- **Responsive behavior that belongs to a reusable component → container query.** Any component that can appear in more than one context (card, widget, media object, form block, list row) must respond to *its own available width*, not the viewport. A card built on media queries breaks the moment it moves from a wide column into a narrow sidebar, because the viewport did not change even though its space did. A card built on container queries adapts automatically anywhere you drop it.
- **Responsive behavior that belongs to the page → media query.** Page shell, global column count, and the mobile-vs-desktop navigation swap stay on media queries.
- **User-preference and environment conditions → always media queries:** `prefers-reduced-motion`, `prefers-color-scheme`, `prefers-contrast`, and print. Container queries cannot express these.

Most real layouts need both. Setup is two steps — declare containment on the wrapper, then query it:

```css
.card-wrap { container-type: inline-size; }   /* almost always inline-size, not size */
@container (min-width: 28rem) {
  .card { grid-template-columns: 8rem 1fr; }   /* horizontal only when the card itself has room */
}
```

In Tailwind (v3.4+): wrap with `@container` and prefix variants with `@md:`, `@lg:` etc. Name containers (`container-name`) when nesting so a query targets the intended ancestor. Use `container-type: inline-size` by default — `size` requires a fixed block dimension and is rarely what you want. Gate enhancements with `@supports (container-type: inline-size)` only if you must support pre-2023 browsers; otherwise the default un-queried layout is the fallback, so design that base layout to be usable at any width.

**Do not** convert every media query to a container query. Page-level structure stays on media queries. **Do not** over-nest containers — excessive containment has a measurable rendering cost.

---

## Fluid typography and spacing

Prefer continuous scaling over breakpoint jumps for type and spacing. `clamp(MIN, PREFERRED, MAX)` scales smoothly between a floor and ceiling without media queries:

```css
h1 { font-size: clamp(1.75rem, 1.2rem + 2.5vw, 3rem); }
```

Use `vw`/`vh` in the preferred term for viewport-relative scaling, or container units (`cqi`, `cqw`) when the text should scale to its component rather than the screen. Build the whole type and space scale this way (a tool like Utopia generates the `clamp()` custom properties) so you stop hand-tuning sizes at each breakpoint. Hard floors still apply: body text never below 14px, and form inputs never below 16px regardless of what `clamp()` computes.

---

## Modern CSS toolbox

Reach for these before writing JavaScript or piling on media queries:

- **`:has()`** — let a parent restyle based on its children (e.g. a card that goes two-column only when it contains an image). Replaces JS layout branching.
- **CSS nesting + cascade layers (`@layer`)** — keep component rules self-contained and specificity predictable; prevents the override wars that break responsive code.
- **Intrinsic layout** — `grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr))` reflows from one column to many with no breakpoints. Prefer this to fixed `grid-cols-N`.
- **View Transitions API** — native page/state transitions without a framework; improves perceived mobile quality.
- **If responsive behavior needs JavaScript, you are probably doing it wrong** — exhaust container queries, `clamp()`, intrinsic grids, and `:has()` first.

---

## Component substitution table

Pick the right component per screen size. These are correctness decisions, not stylistic ones.

| Feature / intent | Mobile (base) | Desktop (`md:`/`lg:`) |
|---|---|---|
| **Primary navigation (2–5 dests)** | **Fixed bottom tab bar** (icons + labels, thumb zone) | Horizontal top navbar or persistent sidebar |
| **Primary navigation (6+ dests)** | Bottom tab bar for the top 3–5 + "More" → drawer/sheet for the rest (Priority+ pattern) | Top navbar or sidebar |
| **Secondary / rare nav** | Hamburger → slide-in drawer (closes on backdrop tap + Esc) | Sidebar or overflow menu |
| **Settings / filters / sort** | Bottom sheet (slides up, drag handle, swipe-to-dismiss) | Popover, side sheet, or inline sidebar |
| **Confirmation / destructive alert** | Dialog, full-width with large targets | Standard centered modal |
| **Row/card extra actions** | Tap-to-expand accordion or bottom-sheet action list | Visible inline action buttons |
| **Sidebar / filter panel** | Off-canvas drawer, toggled by a button | Persistent inline sidebar |
| **Data table (many columns)** | Card list (one card per row) or accordion | Full table |
| **Multi-step / long form (5+ fields)** | Full-screen step-by-step, one logical group per screen | Multi-column form |
| **Date / time** | Native `<input type="date">` or bottom-sheet calendar | Popover/inline calendar |
| **Tabs (3+)** | Horizontally scrollable chip/tab strip *or* `<select>` | Standard tab bar |
| **Overflow menu ("…")** | Bottom-sheet action list | Dropdown/context menu |
| **Image gallery** | Full-screen swipeable carousel | Grid |
| **Long / multi-line text** | Clamp to 2–3 lines + "Show more", or collapse behind accordion / `<details>` | Show inline (room allows) |
| **Tooltip** | **Do not use** (no hover on touch) → inline help text or tap-for-info icon | Hover tooltip |
| **Hover card / preview** | Tap to expand or navigate | Hover card on cursor |
| **Rich text area** | Single column, full-width, label above | Multi-column or label-inline |
| **Search** | Full-width sticky bar at top | Inline search in header |

### The three swaps that matter most

**Bottom tab bar is the default for primary navigation — not the hamburger.** Hidden menus measurably reduce task completion and feature discovery (NN/g: ~21% drop); apps that moved primary nav from hamburger to a visible bottom bar saw engagement rise ~30%. Keep 3–5 items, each with icon **and** label, fixed in the thumb zone. Reserve the hamburger/drawer for *secondary* items only. Both iOS (Tab Bar) and Android (Bottom Navigation) standardize on this.

**Bottom sheet is the dominant container for secondary content.** Settings, filters, confirmations, previews, share, and overflow actions all belong in a draggable bottom-anchored panel (the mobile replacement for every popover/floating panel). It must have a visible grab handle, support drag-to-dismiss, and convert to a popover or side sheet on desktop. Use a full-screen takeover only when the task genuinely deserves the whole screen.

**Drawer vs Dialog.** Drawer (slides from an edge, non-blocking) for navigation and supplementary content that needs no immediate decision. Dialog (modal, blocks background) only when the user *must* decide before continuing — confirmations, warnings, critical errors. On mobile, dialogs are full-width with large targets; never a fixed pixel width.

**Gestures are accelerators, never the only path.** Swipe/long-press are hard to discover. Always keep a visible control for any core action and treat gestures as optional speed-ups. Pair them with haptic feedback where available.

---

## Navigation: decision and rules

```
Primary destinations?
├── 2–5 → Fixed bottom tab bar (icon + label, thumb zone)
├── 6+  → Bottom tab bar (top 3–5) + "More" → drawer/sheet (Priority+)
└── Deep hierarchy → Bottom bar for sections + nested accordion inside a drawer
Secondary / rare items → hamburger → drawer (never primary nav)
```

- Bottom nav/tab bar is `position: fixed; bottom: 0` with `padding-bottom: env(safe-area-inset-bottom)`.
- Hamburger and close triggers should be touch-friendly and proportionally padded; drawer closes on backdrop tap and `Escape`.
- Active/selected state is always visually distinct; users must never guess where they are.
- Nav `z-index` sits above page content but below dialogs.
- Nav items never require horizontal scroll — collapse overflow into "More".
- Keep nav position consistent across pages (muscle memory). Avoid hiding the tab bar on scroll unless tested.
- **Build the mobile nav as a separate element.** Hide desktop nav with `hidden md:flex`; show mobile nav with `flex md:hidden`. Never repurpose or restructure the desktop nav to serve mobile.

---

## Overflow prevention

Horizontal scrolling on mobile = broken, unless inside a deliberately scrollable element (`overflow-x: auto`).

| Cause | Fix |
|---|---|
| Fixed-width element | `w-full` or `w-full md:w-96` |
| Flex row, no wrap | `flex-wrap` or `flex-col md:flex-row` |
| Text won't truncate in flex child | `min-w-0` on **every** flex ancestor |
| `100vw` on inner element | `w-full` (`100vw` includes the scrollbar) |
| Long URLs / unbreakable strings | `break-words` / `overflow-wrap-anywhere` |
| Table with no scroll | wrap in `overflow-x-auto` |
| Image wider than container | `max-w-full h-auto` |
| Fixed `grid-cols-N` | intrinsic grid or `grid-cols-1 sm:grid-cols-2 lg:grid-cols-4` |
| Negative margin (`-mx-4`) | pair with equal padding on the parent |

**The `min-w-0` rule (the most common React overflow bug):** in any flex chain where a child holds text that should truncate, *every* flex ancestor needs `min-w-0`. Miss one level and the text pushes the container wide.

```jsx
// CORRECT — min-w-0 at every flex level
<div className="flex min-w-0">
  <div className="flex-1 min-w-0">
    <h3 className="truncate">Very long title that would otherwise break the layout</h3>
  </div>
</div>
```

---

## Touch targets

- Interactive elements must be proportionally sized and touch-friendly on mobile while scaling cleanly across screen sizes.
- Maintain adequate proportional spacing (`gap-2 sm:gap-3`) between adjacent targets.
- Icon-only buttons should use relative padding, touch-friendly hit areas, and accessible labels; avoid hardcoded pixel dimensions or hover-only tooltips on touch devices.

```jsx
// CORRECT — proportional, touch-friendly sizing with screen-reader label
<button className="p-2 sm:p-3 rounded-lg text-muted-foreground hover:text-foreground transition-colors" aria-label="Close">
  <XIcon className="size-5 sm:size-4" />
</button>
```

**Thumb zone.** The lower screen area is the comfortable one-handed reach zone on mobile devices. Put primary actions, the tab bar, and any FAB there. Push destructive or rare actions (delete, sign out) to the upper zone so they are harder to hit by accident. Proportional scaling ensures controls remain comfortably reachable across all viewports.

---

## Buttons

- Primary action: `w-full` on mobile, `md:w-auto` on desktop. Never blanket-apply `w-full` to desktop buttons.
- Scale height and padding proportionally (`py-2 px-4 sm:px-6`); font ≥ `text-sm` sm:`text-base`.
- Place primary CTA at the bottom of the form/screen; make it sticky when the form is long, with `safe-area-inset-bottom` padding so the keyboard doesn't bury it.
- Put a destructive secondary action *above* the primary (users scan bottom-up); keep proportional gaps between buttons.
- Always show a disabled + spinner loading state to prevent double-submit on slow networks.

---

## Typography and text

| Rule | Value |
|---|---|
| Body text | `text-sm` (14px) floor |
| Input text | **16px hard minimum** — anything smaller triggers iOS focus-zoom |
| Line height | `leading-relaxed` for mobile body copy |
| Heading sizes | ≤ 3 on mobile; scale up with `md:` or `clamp()` |
| Long-form width | `max-w-prose` on desktop; no max-width needed on mobile |
| Truncation | `truncate` + `min-w-0`, never `overflow-hidden` alone |

**iOS focus-zoom** happens when an input's `font-size < 16px`. Always set form inputs to `text-base`+. If a smaller visual is required, shrink the `::placeholder`, never the input.

---

## Managing text density (progressive disclosure)

Long or multi-line text is the main source of clutter on a phone — a description that wraps to six lines pushes the primary action off-screen and makes the page feel like a wall. The rule: **show a short, scannable version by default; let the user reveal the rest on demand.** Summarize on mobile, expand on tap.

What to do:

- **Clamp supplementary text to 2–3 lines** with `line-clamp` (`-webkit-line-clamp` / Tailwind `line-clamp-2`) and pair it with a **"Show more" / "Show less"** toggle that expands in place. Never clamp without giving a way to read the rest.
- **Collapse secondary blocks** (long descriptions, specs, FAQs, metadata, "about" copy) behind an accordion, a tap-to-expand card, or native `<details>`/`<summary>` — zero-JS, accessible, and keyboard-operable by default.
- **Push deep detail off the page** entirely: show a 1–2 line summary with a "View details" affordance that opens a bottom sheet or a dedicated screen, rather than rendering everything inline.
- Make the disclosure affordance obvious (a "Show more" link or chevron) with a touch-friendly proportional hit target; toggle its label and `aria-expanded` state.
- Default to **collapsed** for anything in the *Should-show* / *Can-show* tiers; keep it expanded only when the text is the primary content.

What to avoid:

- Never truncate or hide the **primary content or the primary action** — only supplementary text.
- No bare `…` ellipsis with no path to the full text.
- Don't hide so aggressively that a core task needs several taps to reach — disclosure reduces clutter, it shouldn't bury essentials.

```jsx
// Clamp + reveal (only the description collapses; title and CTA stay visible)
<p className={expanded ? "" : "line-clamp-2"}>{description}</p>
<button
  className="mt-1 py-2 px-3 text-sm font-medium text-primary hover:bg-primary/5 rounded-md transition-colors"
  aria-expanded={expanded}
  onClick={() => setExpanded(v => !v)}
>
  {expanded ? "Show less" : "Show more"}
</button>
```

---

## Forms

- **Single column always** on mobile — no multi-column grids.
- **Label above the input** — never floating/inline (ambiguous once filled).
- Correct `type` per field (`email`, `tel`, `number`) to summon the right keyboard, plus correct `autocomplete` tokens for autofill.
- Errors inline below the field, with icon + color — never toast-only on a form.
- The keyboard covers ~40% of the screen: keep the focused field and submit button reachable (scroll-into-view or sticky submit).
- Break long forms (5+ fields) into full-screen steps rather than one long scroll.

---

## Data tables

Full tables do not fit phones. Pick a strategy:

| Situation | Mobile strategy |
|---|---|
| 2–3 columns | `overflow-x-auto` + frozen first column |
| Many columns, selectable rows | Card list — one card per row, key fields only |
| Per-row actions | Tap row → expand accordion with details + actions |
| Comparison table | Accordion-grouped attributes, locked first column |
| Summary + detail | 2–3 key columns; "View details" → page or bottom sheet |

Implement as parallel views: `hidden md:block` for the `<table>`, `block md:hidden` for the card list — never try to reflow one `<table>` into cards with CSS alone.

---

## Images and media

- Every `<img>`: `max-w-full h-auto`, always.
- Serve responsive sources with `srcset`/`sizes` and modern formats (AVIF/WebP); the LCP image should stay under ~200KB.
- Use `aspect-ratio` (not fixed `height`) to hold proportions; `object-fit: cover` for controlled crops.
- Avoid fixed-height image containers on mobile (distortion). Hide purely decorative images with `hidden md:block`.

---

## Viewport units

`100vh` is broken under mobile browser chrome. Use:

| Unit | Meaning | Use for |
|---|---|---|
| `svh` | height with all chrome visible | hero/landing — safe default |
| `dvh` | current height as chrome shows/hides | app shells, full-screen layouts |
| `lvh` | height with chrome hidden | rare; causes initial cutoff |
| `vh` | legacy, broken on mobile | fallback only |

```css
.app-shell { display: grid; grid-template-rows: auto 1fr auto; height: 100dvh; overflow: hidden; }
.app-content { overflow-y: auto; -webkit-overflow-scrolling: touch; }
```

---

## Safe-area insets

Notches, rounded corners, and home indicators hide or block content. Apply `env(safe-area-inset-*)` to fixed top bars, fixed bottom navs, full-screen modals/drawers, and sticky footer buttons. This requires the viewport meta tag:

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
```

```jsx
<nav className="fixed bottom-0 left-0 right-0" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
```

---

## Spacing and layout scaling

Mobile needs breathing room; desktop can afford more. Scale padding/gap up with breakpoints rather than reusing one value: `px-4 py-8 md:px-8 md:py-16`, `p-4 md:p-6`, `gap-3 md:gap-6`. Default to `flex-col` → `md:flex-row` and `grid-cols-1` → `md:grid-cols-2 lg:grid-cols-3`, or use an intrinsic `auto-fit` grid to skip the breakpoints entirely.

---

## What NOT to change on desktop

This skill is *mobile-first*, not *mobile-only*. Add mobile support without disturbing desktop:

1. Do not restructure desktop layouts — only add responsive classes.
2. Do not remove desktop-only interactions (hover, tooltips, multi-column) — hide them with `hidden md:block` and provide a mobile equivalent with `block md:hidden`.
3. Do not change desktop font sizes or spacing unless they are actively wrong.
4. Build a parallel mobile nav; never repurpose the desktop one.
5. A desktop sidebar stays visible on desktop — add a mobile drawer *in addition*, not *instead*.

---

## Pre-flight audit checklist

Verify on a real phone or a 375px viewport before declaring "mobile-ready". Also re-check desktop at 1280px to confirm nothing regressed.

**Layout** — no horizontal scroll at 375/390/414px · nothing escapes the viewport · all flex rows have `flex-wrap`/`flex-col` safety · truncating children have `min-w-0` on themselves and every ancestor · no fixed widths > 375px.

**Container queries** — reusable components respond to their container, not the viewport · verified in every context they appear (sidebar, grid, modal).

**Navigation** — primary nav is a visible bottom bar (not a hamburger) · desktop nav is `hidden md:block`, mobile nav is separate · close button is touch-friendly and proportional · active route indicated · drawer closes on backdrop + Esc.

**Touch** — targets are touch-friendly and proportionally sized · adequate separation between targets · no hover-only interactions · no action reachable only by gesture.

**Forms** — inputs ≥ 16px (no zoom) · labels above · errors inline · submit reachable with keyboard open · correct `type` attributes.

**Text** — body ≥ 14px · nothing clipped/overflowing · readable line lengths (not one word per line) · long/multi-line copy is clamped or collapsed behind "Show more"/accordion, not dumped full-height.

**Viewport / safe area** — `100vh` replaced with `svh`/`dvh` · fixed bottom bars have `safe-area-inset-bottom` · meta tag includes `viewport-fit=cover` · nothing hidden behind notch/home indicator.

**Dialogs / sheets / drawers** — dialogs full-width (not fixed px) · sheets have a drag handle · modals scroll internally past ~90% height · drawers close on backdrop + Esc.

**Preferences** — `prefers-reduced-motion` respected · keyboard focus visible.

---

## Anti-patterns to reject

| Anti-pattern | Why wrong | Fix |
|---|---|---|
| Hamburger for primary nav | Hides destinations, cuts task completion/discovery | Visible bottom tab bar; hamburger for secondary only |
| Media query on a reusable component | Breaks when the component moves contexts | Container query on its wrapper |
| `width: 500px` on a card | Overflows phones | `w-full md:w-[500px]` |
| Dialog `max-w-xl` with no `w-full` | Wrong width on phones | `w-full max-w-xl mx-4` |
| Tooltip on an icon button | Untriggerable on touch | Visible label or tap-to-expand |
| Gesture as the only path to an action | Undiscoverable | Add a visible control; gesture is a bonus |
| `flex` with no wrap | Overflows | `flex-wrap` / `flex-col md:flex-row` |
| `100vh` full-screen layout | Broken on mobile Safari | `100svh` / `100dvh` |
| Input font-size 14px | iOS focus-zoom | 16px minimum |
| Hover-only mega menu | Unusable on touch | Tap-triggered drawer/sheet |
| Fixed sidebar on mobile | Covers content, no close | `hidden md:block` + hamburger |
| Desktop-size buttons on mobile | Too small to tap | `py-3 text-base` on base |
| Multi-column form on mobile | Cramped fields | Force single column |
| 6+ column table, no scroll | Escapes viewport | `overflow-x-auto` or card list |
| Breakpoint-jumped font sizes | Janky scaling, many overrides | `clamp()` fluid type |
| Long multi-line text dumped full-height | Clutters screen, pushes CTA off | `line-clamp` + "Show more" / accordion / `<details>` |