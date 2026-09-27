<p align="center"><img src="../design/logo/restic-fleet-logo.svg" alt="restic-fleet" width="300"></p>

# restic-fleet design system

The dashboard is a tool people open when something might be wrong. Every decision below serves
one job: **show what needs attention first, and make healthy look calm.**

The source of truth is [`roles/dashboard/files/static/app.css`](../roles/dashboard/files/static/app.css):
tokens at the top, components below. Components use tokens only; never a raw colour.

## Logo

| File | Use |
|---|---|
| [`design/logo/restic-fleet-mark.svg`](../design/logo/restic-fleet-mark.svg) | the mark on light backgrounds |
| [`design/logo/restic-fleet-logo.svg`](../design/logo/restic-fleet-logo.svg) | mark + wordmark; adapts to light and dark |
| [`design/logo/restic-fleet-favicon.svg`](../design/logo/restic-fleet-favicon.svg) | app icon / favicon (ink tile, legible at 16 px) |

**The dial.** Six segments form a ring: the backup schedule, one snapshot after another. The newest
segment is lit in the accent colour. In the middle sits the vault, a solid core with a slot. The
ring is the fleet's routine; the core is where everything ends up.

- Keep clear space around the mark of at least a quarter of its width.
- Don't recolour the segments individually, rotate the mark, or add effects.
- The wordmark is `restic` regular + `fleet` bold, set in the system UI face, one word.
- restic-fleet is a community project built on restic; don't use the logo to imply it is made by the
  restic authors.

## Colour

Neutrals are cool greys biased toward the accent. Status colours are **semantic only**: they are never
used for decoration, and the accent is never used to mean "good".

| Token | Light | Dark | Use |
|---|---|---|---|
| `--rf-ground` | `#F4F6FA` | `#0B1220` | page background |
| `--rf-surface` | `#FFFFFF` | `#111A2B` | cards, tables, drawer head |
| `--rf-surface-2` | `#EEF1F7` | `#17223A` | inset fields, table headers |
| `--rf-ink` | `#0F1B2A` | `#E6EBF5` | primary text |
| `--rf-muted` | `#5A667D` | `#93A0B8` | secondary text, labels |
| `--rf-line` | `#DCE2EC` | `#22304A` | borders, dividers |
| `--rf-accent` | `#3350D8` | `#8FA2FF` | brand, primary action, "backing up" |
| `--rf-ok` | `#13804B` | `#4CC38A` | healthy, succeeded |
| `--rf-warn` | `#9A5B00` | `#F0B45A` | warning, overdue |
| `--rf-fail` | `#C4312B` | `#FF7A70` | failed |
| `--rf-idle` | `#8C97AB` | `#6F7C94` | no data yet |

Each status colour has a `-soft` background for pills and alerts. Text on its soft background meets
WCAG AA in both themes. The theme follows the operating system (`prefers-color-scheme`).

## Status

Status is always shown as **icon + word + colour**, never colour alone, so it survives colour
blindness, greyscale screenshots and bad projectors.

| Status | Icon | Word | Meaning |
|---|---|---|---|
| `failed` | × | Failed | the latest run of any job failed |
| `overdue` | clock | Overdue | no success within 1.5 × the expected interval |
| `warning` | triangle | Warning | finished, but some files could not be read |
| `running` | spinning arc | Backing up | a job is running now |
| `never` | dash | No backup yet | nothing reported yet |
| `ok` | check | Healthy | everything above is false |

Machines are always **sorted by this order**, so problems rise to the top.

## Type

System fonts only, on purpose: the dashboard runs on your own server, often without internet access,
under a strict Content-Security-Policy. No web fonts, no external requests.

- UI: `ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, …`
- Code, snapshot IDs, job names: `ui-monospace, "SF Mono", "Cascadia Code", …`
- Numbers always use tabular figures (`font-variant-numeric: tabular-nums`) so columns line up.

| Token | Size | Use |
|---|---|---|
| `--rf-text-xs` | 12 px | labels (uppercase, +0.06em tracking), pills |
| `--rf-text-sm` | 13 px | secondary text, tables |
| `--rf-text-md` | 14 px | body |
| `--rf-text-lg` | 16 px | card titles, section titles |
| `--rf-text-xl` | 20 px | page verdict, drawer title |
| `--rf-text-2xl` | 28 px | headline figures |

## Space, shape, depth

- 4 px grid: `--rf-1` (4) … `--rf-7` (48). Lay out with `gap`, not margins.
- Radius: 6 px controls, 10 px inner blocks, 14 px cards and panels, pills fully round.
- Borders separate things; shadows are reserved for things that float (the drawer).

## Components

- **Top bar**: logo, fleet name, live indicator (green dot, or amber "Connection lost · showing last
  data"), sign out.
- **Verdict**: one sentence that answers "are we OK?", with the worst status's icon.
- **Stats**: four figures with a caption each. A figure turns red only when it needs action.
- **Machine card**: name, schedule and last success, status pill, three facts (stored, snapshots,
  last run), job chips with a status dot, 30-day history (one bar per day, worst result wins, today
  outlined), integrity-check footer. Cards needing attention get a tinted border.
- **Drawer**: opens from the right with details, per-job results, recent runs, and copyable restore
  commands for that machine.
- **Tables**: uppercase labels, right-aligned numbers, messages wrap; wide tables scroll inside their
  own container, never the page.
- **Empty states** say what will appear and how to make it appear.

## Voice

Write from the operator's side: "Backs up daily", "Integrity check passed 2 days ago",
"Connection lost · showing last data". Say what happened and what to do. No exclamation marks, no
apologies, no internal jargon on the screen (say "machine", not "client"; "stored", not "repo size").

## Accessibility

- Keyboard: every card is a button; `/` focuses search; `Esc` closes the drawer; focus is always visible.
- `prefers-reduced-motion` disables the spinner and drawer animation.
- The verdict region is `aria-live="polite"`, so screen readers hear when the fleet's state changes.
