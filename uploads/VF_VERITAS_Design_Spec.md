# VF VERITAS — Design Specification for Claude Design

**Product:** VF VERITAS — Counterfeit Intelligence Platform
**Audience:** VF Corporation Legal & Brand Protection (Specialists, Managers, Admins)
**Purpose of this doc:** A complete, build-ready design brief covering design tokens, component library, and every screen across all 10 epics (including future-enhancement epics). Hand this directly to Claude Design.
**Status:** v1.0 — CONFIDENTIAL

> **VF brand note:** This spec is anchored to the Heritage Blue palette from the VERITAS product backlog. VF Corporation maintains an internal Brand Identity Guide (logo, color palette, typography, photography, brand expression, verbal framework). The exact VF hex values, Pantone codes, and licensed typefaces live in VF's brand portal and are **not** publicly published. Every token below that must be reconciled with the official VF system is flagged **`[CONFIRM WITH VF BRAND]`**. Swap those values in without restructuring anything else.

---

## 0. Design Principles (read first)

This is a **forensic working tool**, not a marketing site. Every design decision serves a Brand Protection Specialist who looks at 30–50 suspected-counterfeit cases a day and must reach a defensible, litigation-ready verdict. The design priorities, in order:

1. **Evidence clarity over decoration.** The product image and its annotations are the hero on every analysis screen. Chrome recedes; evidence dominates.
2. **Decision confidence.** Color-coded scores, match/no-match states, and confidence badges must be unambiguous at a glance and never rely on color alone (WCAG AA).
3. **Data density without clutter.** Specialists scan tables and comparison grids fast. Prefer compact, scannable rows over airy cards where data is the point.
4. **Human override is sacred.** The AI proposes; the human decides. Every AI assessment screen has a visible, dignified override path — never buried.
5. **Chain-of-evidence trust.** Timestamps, author attribution, hashes, and audit trails are first-class UI, because the output may end up in court.

**One signature element:** the **Counterfeit Probability Gauge** — a circular score ring (green→amber→red) that appears on the Case Queue, the hero analysis header, and the Summary. It is the visual spine of the product. Spend the boldness here; keep everything else quiet.

---

## 1. Design Tokens

### 1.1 Color

#### Brand / Core
| Token | Hex | Use | Flag |
|---|---|---|---|
| `--vf-heritage-blue` | `#1F4E96` | Primary brand, headers, primary buttons, active states | `[CONFIRM WITH VF BRAND]` |
| `--vf-heritage-blue-deep` | `#163A70` | Hover on primary, header bars | `[CONFIRM WITH VF BRAND]` |
| `--vf-heritage-blue-tint` | `#EDF1F7` | Selected rows, subtle fills, alternating table rows | derived |
| `--vf-ink` | `#1A2B3C` | Primary text on light | |
| `--vf-slate` | `#5A6B82` | Secondary text, labels | |
| `--vf-mist` | `#8A9BB5` | Tertiary text, disabled, captions | |

#### Surfaces
| Token | Hex | Use |
|---|---|---|
| `--surface-app` | `#F7F9FC` | App background |
| `--surface-card` | `#FFFFFF` | Cards, tables, panels |
| `--surface-raised` | `#FFFFFF` + shadow | Modals, popovers, dropdowns |
| `--border-subtle` | `#E2E8F1` | Card borders, dividers |
| `--border-strong` | `#C7D2E0` | Table cell borders, input borders |

#### Semantic / Status (the verdict palette — used everywhere)
| Token | Hex | Meaning |
|---|---|---|
| `--status-authentic` | `#2E7D32` | Likely Authentic · Match · Score 0–30 · Pass |
| `--status-authentic-bg` | `#E8F3E9` | Authentic card/badge fill |
| `--status-caution` | `#C99A00` | Inconclusive · Mismatch · Score 31–60 · Uncertain |
| `--status-caution-bg` | `#FBF3DC` | Caution card/badge fill |
| `--status-counterfeit` | `#C0392B` | Suspected/Confirmed Counterfeit · No Match · Score 61–100 · Deviation |
| `--status-counterfeit-bg` | `#FBE9E7` | Counterfeit card/badge fill |
| `--status-neutral` | `#5A6B82` | Not yet assessed · N/A · gray dash |

#### Brand-of-record accents (for filtering/labeling VF-owned brands in cases)
| Token | Hex | Brand | Flag |
|---|---|---|---|
| `--brand-tnf` | `#000000` | The North Face | `[CONFIRM WITH VF BRAND]` |
| `--brand-vans` | `#C8102E` | Vans | `[CONFIRM WITH VF BRAND]` |
| `--brand-timberland` | `#7B5530` | Timberland | `[CONFIRM WITH VF BRAND]` |

> Status colors are paired with an **icon + text label** in every instance (e.g. a red triangle + "No Match"). Never communicate a verdict with color alone.

### 1.2 Typography

| Role | Family | Fallback | Notes | Flag |
|---|---|---|---|---|
| Display / H1–H2 | VF brand display face | `"Inter", system-ui, sans-serif` | VF uses "a bolder typeface" for headlines | `[CONFIRM WITH VF BRAND]` |
| Body / UI | VF brand sans | `"Inter", system-ui, sans-serif` | Default for all interface text | `[CONFIRM WITH VF BRAND]` |
| Data / Mono | `"IBM Plex Mono"` | `ui-monospace, monospace` | UPC codes, security tags, hashes, case IDs | |

**Type scale (rem, 16px base):**
| Token | Size | Line | Weight | Use |
|---|---|---|---|---|
| `text-display` | 2.0 / 32px | 1.2 | 700 | Page titles |
| `text-h1` | 1.5 / 24px | 1.25 | 700 | Section headers |
| `text-h2` | 1.25 / 20px | 1.3 | 600 | Tab/panel headers |
| `text-h3` | 1.0625 / 17px | 1.4 | 600 | Card titles |
| `text-body` | 0.9375 / 15px | 1.5 | 400 | Default body |
| `text-sm` | 0.8125 / 13px | 1.45 | 400 | Table cells, secondary |
| `text-xs` | 0.6875 / 11px | 1.4 | 500 | Labels, eyebrows, badges (uppercase, +0.04em tracking) |
| `text-data` | 1.0 / 16px | 1.4 | 500 | Monospace codes/scores |

### 1.3 Spacing, Radius, Elevation, Motion

**Spacing scale (4px base):** `4, 8, 12, 16, 20, 24, 32, 40, 48, 64`
Tokens: `space-1`=4 … `space-16`=64. All gaps, padding, margins are multiples of 4.

**Radius:** `radius-sm`=4px (inputs, badges) · `radius-md`=8px (cards, buttons) · `radius-lg`=12px (modals, panels) · `radius-full`=9999px (pills, gauge).

**Elevation:**
- `shadow-sm`: `0 1px 2px rgba(26,43,60,.06)` — cards
- `shadow-md`: `0 4px 12px rgba(26,43,60,.10)` — dropdowns, popovers
- `shadow-lg`: `0 12px 32px rgba(26,43,60,.16)` — modals

**Motion:** durations `120ms` (micro), `200ms` (default), `320ms` (panel/overlay). Easing `cubic-bezier(.2,.0,.0,1)`. Respect `prefers-reduced-motion` — disable non-essential transitions, keep instant state changes.

**Grid:** 12-column, max content width `1440px`, gutter `24px`. App uses a fixed left nav (240px) + fluid content area.

---

## 2. Component Library

Every screen is assembled from these. Build them as reusable components first.

### 2.1 Counterfeit Probability Gauge `<ScoreGauge>` — SIGNATURE
Circular progress ring. Props: `score` (0–100), `size` (sm 48px / md 80px / lg 120px), `showLabel`.
- Track: `--border-subtle`. Arc color by band: 0–30 authentic green, 31–60 caution amber, 61–100 counterfeit red.
- Center: score number in `text-data`, weight 700; tiny "/100" beneath in `--vf-mist`.
- Unassessed state: gray dashed track, "—" in center.
- Animate arc sweep on mount (200ms, reduced-motion → instant).

### 2.2 Status Badge `<StatusPill>`
Pill with icon + label. Variants: `authentic` (check), `caution` (alert-triangle), `counterfeit` (x-octagon), `neutral` (dash). Background = `*-bg`, text/icon = solid status color. Used for case status, match results, dimension pass/fail.

### 2.3 Case Status Tag `<CaseStatusTag>`
Specific to pipeline status: `New` (blue), `In Review` (amber), `Authenticated` (green), `Enforcement` (red), `Closed` (slate). Solid-fill small pill, `text-xs` uppercase.

### 2.4 Pipeline Progress `<PipelineBar>`
6-segment horizontal bar showing the 6 case stages. Completed = Heritage Blue fill; current = pulsing Heritage Blue outline; upcoming = `--border-subtle`. Current stage name in `text-xs` below. Compact variant (no label) for table rows.

### 2.5 Data Table `<DataTable>`
Dense, sortable, filterable. Sticky header row in `--vf-heritage-blue` with white text. Zebra striping via `--vf-heritage-blue-tint` on odd rows. Row hover = subtle tint + pointer. Sort indicators (▲▼) on click. Checkbox column (optional) for bulk select. 25 rows/page default. Includes empty state slot.

### 2.6 Filter Bar `<FilterBar>`
Horizontal row of dropdown filters + search input (debounced 300ms). Active filters render as removable chips below. "Clear filters" link. Filters combine with AND.

### 2.7 Override Panel `<OverridePanel>` — repeated across all analysis tabs
Collapsible panel anchored bottom of each analysis tab. Toggle to reveal: a dropdown (`Confirmed Match` / `Confirmed No Match` / `Requires Physical Verification`), a notes textarea, and a Save button. On save → writes to Casemates, shows toast, stamps the human decision over the AI's. Visually distinct (left border in Heritage Blue) so it reads as "your call."

### 2.8 Image Comparison Viewer `<CompareViewer>`
Split pane: left = seized (header tinted `--status-counterfeit-bg`, label "Seized"), right = authentic reference (header tinted `--status-authentic-bg`, label "Authentic Reference"). Synchronized zoom + pan. Toolbar: zoom in/out, reset, toggle annotations, toggle overlay-diff. Single-pane fallback on tablet.

### 2.9 Annotated Image `<AnnotatedImage>`
Product photo with overlaid bounding boxes from the Vision API. Box color = pass/caution/deviation. Click box → opens linked detail card with the AI's reasoning. Toolbar toggles annotations on/off.

### 2.10 Dimension Score Card `<DimensionCard>`
Compact card: 48px `<ScoreGauge>`, dimension name, one-line key finding. Expandable to reveal full Vision API reasoning linked to bounding boxes. Used in a horizontal scroll row of 6 on Construction Analysis.

### 2.11 KPI Card `<KpiCard>`
Big value (`text-display`), label, and trend indicator (arrow + % vs prior period; green up / red down depending on metric polarity). Dashboard only.

### 2.12 Match Result Card `<MatchCard>`
Verdict card for UPC/Security-tag/Supplier lookups. Three states: Match (green), No Match — Counterfeit Indicator (red), Mismatch warning (amber, shows "belongs to Product A but appears to be Product B"). Shows matched product details + reference thumbnail when matched.

### 2.13 Supporting
- `<Button>` — primary (Heritage Blue), secondary (outline), ghost, destructive (red). Same verb through the whole flow (Publish→Published).
- `<Tabs>` — horizontal; active tab Heritage Blue + bottom border; URL-synced.
- `<Toast>` — top-right; success/error/warning/info; auto-dismiss 5s (errors persist).
- `<ConfirmModal>` — destructive-action confirmation; red styling; describes consequence.
- `<Skeleton>` — shimmer loaders matching content layout.
- `<EmptyState>` — icon + plain-language message + primary action.
- `<MapView>` — markers + dashed path; used in Origin tabs/screens.
- `<Timeline>` — vertical event log; used for enforcement tracker + audit trail.

---

## 3. App Shell & Navigation

```
┌─────────────────────────────────────────────────────────────┐
│ TOPBAR: VF VERITAS wordmark · global search · user/role menu │
├──────────┬──────────────────────────────────────────────────┤
│ LEFT NAV │  CONTENT AREA (max 1440px, fluid)                 │
│ 240px    │                                                   │
│          │                                                   │
│ Dashboard│                                                   │
│ Cases    │                                                   │
│ Origin   │                                                   │
│ Evidence │                                                   │
│ Admin    │                                                   │
│          │                                                   │
│ ─────    │                                                   │
│ role tag │                                                   │
└──────────┴──────────────────────────────────────────────────┘
```
- Left nav items gated by role: Specialists see Dashboard/Cases/Origin/Evidence; Managers add approval surfaces; Admin adds user management.
- Persistent breadcrumb under topbar on detail screens (e.g. `Cases / VF-2026-0412 / Authentication Analysis`).
- Running confidentiality marker in the topbar right: "CONFIDENTIAL".

---

## 4. Screen-by-Screen Specifications

> Format per screen: **purpose · layout (ASCII) · components · states · stories covered.**

### EPIC 0 — Authentication & Access Control

#### 0.1 Loading / Splash (E0-05)
Centered VF VERITAS wordmark + Heritage Blue spinner on `--surface-app`. Transitions to Dashboard within ~3s.

#### 0.2 Sign In (E0-01, E0-02, E0-03)
```
            ┌───────────────────────────┐
            │     VF VERITAS            │
            │  Counterfeit Intelligence │
            │                           │
            │  [ Sign in with VF SSO ]  │  ← primary, full width
            │                           │
            │  error slot (red, inline) │
            └───────────────────────────┘
```
- Single SSO button → OAuth2 to VF IdP. Failed auth → inline red error. No password fields (SSO only).
- Session: JWT, 8h, auto-refresh; expiry → redirect here. Sign-out clears token; back button must not restore.

#### 0.3 Admin — User & Role Management (E0-04, E0-06)
`<DataTable>` of users: Name, Email, AD Group, Role (Specialist/Manager/Admin), Last Active. Role assigned via dropdown (sourced from AD group). Admin-only route.

---

### EPIC 1 — Dashboard *(future enhancement)*

#### 1.1 Executive Dashboard (E1-01 → E1-08)
```
┌───────────── FilterBar: [Date ▾][Brand ▾]  ........ [Export PDF] ┐
├──────────┬──────────┬──────────┬──────────┐
│ KPI Cases│ KPI Avg  │ KPI Enf. │ KPI Rev. │   ← 4× <KpiCard> w/ trend
│ This Mo. │ Resolve  │ Actions  │ Protected│
├──────────┴──────────┴────┬─────┴──────────┤
│ 12-mo Case Volume (line, │  Brand Split   │
│ one line per brand)      │  (donut)       │
├──────────────────────────┴────────────────┤
│ Geographic Seizure Heat Map (world)        │
├────────────────────────────────────────────┤
│ Top 5 Counterfeit Sources  (table)         │
└────────────────────────────────────────────┘
```
- Date filter: 7d/30d/90d/12m/custom (default 30d). Brand filter: multi-select chips ("All Brands" default).
- Line chart: one line per brand (TNF/Vans/Timberland), tooltip on hover. Donut: click segment → filter Case Queue. Heat map: hover = country + count; click → filtered queue. Top-5 table: Source, Cases, Last Seen, Trend; click → Origin Intelligence.
- Export PDF: snapshot of current dashboard state.

---

### EPIC 2 — Case Queue

#### 2.1 Case Queue (E2-01 → E2-12)
```
┌ Cases ........................................ [+ New Case] ┐
├ FilterBar: search · [Brand▾][Status▾][Stage▾][Date▾][Assignee▾]
├─────────────────────────────────────────────────────────────┤
│ ☐ │Case ID│Brand│Source│Date│Assigned│Stage(bar)│Score│Status│
│ ☐ │VF-..  │ TNF │ CN   │... │ J.Doe  │▮▮▮▯▯▯    │ 78● │Review│
│ ... 25 rows, zebra, sortable, row-click → Analysis ...      │
├─────────────────────────────────────────────────────────────┤
│ Showing 1–25 of N      [‹ Prev]  1 2 3 …  [Next ›]          │
└─────────────────────────────────────────────────────────────┘
```
- Columns: Case ID, Brand (accent dot), Source, Date, Assigned To, Stage (`<PipelineBar>` compact), Score (`<ScoreGauge>` sm, color-coded), Status (`<CaseStatusTag>`).
- Search debounced 300ms across ID/brand/location. Filters AND-combine, show as removable chips. Sort any column (asc→desc→reset).
- Row / Case-ID click → `/cases/:id/analysis`. Bulk select → action bar (Assign To, Escalate, Export CSV). Pagination 25/page.
- Empty state: magnifier icon, "No cases match your filters.", "Clear filters" button.

---

### EPIC 3 — Case Intake

#### 3.1 Case Intake (E3-01)
Cases arrive as **JSON + attachments** delivered by the RPA bot (not a manual form). Provide a minimal **intake monitor** view: a list/log of incoming payloads with parse status (Received → Metadata Extracted → Loaded into Analysis), the extracted fields preview (security tag, UPC, style, construction, origin), and any parse errors with retry. `+ New Case` from the queue routes here. Primary actor is the bot; this screen is for humans to observe/troubleshoot ingestion.

---

### EPIC 4 — Authentication Analysis *(HERO SCREEN)*

The core workspace. Header + 5 tabs. (Spec backlog also defines a Security Tag section, 4F.)

#### 4.0 Shell (4A · E4-01 → E4-03)
```
┌ ‹ Back   VF-2026-0412   [TNF]   [In Review]        ( 78 ) ┐ ← header + lg ScoreGauge
├ Tabs: UPC Validation | Style Matching | Construction |     │
│        Origin Intelligence | Security Tag | Summary        │
├────────────────────────────────────────────────────────────┤
│  ……… active tab content ………                                │
└────────────────────────────────────────────────────────────┘
```
- Header: back arrow → queue, Case ID, Brand badge (brand accent), `<CaseStatusTag>`, large `<ScoreGauge>` updating as analysis progresses.
- Tabs URL-synced; active = Heritage Blue + underline.

#### 4.1 UPC Validation tab (4B · E4-04 → E4-09)
- Extracted UPC in `text-data` large + barcode crop from uploaded photo alongside.
- Auto cross-reference against PIM → `<MatchCard>`: Match (green, shows name/style/colorway/MSRP + DAM thumbnail) · No Match (red "Counterfeit Indicator") · Mismatch (amber "UPC belongs to A but appears to be B", both shown).
- Validate against **SAP MDG** master record (single source of truth) — show source provenance.
- `<OverridePanel>` at bottom.

#### 4.2 Style Matching tab (4C · E4-10 → E4-15)
- `<CompareViewer>` seized vs authentic (from DAM). Synchronized zoom/pan. Overlay-diff toggle highlights deviations.
- Attribute comparison `<DataTable>`: Attribute | Seized | Authentic | Match — rows: Style #, Colorway, Season, MSRP, Pocket Config; green/red/gray match cells.
- Validate against **VF DAM** master record. `<OverridePanel>`.

#### 4.3 Construction Analysis tab (4D · E4-16 → E4-21)
```
┌ Toolbar: [Annotations ▣][Compare ▢][Zoom ±][Reset] ┐
│ <AnnotatedImage> (bounding boxes, color-coded)      │
├─────────────────────────────────────────────────────┤
│ <DimensionCard>×6 (horizontal scroll):              │
│ Logo · Stitch · Hardware · Label · Material · Overall│
├─────────────────────────────────────────────────────┤
│ Expanded card → Vision API reasoning (linked to box) │
└─────────────────────────────────────────────────────┘
```
- Vision API returns structured per-dimension scores. Click bounding box or card → reasoning detail. `<OverridePanel>`.

#### 4.4 Origin Intelligence tab — condensed (4E · E4-22 → E4-25) *(future enhancement)*
- `<MapView>`: suspected origin + seizure markers, dashed connector.
- Origin indicator cards: Factory Code, Country of Origin, Shipping Route, Supplier Match (value + source + confidence badge).
- Related historical cases table (same source). Cross-reference Authorized Supplier Registry → Authorized (green) / Not Authorized (red) / Unknown (gray) / Potential Ghost-Shift (amber alert).

#### 4.5 Security Tag tab (4F · E4-26 → E4-30)
- Mirrors UPC tab for the brand security tag: extracted value (`text-data`) + crop; auto cross-reference against security-tag DB → `<MatchCard>` (Match/No Match/Mismatch with product details + DAM thumbnail). `<OverridePanel>`.

#### 4.6 Summary tab (4G · E4-31 → E4-34)
```
┌ 4-icon status bar: UPC ✓ · Style ▲ · Construction ✕ · Origin ✓ ┐
├ Composite verdict (large, color-coded):                        │
│   Suspected / Confirmed Counterfeit / Likely Authentic / Inconc.│
├ Key Evidence Summary (top 3–5 findings, each → source tab)     │
├ Actions: [Advance to Enforcement] [Request Add'l Review]       │
│          [Close as Authentic]                                  │
└────────────────────────────────────────────────────────────────┘
```
- Composite derived from all dimensions. Each action → Casemates status update + route.

---

### EPIC 5 — Origin Intelligence Deep Dive *(future enhancement)*

#### 5.1 Origin Deep Dive (E5-01 → E5-06)
```
┌ Interactive Supply-Chain Map (60vh)                         ┐
│  origin(red) ··· waypoints ··· seizure(blue) · factories(grn)│
├──────────────────────────────┬──────────────────────────────┤
│ Network Graph (D3 force)      │ Related Cases (table)        │
│ center=this case, edges=conn  │ ID·Date·Brand·Score·Conn type│
├──────────────────────────────┴──────────────────────────────┤
│ Investigation Notes (timestamped, attributed, autosave)      │
└──────────────────────────────────────────────────────────────┘
```
- Map: full-width, transshipment waypoints on path (hover details). Network graph: click node → details. TMS cross-reference: legit VF lanes vs unknown routes. Notes write to Casemates.

---

### EPIC 6 — Enforcement Action *(flagged "Ignore" in backlog — design but deprioritize)*

#### 6.1 Enforcement (E6-01 → E6-07)
- AI recommendation card: tier badge (1–4), rationale, clickable evidence refs → analysis tabs.
- Actions: Approve (confirm modal → status update → execution tracker appears) · Modify tier (dropdown 1–4 + required justification) · Escalate (assignee dropdown + notes) · Close (reason selector: Insufficient Evidence / Confirmed Authentic / Duplicate / Other).
- `<Timeline>` execution tracker: steps with status icons + dates. Recommendation auto-generated from decision matrix (score, origin, repeat-offender, volume).

---

### EPIC 7 — Evidence Package

#### 7.1 Evidence Package (E7-01 → E7-07)
```
┌ Evidence Package — VF-2026-0412         [PDF][DOCX][Email] ┐
├ TOC sidebar │  PDF-style scrollable preview               │
│  Cover      │  (page breaks visible, zoom controls)        │
│  Summary    │                                              │
│  Metadata   │  Cover · Exec Summary · Metadata ·           │
│  UPC/Style  │  UPC/Style · Construction (w/ images) ·      │
│  Construct. │  Origin · Enforcement rec ·                  │
│  Origin     │  Appendix: raw images + SHA-256 hashes       │
│  Appendix   │                                              │
└─────────────┴──────────────────────────────────────────────┘
```
- Auto-generated from case data. Preview before download. Download PDF (`VERITAS-[CaseID]-Evidence-Package.pdf`) / DOCX (editable). Email → modal with pre-filled brand recipients. SHA-256 hashes computed at intake, shown in appendix. Regenerate = versioned (never overwrites).

---

### EPIC 8 — System Integrations *(flagged "Ignore" — backend; minimal UI)*

#### 8.1 Integration Health (admin, optional)
A status board listing connectors with health/last-sync: PIM (UPC/style lookups), DAM (reference images), MDM (cross-brand), Casemates (CRUD + bidirectional sync, Casemates wins conflicts), Authorized Supplier Registry, TMS (shipping routes), OpenAI Vision API. Plus a **Prompt Templates** admin view: versioned, editable brand/dimension prompts (no code deploy) — E8-11.

---

### EPIC 9 — Notifications, Audit & Non-Functional *(flagged "Ignore" — but these are cross-cutting; bake into the system)*

- **Toasts** (E9-01): top-right, 4 types, success/info auto-dismiss 5s, errors persist.
- **Audit trail** (E9-02): per-case `<Timeline>` — user, timestamp, action, before/after; viewable + exportable.
- **Confirm modals** (E9-03): all destructive actions (close, approve, override).
- **Skeletons** (E9-04) on all data fetches. **Error states** (E9-05): message + retry, no raw codes.
- **Accessibility** (E9-06): WCAG 2.1 AA — focus rings, logical tab order, aria labels on icon buttons, 4.5:1 contrast, screen-reader tested. Never color-only verdicts.
- **Responsive** (E9-07): tablet 768px+; tables horizontally scroll; touch targets ≥44px; CompareViewer falls back to single-pane.
- **Security** (E9-08): TLS 1.3 in transit, AES-256 at rest, signed URLs with expiry for image retrieval (a UI concern only insofar as expired-link handling shows a graceful reload state).

---

## 5. Build Order (suggested for Claude Design)

1. **Tokens + core components** — `<ScoreGauge>`, `<StatusPill>`, `<DataTable>`, `<OverridePanel>`, app shell.
2. **Case Queue** (Epic 2) — the daily home base.
3. **Authentication Analysis hero** (Epic 4, all tabs) — the product's reason to exist.
4. **Summary + Evidence Package** (4G + Epic 7) — close the core loop.
5. **Case Intake monitor** (Epic 3), **Auth/Admin** (Epic 0).
6. **Dashboard** (Epic 1), **Origin Deep Dive** (Epic 5) — future-enhancement, lower fidelity ok.
7. **Enforcement** (Epic 6), **Integration/Prompt admin** (Epic 8) — flagged-ignore; stub-level.
8. Cross-cutting (Epic 9) woven throughout.

---

## 6. Sample Copy (use real-feeling content, not lorem)

- Empty queue: **"No cases match your filters."** + *Clear filters*
- No UPC match: **"No Match — Counterfeit Indicator"** / *This UPC does not exist in the PIM master record.*
- Mismatch: **"UPC belongs to Watertight II Jacket (WL2433-010) but the product appears to be a Bugaboot III boot."**
- Ghost-shift alert: **"Potential ghost-shift — factory is authorized for VF production but this run is outside scheduled volume."**
- Override saved toast: **"Your determination was saved and now overrides the AI assessment."**
- Composite verdict examples: *Suspected Counterfeit* · *Confirmed Counterfeit* · *Likely Authentic* · *Inconclusive*

---

## 7. Open Items to Confirm with VF
- Exact Heritage Blue + neutral hex values, Pantone codes — `[CONFIRM WITH VF BRAND]`
- Licensed display + body typefaces — `[CONFIRM WITH VF BRAND]`
- Per-brand accent colors (TNF/Vans/Timberland and the rest of VF's portfolio) — `[CONFIRM WITH VF BRAND]`
- Logo lockup + clear-space rules for the topbar wordmark — `[CONFIRM WITH VF BRAND]`
- Whether the platform should co-brand per case (e.g. show the relevant brand's mark) or stay One-VF neutral.

---

*End of specification. Anchored to the VERITAS v1.0 backlog (78 stories, 10 epics). Hand to Claude Design; start at §5 build order.*
