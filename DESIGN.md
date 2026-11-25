# Design

## The runtime, which constrains everything below

CEP panels in Premiere 2026 run **Chromium 99** (`libcef.dll` reports
`99.2.15+chromium-99.0.4844.84`). That is the browser this CSS has to satisfy, not the one
on your desktop.

It cost a full rebuild to learn: the first version of this panel used `oklch()` and
`color-mix()`, both of which need Chrome 111. Chromium 99 discards those declarations
entirely, so every colour fell away and the panel rendered as unstyled black text on black.
Layout survived, which is the tell: if structure is right but nothing has colour, suspect an
unsupported colour function rather than a broken stylesheet.

**Do not use** in this panel: `oklch()`, `lab()`, `lch()`, `color-mix()`, `:has()`,
container queries, `@layer`, subgrid. Colours are plain hex. Blends are precomputed.

Safe and used here: custom properties, flexbox with `gap`, `:focus-visible`,
`font-variant-numeric`, CSS animations, `aspect-ratio`.

## Theme, and why

The scene: an editor inside Premiere's dark chrome, often late, with graded footage on the
same screen a few centimetres away.

That forces dark. Not because tools look good dark, but because a bright panel docked beside
a video being colour-judged both clashes with the host UI and skews the read of the image.
The panel is a guest in someone else's window.

## Colour

Sampled from the show's own logo rather than reached for. The badge is cream, gold, forest
green and olive. These were designed in OKLCH for even lightness steps, then converted to
hex because of the runtime above:

| role | hex | from |
|---|---|---|
| `--bg` | `#111511` | forest `#203020`, darkened |
| `--surface` | `#1A201A` | one step up |
| `--raised` | `#242B24` | inputs, hovered rows |
| `--line` | `#323B32` | borders |
| `--text` | `#EBE7DF` | cream `#E0D0B0`, lifted |
| `--dim` | `#9A998F` | secondary copy |
| `--faint` | `#696A62` | hints, inert states |
| `--accent` | `#D5A13C` | gold `#C09030` |
| `--good` | `#8CB460` | olive `#809030` |
| `--warn` | `#ED9D44` | warmer gold |
| `--bad` | `#D55753` | the one hue not in the logo, reserved for failure |

Precomputed blends stand in for `color-mix()`: `--accent-wash #2B2C1D`,
`--good-edge #647E4B`, `--good-wash #222A1F`.

**Strategy: restrained.** Tinted neutrals plus one accent, gold, held under 10% of surface.
Every neutral carries chroma 0.012–0.018 toward the forest hue, so nothing is flat grey and
nothing is `#000` or `#fff`.

This is a deliberate refusal of the category reflex. "Video tool" trains you toward blue
(the previous panel used `#4a90d9`) or toward Premiere's own violet. Neither says anything.
The show's palette does.

## Type

System stack: `Segoe UI, -apple-system, sans-serif`. Panels are not the place for a webfont.

| step | size | weight | use |
|---|---|---|---|
| step label | 10px | 600, `0.08em` tracking, uppercase | rail |
| micro | 11px | 400 | metadata, hints |
| body | 12.5px | 400 | default |
| title | 15px | 600 | step heading |
| figure | 20px | 600, tabular | counts, durations |

Ratio between body and title is 1.2; between body and figure, 1.6. Numbers use
`font-variant-numeric: tabular-nums` so columns of timecodes do not jitter.

Body copy caps at 68ch.

## Layout

A step rail, 68px wide, fixed. The workspace fills the rest and shows one step.

Spacing is deliberately uneven: 16px inside a group, 24px between groups, 32px above a
primary action. Uniform padding reads as a form; varied padding reads as structure.

Containers only where a boundary means something. A list of moments is a list, not ten
cards. The one place a card earns itself is the moment under review, because it genuinely
is a discrete object being judged.

## Components

- **Rail step**: number, label, state dot. Current step gets a gold dot and full-strength
  text; completed steps olive; future steps `--dim`.
- **Row** (moments): full 1px border, tinted background when selected. Status is a leading
  dot plus the border, never a side stripe.
- **Field**: label above, control below, hint under. Hints are always present for anything
  non-obvious, since this tool has to explain itself.
- **Primary action**: gold, one per step. Everything else is a bordered ghost button.

## Motion

Transform and opacity only, never layout properties. `cubic-bezier(0.22, 1, 0.36, 1)`
(ease-out-quint), 160ms for state, 240ms for step changes. Nothing bounces.

## Bans

Enforced, not aspirational:

- No side-stripe borders. The previous panel used `border-left: 3px solid` for status on
  every reel row. Status is a full border plus a tint plus a leading dot.
- No gradient text, no glassmorphism, no hero-metric tiles, no identical card grids.
- No modals. Everything happens in place.
- No em dashes in interface copy.
