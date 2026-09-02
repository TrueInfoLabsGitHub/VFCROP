# Veritas — Final Design Handoff Spec

The locked design: dark royal-blue "Scan Chamber" studio UI with state-driven garment
graphics and a runtime theme switcher. `veritas-prototype-reference.html` in this folder
is the living reference — open it in a browser; every measurement, animation and behavior
below can be verified there. `themes.css` is the drop-in token file.

## 1 · Theme system (runtime-switchable, persisted)

Six themes. The FOUR mandatory ones ship first-class; the two optional ones are included
in `themes.css` and cost nothing extra.

| id | Name | Type | Notes |
|---|---|---|---|
| `royal-navy` | Royal Navy | dark | **Default.** Luminous blue #5b9dff on navy-ink #0b1322 |
| `oled-ice` | OLED Ice | dark | True #000 bg, ice-blue accent, 56px hairline micro-grid, tighter glows |
| `satin-gold` | Satin Black & Gold | dark | Champagne gold #e6c34a on satin black, diagonal sheen layer |
| `half-white-purple` | Half-White & Purple | **light** | Off-white #f4f2f8 ground, royal purple #7c3aed, needs the light-theme correction rules |
| `emerald-night` | Emerald Night | dark | Optional |
| `teal-compare` | Teal Compare | dark | Optional; coral `--sus` marks the suspect twin |

Rules:
- Theme = `data-theme` attribute on `<html>`. Persist choice in `localStorage("veritas-theme")`;
  fall back to `royal-navy`.
- Components consume ONLY the CSS variables in `themes.css`. No hardcoded colors anywhere.
  Accent transparency is always `rgba(var(--acc-rgb), α)`.
- Switcher UI: pill of circular swatches (26px, 2px `--accl` ring + glow when active) —
  in Settings and/or a compact pill in the top bar. Switching repaints instantly (pure CSS vars).
- `--warn`/`--bad` are semantic (status) colors, separate from the accent; verdict chips and
  match bars use them, never the accent.

## 2 · Typography

| Role | Font | Sizes |
|---|---|---|
| UI / display | **Space Grotesk** (400–700), fallback `'Segoe UI', sans-serif` | page title 26–27px/700 · card title 16.5px/700 · body 15–15.5px · table rows 15.5px |
| Data / labels | **IBM Plex Mono** (400–500) | section labels 12px, letter-spacing .16em, uppercase, `--dim` · case numbers 13.5px `--accl` · captions 12.5px |

Minimum text size anywhere: 12px (11px only for tiny badges). KPI numbers 33px/700 with
`text-shadow: 0 0 28px rgba(var(--acc-rgb),.45)`.

## 3 · Surfaces & depth ("card3d" language)

- Card: `background: var(--sheen), <accent corner tint>, var(--glass)`; `border: 1px solid var(--bord)`;
  `border-radius: 16px` (glass panels 14px); `box-shadow: var(--shadow-card)`; `backdrop-filter: blur(14px)`.
- Hover (interactive cards): `translateY(-3px)`, border → `rgba(var(--acc-rgb),.5)`,
  add `0 0 34px rgba(var(--acc-rgb),.14)` glow. 220ms ease.
- App ground: `.app-root` in themes.css (base + radial halo + per-theme extra layers).
  Dashboard additionally gets a perspective floor-grid SVG and two ambient radial glows
  (see reference, `.dashbg`).
- Buttons: primary = `linear-gradient(135deg, var(--acc2), var(--acc))`, text `var(--bg)`,
  radius 11px, glow shadow; ghost = 1px `--bord` border, text `--mut`. Focus-visible:
  2px `--acc` outline, offset 2.
- Status chips: pill, IBM Plex Mono 12px — ok: `rgba(var(--acc-rgb),.1)` bg / `.35` border / `--accl` text;
  bad and warn use the same recipe with their semantic colors; neutral uses `--bord` + `--mut2`.

## 4 · State-driven garment graphics (the signature)

One shared SVG asset: the jacket wireframe (viewBox `0 0 200 220`, stroke-based,
paths in the reference — extract into `<GarmentWireframe stroke size />`). It appears in
five states; implement as small components, CSS-animation driven (Framer Motion optional):

| Where | State | Graphic | Spec |
|---|---|---|---|
| Dashboard "Live radar" card | monitoring | **Radar sweep**: 172px circle, conic-gradient sweep `rgba(var(--acc-rgb),.45)` rotating 4s linear; 2 amber blips pulsing; 88px garment centered | click → Analysis |
| Analysis chamber | idle | **Turntable**: garment 300×330 inside 3 orbit ellipses (480×418 svg, middle ring solid, outer dashed); garment wrapper `perspective:700px` + `rotateY 360°/8s linear`; horizontal scan line sweeps top↔bottom 5s ease-in-out (`--acc` gradient bar + glow) | click chamber → add photos |
| Analysis chamber | photos added | **Particle assembly**: stop rotation; garment paths get `stroke-dasharray:1 9; stroke-linecap:round;` `stroke-dashoffset` animating −40 loop 2.6s (flowing dots) | |
| Run overlay | processing | **Laser sweep**: 150×160 garment + triangulated mesh lines at `rgba(var(--acc-rgb),.3)`; vertical beam (2.5px, glow) sweeping left↔right 2.4s; progress bar + 4-line mono log appearing at 650ms intervals; total ~3.2s then route to Results | |
| Results exam scene | verdict | **X-ray + callouts** (section 5) | |

`prefers-reduced-motion: reduce` disables all of the above animations (static frames remain).

## 5 · Results "forensic exam" scene (820×470 stage, centered)

- Suspect garment 260×286, stroke `--bad`, drop-shadow `0 0 30px rgba(242,139,130,.3)`,
  standing on a glowing elliptical **podium** (radial `rgba(var(--acc-rgb),.22)` + 240px ring)
  with two vertical **light shafts** behind.
- Authentic reference as **ghost overlay**: same garment, stroke `--acc`, opacity .32,
  offset ~16px right / −10px up (the misalignment is intentional and must remain).
- 3 pulsing **heat blobs** (radial gradients, 40–54px, blur 2px, 2.6s pulse, staggered delays)
  under 3 numbered **hotspot** rings (30px, `--bad`×2 + `--warn`×1).
- 3 floating **callout cards** (212px, glass, bob ±5px 4.5s alternate, staggered) connected by
  1.4px leader polylines with endpoint dots; each card: severity tag → title → one-line finding →
  REF vs SUSPECT mini-swatch pair (stitch-density pattern / typeface sample / letter-spacing sample).
- Chain-of-custody pill top-left: lock icon + `CHAIN OF CUSTODY · SHA-256 …· EVIDENCE LOCKED`.
- Right panel: score ring (r=50, stroke-width 9, dasharray animates 0→score/100·314 over 1.2s,
  number counts up), verdict chip, confidence, findings list (severity tags), then
  **Confirm verdict** / **Escalate to legal** ghost buttons, **Export findings report** primary.

## 6 · Screens & flow

Dashboard (KPI tiles w/ sparklines + score ring · recent-cases table w/ match bars + verdict-mix
footer strip · live radar card · engine card) → Cases (filter chip-tabs, bulk checkboxes, sortable
Case Nº/Submitted, Export CSV, row click routes by status) → Analysis (chamber + case № input +
optional UPC + engine + reference + assignee/priority + readiness checklist gating **Run analysis**;
2-of-3 counter) → Run overlay → Results → Reports (list + PDF-style preview card) → Models
(engine cards w/ benchmark metrics + set-default, usage quota, calibration).

Enterprise chrome on every page: top bar (logo, scene label mono, nav w/ accent underline,
⌘K search pill, notification bell + badge, LIVE chip, tenant chip, avatar) and bottom status bar
(version · tenant · data region · SOC 2/ISO · operational dot + last sync).

## 7 · States, edge cases, a11y

- Readiness rows: circle → filled check + `--accl` text; optional row uses dashed circle.
- Run button disabled until case № (≥3 chars) AND ≥1 photo; disabled = grayscale .45 opacity.
- Empty tables: keep header, show mono `--dim` empty line; loading: reuse particle garment or bar shimmer.
- Toasts: bottom-center glass pill, accent border, 2.6s, slide+fade 250ms.
- Keyboard: all interactive elements focusable with visible `--acc` outline; hit targets ≥ 36px
  (44px for primary actions). Contrast: body text ≥ 7:1 on dark themes, ≥ 4.5:1 for `--mut2`;
  the light theme's correction rules in themes.css are mandatory.
- Breakpoints: desktop-first; <1100px → KPI grid 2-col, rows wrap, side panel 300px;
  tables scroll in their own `overflow-x:auto` container.

## 8 · Sample data

All numbers/case IDs/findings in the reference are SAMPLE data — wire to the real API/mock
layer during implementation; keep formats (`VF-2026-####`, score /100, SHA-256 short hash).
