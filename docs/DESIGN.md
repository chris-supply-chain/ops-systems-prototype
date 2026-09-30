# Ops OS: design system

**Direction: "Guide sign."** An ops console for a California mobility company that
makes things in Taiwan and moves them to doors. The chrome borrows from freeway
guide signage: a deep sign-green sidebar, white condensed type, a sign-yellow
active rail. The content area reads like a manufacturing traveler: warm concrete
paper, crisp white cards, hairlines, monospaced serials. The data is the loudest
thing on the page, and the chrome stays quiet around it.

The one memorable element is **the brand block**, a small green guide sign with the
classic inset white border reading `OPS OS` over a `FACTORY → DOOR` route line.
Page kickers echo it as a small green "exit tab" plaque (`MAKE · 04`).

## Type

| Role | Family | Use |
|---|---|---|
| UI / body / numbers | **Barlow** 400/500/600/700 | everything by default, including hero and KPI values (proportional figures) |
| Condensed | **Barlow Semi Condensed** 500/600/700 | page titles, card titles, nav labels, table headers, kickers |
| Mono | **IBM Plex Mono** 400/500 | serials, lot ids, PO numbers, timestamps, SQL, JSON payloads |

Google Fonts link (with real fallbacks, since the app must still work offline):
`https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Barlow+Semi+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap`

```css
--font-ui:   "Barlow", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
--font-cond: "Barlow Semi Condensed", "Barlow", ui-sans-serif, system-ui, sans-serif;
--font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
```

Scale: body 14px/1.45; small 12.5px; micro (labels, table headers) 11.5px uppercase
+0.06em tracking; card title 15.5px cond 600; page title 30px cond 600, -0.005em;
KPI value 30px Barlow 600; hero figure 52px Barlow 600. `tabular-nums` only in
table cells and axis ticks, never on KPI or hero values.

## Color tokens

Light is the default. Dark swaps tokens and never flips automatically: it follows
`prefers-color-scheme` unless `<html data-theme="light|dark">` is set by the
toggle, which wins in both directions.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--canvas` | `#F3F2EE` | `#0E1110` | page background ("concrete") |
| `--surface` | `#FFFFFF` | `#1A1A19` | cards, drawer, tables, chart surface |
| `--surface-2` | `#F8F7F3` | `#212321` | table header, subtle wells, inputs |
| `--row-hover` | `#F4F3EE` | `#252725` | table row hover |
| `--ink` | `#141816` | `#F1F2EE` | primary text |
| `--ink-2` | `#4D5451` | `#C3C2B7` | secondary text |
| `--ink-3` | `#858B87` | `#898781` | muted text, axis labels |
| `--hairline` | `rgba(20,24,22,.10)` | `rgba(255,255,255,.10)` | borders, separators |
| `--grid` | `#E6E5DE` | `#2C2C2A` | chart gridlines (solid 1px) |
| `--axis` | `#C5C4BB` | `#383835` | chart baseline/axis |
| `--sign` | `#0B5A40` | `#07402D` | sidebar, brand, kicker plaque, primary accent |
| `--sign-2` | `#0E6A4C` | `#0B5239` | sidebar hover/active wash base |
| `--sign-ink` | `#F4F6F2` | `#F4F6F2` | text on sign green |
| `--sign-muted` | `rgba(244,246,242,.62)` | same | muted text on sign green |
| `--sign-yellow` | `#F4C430` | `#F4C430` | active rail, focus on dark, highlights (never data) |
| `--link` | `#0B5A40` | `#6FD3AA` | links, mono ids |
| `--focus` | `#0B5A40` | `#F4C430` | 2px focus ring |
| `--code-bg` | `#0F1A15` | `#0A0F0C` | SQL / JSON blocks (light text on dark in both themes) |
| `--code-ink` | `#E4EEE8` | `#E4EEE8` | code text |

**Status palette (fixed, reserved for state, always icon + label):**
good `#0CA30C` · warning `#FAB219` · serious `#EC835A` · critical `#D03B3B` · info `#2A78D6`
(info borrows categorical blue and is used only for neutral "in progress" states).
Chips use the status color for the icon/dot and a 12% tint for the background; the
chip's text stays in `--ink`.

**Chart palette (categorical, fixed order, never cycled). Validated per dataviz skill:**

| Slot | Light | Dark |
|---|---|---|
| 1 blue | `#2a78d6` | `#3987e5` |
| 2 orange | `#eb6834` | `#d95926` |
| 3 aqua | `#1baf7a` | `#199e70` |
| 4 yellow | `#eda100` | `#c98500` |
| 5 magenta | `#e87ba4` | `#d55181` |
| 6 green | `#008300` | `#008300` |
| 7 violet | `#4a3aa7` | `#9085e9` |
| 8 red | `#e34948` | `#e66767` |

De-emphasis gray for "context" series: `#B9B8B0` light / `#4A4A46` dark.
Sequential (magnitude, heat cells): single blue ramp `#cde2fb → #0d366b`.
Diverging: blue ↔ red with gray midpoint `#f0efec` / `#383835`.

## Layout

- `.app` grid: sidebar 248px + main. Under 960px the sidebar becomes an off-canvas
  drawer behind a menu button in the top bar.
- **Sidebar:** full-height, `--sign` background, 16px/12px padding.
  - Brand sign: radius 10px, `--sign` fill, inset border
    `box-shadow: inset 0 0 0 3px var(--sign), inset 0 0 0 4.5px var(--sign-ink)`,
    `OPS OS` in cond 700 26px white, `FACTORY → DOOR` 10.5px uppercase tracked
    0.18em with a thin arrow. Below the sign: `Prototype · mock data`, 11px muted.
  - Group labels: 10.5px uppercase, tracking 0.14em, `--sign-muted`, 18px above.
  - Items: 34px rows, radius 8px, cond 500 15px, 18px icon (stroke 1.75) at 60%
    opacity. Hover: `rgba(255,255,255,.07)`. Active: `rgba(255,255,255,.12)`, white
    label, yellow icon, 3px `--sign-yellow` rail on the left edge. Optional right
    badge (count), with a yellow pill and dark text when anything is critical.
  - Footer: dataset clock (mono), "Reset demo data" (inline two-step confirm),
    theme toggle.
- **Top bar:** sticky, 56px, canvas background, bottom hairline. Left: breadcrumb
  (`Make / Genealogy`). Right: global search (320px; `/` focuses; searches serials,
  lots, orders, POs, shipments, suppliers, items), `AS OF Sat Sep 26 · 08:00 PT`
  chip in mono, `MOCK DATA` chip, theme toggle.
- **Page:** padding 24px 28px 72px, max-width 1560px. Page header: exit-tab kicker
  (`MAKE · 04`: green plaque, white cond 600 11px tracked), title, lede (14.5px
  `--ink-2`, max 780px), then a goal line: a small outlined tag `GOAL` followed
  by the quoted responsibility in `--ink-2`. Page actions and filters sit right of
  the title, or in one filter row directly under the header.
- Grid: 12 columns, 16px gap. `.span-3/4/5/6/7/8/12`. Collapse to one column
  under 1100px.

## Components

- **Card:** surface, 1px hairline, radius 12px, padding 16px 18px. Header: title
  (cond 600 15.5px) + optional subtitle (12.5px `--ink-3`) + right-aligned actions.
  `flush` variant has zero body padding for tables.
- **KPI tile:** label (12.5px `--ink-2`, sentence case, no colon), value (30px 600),
  optional unit, delta (signed, with ▲/▼ glyph; color from text tokens:
  good = `#006300` light / `#0ca30c` dark, bad = `#B42F2F` light / `#e66767` dark),
  hint line (12px `--ink-3`), optional 12-point sparkline in de-emphasis gray with
  the last point in slot-1 blue.
- **Status chip:** 22px pill, 12px 600, dot or icon in the status color, 12% tint
  background, `--ink` text. `statusChip(value)` maps domain statuses to tones.
- **Buttons:** 32px (28px `.sm`), radius 8px. Secondary: surface + hairline.
  Primary: `--ink` background, white text; hover `--sign`. Ghost. Danger (critical
  tint). Icon buttons are 32px square.
- **Inputs/selects:** 34px, `--surface-2`, hairline, radius 8px, 2px `--focus` ring.
- **Table:** header 11.5px uppercase tracked `--ink-3` on `--surface-2`, sticky.
  Rows 36px, 13px, hairline separators, hover `--row-hover`. Numbers right-aligned
  with `tabular-nums`. Ids in mono 12.5px `--link`. Sortable headers show a caret.
  Clickable rows get a pointer cursor.
- **Tabs:** underline tabs in cond 500 14.5px, active = ink + 2px `--sign` bar
  (yellow in dark), optional count pill.
- **Drawer:** right side, 580px (100% under 700px), surface, big soft shadow,
  scrim `rgba(10,14,12,.32)`, header with title, subtitle and close; Esc closes.
- **Callout:** 3px left border in the tone color, 6% tint, bold title.
  Tones are good/warning/serious/critical/info/neutral. This is the "what the
  system decided" pattern.
- **Timeline:** vertical 2px line, 10px dots in tone color with 2px surface ring,
  mono timestamp, title, detail.
- **Code block:** `--code-bg`, `--code-ink`, mono 12.5px, radius 10px, padding 14px,
  horizontal scroll. Light SQL highlighting: keywords in `#F4C430`, strings in
  `#8FE0B8`, comments in `#7C8A83`.
- **Meter:** 6px track at blue-100 with blue-450 fill; status-toned variant.

## Charts (per the dataviz skill)

SVG, responsive through viewBox and width 100%. Bars are at most 24px thick with a
4px rounded data end, square at the baseline, and a 2px surface gap between
touching marks. Lines are 2px with round joins, markers r ≥ 4 with a 2px surface
ring. Area fills are the series color at 10%. Gridlines are solid 1px `--grid`,
never dashed. The baseline is `--axis`. Tick labels are 11px `--ink-3`
tabular-nums, rounded to clean numbers. Charts with two or more series get a
legend (rect keys for bars, line keys for lines), plus selective direct labels
on the endpoint or extreme. A single series gets no legend box. Text never wears
the series color.

Hover is on by default. Line and area charts get a crosshair that snaps to the
nearest x and shows one tooltip listing every series. Bars and cells are their
own hit targets, with at least 24px of hit area. In the tooltip the value is
strong and the label secondary, keyed by a short line in the series color.
Every chart card offers a **table view** toggle. Status colors appear in a chart
only when the series *means* good or bad. There is never a dual axis.

## Motion

Page enter: fade plus 6px rise, 180ms ease-out. KPI tiles stagger by 30ms. Drawer
slides in over 200ms. Hover transitions run 120ms. All of it is off under
`prefers-reduced-motion`.

## Voice

Sentence case everywhere except micro-labels. Numbers carry units. Use operator
language (units, lots, promise dates, holds), not dashboard filler. Every page
names the goal it answers.
