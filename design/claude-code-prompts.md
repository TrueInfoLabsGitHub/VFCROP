# Claude Code — Implementation Prompts for the Veritas Final Design

## Setup (do once)
Copy these three files into your repo root (e.g. `design/`):
- `veritas-theme-spec.md` — the full spec
- `themes.css` — the finished theme tokens (drop-in)
- `veritas-prototype-reference.html` — the working reference (open it in a browser next to your app while implementing)

Then run the prompts below in order, one per Claude Code session/task. Each is self-contained —
paste it as written. Commit between phases.

---

## Prompt 1 — Theme engine
```
Read design/veritas-theme-spec.md (section 1) and design/themes.css.

Implement the theme system in this React + TypeScript + Tailwind app:
1. Add design/themes.css to the global styles, imported before Tailwind utilities.
2. Create a ThemeProvider (context) that sets data-theme on <html>, persists the choice
   to localStorage key "veritas-theme", and defaults to "royal-navy". Themes:
   royal-navy, oled-ice, satin-gold, half-white-purple (light), emerald-night, teal-compare.
   The first four are mandatory and must appear in the picker; the last two are optional extras.
3. Build a ThemeSwitcher component: a pill of 26px circular gradient swatches (one per theme,
   tooltip with the theme name, active swatch gets a 2px var(--accl) ring + glow), rendered in
   the top bar. Switching must repaint instantly with no re-mount.
4. Wrap the app shell in a .app-root element per themes.css so the background halo renders.
Do not restyle components yet. Verify: toggling themes changes the whole palette live and
survives reload.
```

## Prompt 2 — Token sweep (kill hardcoded colors)
```
Read design/veritas-theme-spec.md sections 2–3 and open design/veritas-prototype-reference.html
for visual truth.

Sweep the entire codebase and replace every hardcoded color (hex, rgb, Tailwind color classes
like bg-slate-900/text-blue-400) with the CSS variables from design/themes.css, via Tailwind
arbitrary values (e.g. bg-[var(--glass)] text-[var(--text)] border-[var(--bord)]) or small
utility classes. Rules:
- Accent transparency: rgba(var(--acc-rgb), <alpha>).
- Status colors (verdict chips, match bars, alerts) use --warn/--bad, never the accent.
- Load Google Fonts Space Grotesk (400–700) and IBM Plex Mono (400–500); apply per the
  typography table in the spec. No text below 12px.
- Implement the shared surface recipe: card3d (sheen gradient + var(--glass), 1px var(--bord),
  radius 16, var(--shadow-card), hover translateY(-3px) + accent border/glow, 220ms ease),
  primary/ghost buttons, status chips, section labels — exactly as the spec defines.
- Add the light-theme correction classes (track, ring-track, overlay-scrim) where progress
  troughs, ring backgrounds and overlay scrims occur.
Verify every page in all six themes, especially half-white-purple (light) for contrast.
```

## Prompt 3 — Garment graphics system
```
Read design/veritas-theme-spec.md section 4. Extract the jacket wireframe SVG paths from
design/veritas-prototype-reference.html (viewBox 0 0 200 220) into a reusable
<GarmentWireframe stroke size mesh? /> component.

Build the five state graphics as components (CSS keyframe animations; respect
prefers-reduced-motion by disabling all of them):
1. <RadarCard> — dashboard: 172px radar with rotating conic sweep, two pulsing amber blips,
   88px garment centered; whole card clicks through to Analysis.
2. <ScanChamber state="idle|assembling"> — analysis: 300px garment inside three orbit
   ellipses + horizontal scan line (5s ease-in-out). idle = rotateY turntable (8s,
   perspective 700px). assembling = rotation stops, garment paths become flowing dots
   (stroke-dasharray 1 9, dashoffset loop 2.6s). Switch to assembling when the first
   photo is added.
3. <LaserRunOverlay> — the run modal: 150px garment with faint mesh triangles and a vertical
   sweeping laser beam, progress bar, and a 4-line mono log appearing at ~650ms intervals,
   then route to Results.
4. Results exam scene per spec section 5: podium + light shafts, suspect garment (--bad) with
   pulsing heat blobs + numbered hotspots, ghost reference overlay (opacity .32, offset),
   three bobbing callout cards with leader lines and REF-vs-SUSPECT mini swatches,
   chain-of-custody pill, and the animated score ring (dasharray + count-up, 1.2s).
Match the reference prototype pixel-for-pixel where possible.
```

## Prompt 4 — Enterprise chrome & flows
```
Read design/veritas-theme-spec.md sections 6–7.

1. Top bar on every page: logo + product name with mono scene label, nav with accent underline,
   ⌘K search pill (opens the command palette or a stub), notification bell with count badge,
   LIVE engine chip, tenant chip, avatar, ThemeSwitcher.
2. Bottom status bar on every page: version · tenant · data region · SOC 2 / ISO 27001 ·
   operational dot + last-sync (wire to real values where available).
3. Cases: filter chip-tabs with counts, bulk-select checkboxes, sortable Case Nº / Submitted,
   Export CSV; row click routes by status (scanning → Analysis, else → Results).
4. Analysis: readiness checklist gates Run analysis (case number ≥3 chars AND ≥1 photo);
   assignee + priority chips; audit-logged note.
5. Results: Confirm verdict / Escalate to legal actions (record to audit trail or stub),
   Export findings report.
6. Toasts: bottom-center glass pill, accent border, 2.6s.
Keep all existing business logic; this phase is chrome + flow wiring only.
```

## Prompt 5 — QA pass
```
Using design/veritas-theme-spec.md section 7 as the checklist, audit the app:
- All six themes on every page: no hardcoded colors left (grep for #hex and Tailwind palette
  classes), light theme fully legible, progress troughs/ring tracks/overlays corrected.
- prefers-reduced-motion disables every animation; nothing relies on motion to be understood.
- Keyboard: tab order sane, all interactive elements have the 2px var(--acc) focus outline,
  hit targets ≥36px.
- Tables scroll horizontally in their own container below 1100px; KPI grid drops to 2 columns.
- Empty/loading/error states exist for every list and the analysis flow.
Fix everything you find and list what changed.
```
