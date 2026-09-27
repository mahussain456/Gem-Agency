---
name: Gem Agency
description: A graphite and teal workspace for building, launching, and growing websites.
colors:
  primary: "#64d4bd"
  primary-light: "#a0eadb"
  primary-wash: "#203c36"
  primary-ink: "#0f2822"
  primary-hover: "#9de8d9"
  canvas: "#141719"
  canvas-secondary: "#181d20"
  surface: "#1a1f22"
  surface-inset: "#151b1e"
  surface-nested: "#1c2327"
  raised: "#22292d"
  raised-strong: "#2a3338"
  border: "#2b3337"
  border-control: "#3d484e"
  text: "#f4f7f6"
  text-secondary: "#b7c3c8"
  text-muted: "#9baab1"
  live: "#8bbff0"
  success: "#78dba6"
  warning: "#f3c56e"
  critical: "#ff9eab"
  accent-amethyst: "#a594ff"
  accent-amethyst-light: "#cbc0ff"
  accent-amethyst-wash: "#27233f"
  accent-amethyst-ink: "#17123a"
  accent-sky: "#7cc4ff"
  accent-sky-light: "#b5dcff"
  accent-sky-wash: "#1b2d3f"
  accent-sky-ink: "#0b1f31"
  accent-orchid: "#e39cf2"
  accent-orchid-light: "#f1c9f8"
  accent-orchid-wash: "#35233b"
  accent-orchid-ink: "#2f1235"
  accent-mono: "#e6ebe9"
  accent-mono-wash: "#2a3033"
typography:
  headline:
    fontFamily: 'Inter, "Inter var", "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif'
    fontSize: "32px"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.035em"
  title:
    fontFamily: 'Inter, "Inter var", "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif'
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: 'Inter, "Inter var", "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: 'Inter, "Inter var", "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif'
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1
  metadata:
    fontFamily: 'Inter, "Inter var", "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif'
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.6
rounded:
  compact: "5px"
  control: "6px"
  composer: "7px"
  decision: "8px"
  field: "9px"
  panel: "12px"
  large: "14px"
  pill: "20px"
spacing:
  compact: "8px"
  small: "12px"
  medium: "16px"
  panel: "18px"
  roomy: "20px"
  page: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-ink}"
    rounded: "{rounded.control}"
    padding: "0 13px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "#232b2f"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "0 13px"
    height: "36px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.control}"
    height: "36px"
    padding: "0 13px"
  field:
    backgroundColor: "{colors.surface-inset}"
    textColor: "{colors.text}"
    rounded: "{rounded.field}"
    padding: "8px 11px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "18px"
  nav-active:
    backgroundColor: "#23322f"
    textColor: "{colors.primary}"
    rounded: "{rounded.compact}"
    height: "41px"
    padding: "0 10px"
  chip-selected:
    backgroundColor: "{colors.primary-wash}"
    textColor: "{colors.primary}"
    rounded: "{rounded.pill}"
    padding: "0 12px"
    height: "30px"
  decision-card:
    backgroundColor: "{colors.surface-nested}"
    textColor: "{colors.text}"
    rounded: "{rounded.decision}"
    padding: "13px"
---

# Design System: Gem Agency

## Overview

**Creative North Star: "Growth Command Center"**

Gem Agency is a compact operating workspace in graphite and teal. Neutral surfaces organize projects, tangible website output, decisions, and measured growth. The gemstone-G identity gives the workspace its recognizable signature; the surrounding interface remains restrained and readable.

The same visual language carries idea intake through delivery, review, and growth evidence. Emphasis comes from useful hierarchy, surface tone, and a clear action color. Preserve the distinction between completed builds and published websites, and show unavailable measurements as an explicit state.

**Key Characteristics:**

- Graphite canvas with subtly separated neutral panels.
- Teal actions and selection, with distinct semantic status colors.
- Compact sans-serif hierarchy and aligned numerical evidence.
- Persistent desktop navigation and a linear mobile workspace.
- Actual website previews and the faceted gemstone-G identity.

This is a scan of the implemented interface. Source precedence is `app/q/css/quantum.css` followed by `app/q/css/command.css`, as linked by `app/q/index.html`. Components were sampled from the overview, growth, projects, and builder modules. The later stylesheet owns the current visual world; older stylesheet commentary does not describe it.

## Colors

The palette is dark graphite with a cool mint-teal action accent and pale, readable text.

### Primary

- **Gem Teal** (`primary`): primary buttons, active navigation, progress, chart lines, focus outlines, and caret.
- **Light Gem Teal** (`primary-light`): accent text in badges.
- **Teal Wash** (`primary-wash`): selected chips and accent-tag backgrounds.
- **Deep Teal Ink** (`primary-ink`): the dark label on the solid primary button.
- **Hover Teal** (`primary-hover`): the brighter primary-button hover state.

### Secondary

These colors communicate state rather than a second brand identity.

- **Live Blue** (`live`): informational and live-work states.
- **Success Green** (`success`): completed work and positive measurements.
- **Attention Amber** (`warning`): decisions, recovery, and downward dashboard trends.
- **Critical Rose** (`critical`): failures and destructive actions.

### Neutral

- **Graphite Canvas** and **Secondary Canvas**: the working background and supporting shell surfaces.
- **Panel Graphite**, **Inset Graphite**, and **Nested Graphite**: panels, fields/sidebar, and nested delivery or decision surfaces.
- **Raised** and **Raised Strong**: hover surfaces and small supporting markers.
- **Border** and **Control Border**: quiet section boundaries and stronger field edges.
- **Primary Text**, **Secondary Text**, and **Muted Text**: headings/values, descriptions/controls, and supporting timestamps or provenance.

**The Action Accent Rule.** Use teal for action, selection, focus, and measured chart marks. Preserve semantic colors for status.

### Accent themes

Teal is the default and the brand. The operator may swap the action accent for **Amethyst**, **Sky**, **Orchid** or **Mono** in Customise (saved per browser, applied as `html[data-accent]` in `app/q/css/motion.css`). Each theme supplies the full set a theme needs: accent, light, wash and ink. Surfaces, text and semantic colors never change with the accent.

**The No-Alarm Accent Rule.** An accent may never resemble a status color. Amber (warning) and rose or red (critical) are not offered as accents, and green-leaning hues stay distinct from Success Green.

## Typography

**Display and Body Font:** Inter with the exact fallback stack recorded in the frontmatter.

**Label/Mono Font:** JetBrains Mono, Cascadia Mono, Cascadia Code, ui-monospace, SFMono-Regular, Consolas, monospace for keyboard hints, selected compact counts, and builder step numbers.

The hierarchy is compact and direct. Headlines use tight tracking and substantial weight; operational descriptions stay regular. There is no separate serif display role in the current core workflow.

### Hierarchy

- **Headline:** the recorded role serves standard page headings. The overview uses line-height 1.18; its final desktop override is 32px. At mobile widths it is 29px, while standard page headings become 27px.
- **Title:** 16px panel headings establish clear local groups.
- **Body:** 14px shell text; detailed forms and supporting content commonly use 13px. Longer explanatory copy uses line-height 1.6–1.8.
- **Label:** 12px field labels, lane headings, and metric names.
- **Metadata:** 11px progress context, timestamps, provenance, and helper copy.
- **Numerical evidence:** overview values use sans-serif, weight 650, tabular numerals, and tight tracking; desktop values settle at 28px. Numbers remain aligned when they change.

**The Evidence Hierarchy Rule.** Make the value and next action easy to scan, and keep source, status, and time context immediately adjacent.

## Layout

The shell is a full-height grid with an independently scrolling work area. Desktop navigation is normally 210px wide and becomes 185px at 1200px and below. The top bar is 64px high. Content uses a centered maximum width of 1900px, 24px work padding, and an 18px main gap. At 1200px work padding becomes 20px; at 700px it becomes 18px vertically and 14px horizontally.

Navigation collapses to a 64px icon rail at 1023px and below. At 767px and below it becomes an off-canvas drawer opened from the top bar, with a scrim. The user can also collapse desktop navigation.

The overview has a flexible main column and a 290px decisions/activity rail; the rail is 320px from 1600px and 260px at 1200px and below. At 980px it moves under the main content as a two-column section. At 700px and below the overview becomes a vertical sequence, and its four metrics become a two-by-two grid.

The overview composition is a page-level pattern: headline and idea composer, four real metrics, five delivery lanes, and lower growth/opportunities panels, with decisions and activity alongside. Other pages reuse its hierarchy without copying the full dashboard.

The delivery board preserves all five lanes with horizontal scrolling. Lanes have a minimum width of 125px on larger screens and a fixed width of 165px at 700px and below. Individual lanes scroll vertically under sticky headings; desktop lane height is capped at 235px, or 280px from 981–1400px. The builder's form/help columns collapse at 980px.

**The Preserve the Workflow Rule.** Stack the surrounding content on small screens while retaining the board's horizontal stage order.

## Elevation & Depth

Depth is primarily tonal. The canvas, panels, nested cards, and controls use nearby graphite values with thin borders. Main panels, the command shell, and primary buttons have no resting shadow. Avoid reintroducing the atmospheric layers disabled by the current stylesheet.

Overlays retain their own separation: the inherited modal has a soft shadow and backdrop blur, and the mobile navigation drawer casts a side shadow. The shared raised-shadow token is `0 12px 32px #0005`; exact overlay, focus, and motion values are in the sidecar.

Interaction transitions use the inherited 160ms standard easing; pressed buttons shift down 1px and scale to .985 over 120ms. Page entry uses a 460ms entrance with short staggered delays. Reduced-motion preference disables animation and transitions throughout the interface.

An optional **ambient light** (three faint accent washes drifting behind the shell) exists for operators who want it. It is off by default, respecting the retired atmosphere; panels stay opaque over it, so contrast never changes.

## Motion

Motion explains something: a page arriving, a number changing, where you are, what you pressed. Transform and opacity only. Auto-refresh repaints are quiet, so nothing re-animates under a reader.

- **Levels:** Full, Calm (fades only, no movement) and Off, chosen in Customise and applied as `html[data-motion]`. The operating system's reduced-motion setting forces Off.
- **Page entry:** panels, metrics, lanes and rows rise 12px with a 40ms stagger over 460ms; the page heading resolves from a light blur.
- **Numbers:** plain numeric values count up once on page entry; the final frame is always the rendered text, so a count-up can never display a wrong value. Dashes and unavailable states never animate.
- **Navigation:** one indicator glides to the active row (keeping the 2px inset bar) instead of the highlight jumping.
- **Route progress:** a 2px accent line at the top of the window while a page loads.
- **Pointer:** pressed controls show a ripple from the press point; the hovered panel lifts its surface with a faint pointer-following light (Full only, hover only, never at rest).

## Density

Comfortable is the recorded layout. **Compact** (Customise, `html[data-density]`) tightens padding and row height, uses 13px body text and the 27px page heading; values keep their documented size so evidence stays scannable.

## Shapes

Use gently rounded panels and compact controls. Panels use the panel radius, fields the field radius, and buttons the control radius. Smaller preview tiles and navigation rows use the compact radius; decision cards use the decision radius. Chips and tags are pill-shaped.

The gemstone-G logo is the faceted identity asset at `app/q/brand/gem-growth.svg`, displayed without a surrounding tile or decorative shadow. Functional icons are thin stroked SVGs. Project monograms are a fallback when a website preview does not exist.

## Components

### Buttons

Compact, clear actions. Primary buttons have solid Gem Teal, dark labels, and weight 650; secondary buttons use graphite with a control border. Default buttons are 36px high, with a 30px small variant. The idea composer's action is 38px high. Ghost buttons retain text emphasis and gain a raised background on hover.

Primary hover brightens the fill. Pressed buttons move subtly. Disabled buttons use opacity .42 and a not-allowed cursor. Keyboard focus has a 2px teal outline with 3px offset.

### Chips and Tags

Chips select builder modes or filters. Selected chips use Teal Wash, teal text, and a thin inset teal highlight. Tags communicate status with a colored label and matching translucent wash; retain text labels alongside color. Their small dot may glow as a status marker.

### Cards / Containers

Panels have a thin neutral border, rounded corners, and a flat graphite fill. Headers divide title/action from content with a single horizontal border. Normal body padding is 18px; the compact desktop dashboard uses 12px 16px in its lower panels. Nested decision cards use 13px padding and a full-width action.

### Inputs / Fields

Fields use an inset graphite fill, stronger border, visible label, and muted placeholder. Standard fields have 8px 11px padding; textarea content can resize vertically. The builder's main brief has a minimum height of 150px, 15px type, and line-height 1.65.

Focused fields shift the border to teal and gain a soft teal ring, plus the global keyboard outline when applicable. The overview composer joins a flexible input, restrained icon, and primary action in one outlined container.

### Navigation

Rows are 41px high with 13px medium text and 18px stroked icons. Active rows combine teal text, a dark teal surface, and a 2px inset left indicator. Hover raises the neutral surface. The same destinations remain available in the mobile drawer.

### Delivery Board and Website Preview

Each lane has a title, saved project count, and a stack of clickable project entries. A real website preview leads each entry when available; otherwise a monogram and explicit preview state occupy the same space. Project name, progress bar, status label, and next step sit beneath it. Empty lanes use a dashed border and helpful text.

Preview imagery is the project's output, not decorative stock imagery. Device controls on a project detail page switch between full width and a 390px mobile frame.

### Growth Evidence

Charts use a thin teal line and translucent area over quiet horizontal grid lines. Keep the property selector, date period, source, comparison period, and daily-measurement disclosure with the chart. Missing connections and empty data use a titled explanation and a useful connection or audit action.

### Jarvis voice surface

Jarvis is the voice assistant (`app/q/js/jarvis/`, `app/q/css/jarvis.css`), opened with Ctrl J or the orb button in the top bar.

- **Full mode** is an overlay: the workspace blurs behind a centred orb, the heard phrase (14px italic, muted), the reply (20px, weight 450, balanced lines; 16px on small screens) and mono metadata naming the model that answered.
- **Dock mode** is a 20px-radius pill at the bottom of the window with a 52px orb and a two-line reply, used when Jarvis opens a page so the page stays visible.
- **The orb** is the one luminous element: its light and ring shape carry state — listening follows the real microphone, speaking pulses per word, thinking sweeps a comet, error turns critical. With motion off it renders one still frame per state, so state stays legible.
- **Cards** for attention items reuse the decision-card surface with a 2px severity bar in the matching semantic color.
- **Privacy is stated in the surface:** speech is transcribed by the browser's own service and never recorded by Gem Agency; the wake word is opt-in and says the mic stays open.

## Do's and Don'ts

### Do:

- **Do** reuse the graphite surfaces, teal action color, and compact sans-serif hierarchy.
- **Do** pair status color with visible text and preserve keyboard focus.
- **Do** use the gemstone-G asset and actual project previews where available.
- **Do** keep measurement provenance and unavailable states visible.
- **Do** preserve the five-lane reading order with horizontal scrolling on mobile.
- **Do** honor reduced-motion preferences, and the in-app motion level.
- **Do** keep work that spends money one human click away, including from voice.

### Don't:

- **Don't** revive the superseded amethyst atmosphere, serif core page headings, or decorative panel glow.
- **Don't** use fabricated metrics or imply that a completed build is already published.
- **Don't** force every page into the overview's dashboard composition.
- **Don't** hide useful workflow steps to make the mobile layout fit.

