// VF VERITAS — mock data + helpers (prototype only; real data wired in later)

export const STAGES = ['Intake', 'Analysis', 'Review', 'Verdict', 'Enforcement', 'Closed'];

export const BRAND_INFO = {
  TNF:        { name: 'The North Face', accent: '#1A1A1A', short: 'TNF' },
  Vans:       { name: 'Vans',           accent: '#C8102E', short: 'Vans' },
  Timberland: { name: 'Timberland',     accent: '#7B5530', short: 'Timberland' }
};

export const ASSIGNEES = ['J. Doe', 'M. Alvarez', 'R. Chen', 'S. Patel', 'K. Larsson', 'T. Okafor'];

export const COUNTRY_NAMES = { CN: 'China', TR: 'Turkey', VN: 'Vietnam', US: 'United States', MX: 'Mexico', BD: 'Bangladesh', ID: 'Indonesia', IT: 'Italy' };

export function fmtDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
export function scoreBand(s) {
  if (s == null) return 'neutral';
  if (s <= 30) return 'authentic';
  if (s <= 60) return 'caution';
  return 'counterfeit';
}
export function bandColor(band) {
  return { authentic: '#2E7D32', caution: '#C99A00', counterfeit: '#C0392B', neutral: '#8A9BB5' }[band] || '#8A9BB5';
}

export const REF_PRODUCTS = {
  TNF:        { name: '1996 Retro Nuptse Jacket',            style: 'NF0A3C8D-LE4', colorway: 'Recycled TNF Black', msrp: '$320.00', upc: '193393578024', season: 'FW25' },
  Vans:       { name: 'Old Skool',                           style: 'VN000D3HY28',  colorway: 'Black / True White', msrp: '$70.00',  upc: '191167589436', season: 'Core' },
  Timberland: { name: '6-Inch Premium Waterproof Boot',      style: 'TB010061713',  colorway: 'Wheat Nubuck',       msrp: '$228.00', upc: '887168539921', season: 'FW25' }
};

export const CASES = [
  { id: 'VF-2026-0412', brand: 'TNF',        source: 'Shenzhen, CN',   country: 'CN', date: '2026-06-18', assignee: 'J. Doe',     stage: 2, score: 78,  status: 'In Review' },
  { id: 'VF-2026-0411', brand: 'Vans',       source: 'Putian, CN',     country: 'CN', date: '2026-06-18', assignee: 'M. Alvarez', stage: 1, score: 64,  status: 'In Review' },
  { id: 'VF-2026-0409', brand: 'Timberland', source: 'Istanbul, TR',   country: 'TR', date: '2026-06-17', assignee: 'R. Chen',    stage: 2, score: 54,  status: 'In Review' },
  { id: 'VF-2026-0405', brand: 'TNF',        source: 'Dongguan, CN',   country: 'CN', date: '2026-06-17', assignee: 'S. Patel',   stage: 4, score: 91,  status: 'Enforcement' },
  { id: 'VF-2026-0402', brand: 'Vans',       source: 'Los Angeles, US', country: 'US', date: '2026-06-16', assignee: 'J. Doe',    stage: 5, score: 12,  status: 'Closed' },
  { id: 'VF-2026-0398', brand: 'Timberland', source: 'Dhaka, BD',      country: 'BD', date: '2026-06-16', assignee: 'K. Larsson', stage: 3, score: 83,  status: 'In Review' },
  { id: 'VF-2026-0395', brand: 'TNF',        source: 'Hanoi, VN',      country: 'VN', date: '2026-06-15', assignee: 'T. Okafor',  stage: 0, score: null, status: 'New' },
  { id: 'VF-2026-0391', brand: 'Vans',       source: 'Jakarta, ID',    country: 'ID', date: '2026-06-15', assignee: 'M. Alvarez', stage: 5, score: 22,  status: 'Authenticated' },
  { id: 'VF-2026-0388', brand: 'Timberland', source: 'Guangzhou, CN',  country: 'CN', date: '2026-06-14', assignee: 'R. Chen',    stage: 2, score: 47,  status: 'In Review' },
  { id: 'VF-2026-0384', brand: 'TNF',        source: 'Yiwu, CN',       country: 'CN', date: '2026-06-14', assignee: 'S. Patel',   stage: 4, score: 88,  status: 'Enforcement' },
  { id: 'VF-2026-0379', brand: 'Vans',       source: 'Leon, MX',       country: 'MX', date: '2026-06-13', assignee: 'J. Doe',     stage: 1, score: 35,  status: 'In Review' },
  { id: 'VF-2026-0375', brand: 'Timberland', source: 'Naples, IT',     country: 'IT', date: '2026-06-13', assignee: 'K. Larsson', stage: 0, score: null, status: 'New' },
  { id: 'VF-2026-0371', brand: 'TNF',        source: 'Fuzhou, CN',     country: 'CN', date: '2026-06-12', assignee: 'T. Okafor',  stage: 5, score: 18,  status: 'Authenticated' },
  { id: 'VF-2026-0366', brand: 'Vans',       source: 'Quanzhou, CN',   country: 'CN', date: '2026-06-12', assignee: 'R. Chen',    stage: 3, score: 72,  status: 'In Review' },
  { id: 'VF-2026-0361', brand: 'Timberland', source: 'Ho Chi Minh, VN', country: 'VN', date: '2026-06-11', assignee: 'S. Patel', stage: 5, score: 28,  status: 'Closed' },
  { id: 'VF-2026-0357', brand: 'TNF',        source: 'Wenzhou, CN',    country: 'CN', date: '2026-06-11', assignee: 'M. Alvarez', stage: 4, score: 95,  status: 'Enforcement' }
];

export const DIM_NAMES = ['Logo', 'Stitching', 'Hardware', 'Label', 'Material', 'Overall'];

// bounding boxes (percent) over the seized-product image, by dimension index
const BOXES = [
  { x: 30, y: 14, w: 40, h: 22 }, // Logo (chest)
  { x: 8,  y: 60, w: 30, h: 16 }, // Stitching (hem)
  { x: 44, y: 40, w: 16, h: 30 }, // Hardware (zipper)
  { x: 60, y: 8,  w: 30, h: 14 }, // Label (collar)
  { x: 12, y: 30, w: 26, h: 26 }, // Material (panel)
  { x: 6,  y: 6,  w: 88, h: 88 }  // Overall
];

const DIM_COPY = {
  Logo: {
    authentic:   ['Embroidery density & color match reference', 'Stitch count (per cm\u00b2), thread color, and logo proportions align with the DAM master within tolerance.'],
    caution:     ['Slight color cast in logo thread', 'Logo geometry matches, but thread color reads ~1.5 \u0394E warmer than reference \u2014 possible dye-lot variance or early-stage wear.'],
    counterfeit: ['Logo proportions deviate from spec', 'The half-dome wordmark is 8% wider than master and the registration mark is misplaced \u2014 consistent with traced artwork.']
  },
  Stitching: {
    authentic:   ['Stitch pitch consistent with factory spec', 'Bartack placement and 7-stitch-per-inch pitch match authorized production records.'],
    caution:     ['Irregular pitch at hem seam', 'Stitch pitch drifts 6\u20139 SPI along the hem; within failure-prone range but not conclusive.'],
    counterfeit: ['Skipped & uneven stitching at stress seams', 'Visible thread skips and 5 SPI pitch at load-bearing seams \u2014 below VF minimum durability spec.']
  },
  Hardware: {
    authentic:   ['Zipper pull stamped with correct foundry mark', 'YKK pull and slider carry the correct embossed code and finish weight.'],
    caution:     ['Zipper finish slightly off-tone', 'Slider geometry correct; anodized finish reads cooler than reference \u2014 inconclusive without teardown.'],
    counterfeit: ['Unbranded zipper, incorrect pull weight', 'Pull lacks foundry stamp and weighs 0.4g under spec; slider tape gauge does not match authorized BOM.']
  },
  Label: {
    authentic:   ['Care label fonts & RN number valid', 'Woven care label uses correct typeface, RN number, and country-of-origin format.'],
    caution:     ['Care label kerning irregular', 'RN number is valid but care-symbol kerning is inconsistent \u2014 flag for physical review.'],
    counterfeit: ['Invalid RN number on care label', 'Printed (not woven) care label; RN number does not resolve to a VF-registered entity.']
  },
  Material: {
    authentic:   ['Fabric hand & weave match reference', 'Panel weave count and coating match the authorized material spec.'],
    caution:     ['Coating sheen differs from reference', 'Face-fabric weave correct; DWR coating sheen differs under raking light \u2014 possible substitute finish.'],
    counterfeit: ['Substituted shell fabric detected', 'Shell denier and ripstop grid do not match spec; lining is a non-authorized substitute.']
  },
  Overall: {
    authentic:   ['Construction consistent with authentic unit', 'Aggregated dimension scores fall within the authentic band against the reference unit.'],
    caution:     ['Mixed signals \u2014 physical verification advised', 'Some dimensions pass while others deviate; composite is inconclusive and warrants teardown.'],
    counterfeit: ['Multiple construction deviations detected', 'Several independent dimensions deviate from spec, consistent with a counterfeit unit.']
  }
};

function seeded(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return () => { h += 0x6D2B79F5; let t = h; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

const STYLE_ATTRS = ['Style #', 'Colorway', 'Season', 'MSRP', 'Pocket Config'];

export function buildDetail(c) {
  const rnd = seeded(c.id);
  const band = scoreBand(c.score);
  const base = c.score == null ? null : c.score;
  const ref = REF_PRODUCTS[c.brand];
  const countryName = COUNTRY_NAMES[c.country] || c.country;

  // Per-dimension scores clustered around the case score
  const dims = DIM_NAMES.map((name, i) => {
    let s;
    if (base == null) s = null;
    else if (name === 'Overall') s = base;
    else s = Math.round(Math.max(3, Math.min(98, base + (rnd() * 44 - 22))));
    const b = scoreBand(s);
    const copy = DIM_COPY[name][b === 'neutral' ? 'caution' : b];
    return { name, score: s, band: b, finding: copy[0], reasoning: copy[1], box: BOXES[i], boxColor: bandColor(b) };
  });

  // UPC match state
  let upcStatus, upcNote, upcBelongs = null;
  if (band === 'authentic') { upcStatus = 'match'; upcNote = 'UPC resolves to the matching master record in SAP MDG.'; }
  else if (band === 'caution') {
    upcStatus = 'mismatch';
    upcBelongs = c.brand === 'Timberland'
      ? { name: 'Watertight II Jacket', style: 'WL2433-010' }
      : { name: ref.name, style: ref.style };
    upcNote = 'UPC belongs to ' + upcBelongs.name + ' (' + upcBelongs.style + ') but the product appears to be a ' + (c.brand === 'Timberland' ? 'Bugaboot III boot' : ref.name + ' variant') + '.';
  } else { upcStatus = 'nomatch'; upcNote = 'This UPC does not exist in the PIM master record.'; }

  const upcValue = band === 'counterfeit'
    ? (ref.upc.slice(0, 9) + Math.floor(rnd() * 900 + 100))
    : ref.upc;

  // Style attribute comparison
  const seasons = ['FW25', 'SS25', 'FW24', 'Core'];
  const styleRows = STYLE_ATTRS.map((attr) => {
    let authentic, seized, match;
    if (attr === 'Style #') { authentic = ref.style; }
    else if (attr === 'Colorway') { authentic = ref.colorway; }
    else if (attr === 'Season') { authentic = ref.season; }
    else if (attr === 'MSRP') { authentic = ref.msrp; }
    else { authentic = c.brand === 'Vans' ? 'N/A (footwear)' : '2 hand + 1 chest'; }

    if (band === 'authentic') { seized = authentic; match = true; }
    else if (band === 'caution') {
      match = rnd() > 0.5;
      seized = match ? authentic : (attr === 'Colorway' ? authentic.replace('Black', 'Charcoal') : attr === 'Season' ? seasons[Math.floor(rnd() * seasons.length)] : attr === 'MSRP' ? authentic : authentic + ' *');
    } else {
      match = rnd() > 0.7;
      seized = match ? authentic : (attr === 'Style #' ? authentic.slice(0, -2) + 'XX' : attr === 'Colorway' ? 'Black / Grey' : attr === 'Season' ? 'Unbranded' : attr === 'MSRP' ? '\u2014' : 'Altered');
    }
    return { attr, authentic, seized, match };
  });

  // Security tag
  let tagStatus = upcStatus;
  const tagValue = band === 'counterfeit' ? 'VF-SEC-' + Math.floor(rnd() * 9e6 + 1e6) : 'VF-SEC-' + (1000000 + (c.id.charCodeAt(8) * 7919) % 9000000);
  const tagNote = band === 'authentic' ? 'Security tag verified against the brand security-tag registry.'
    : band === 'caution' ? 'Tag format valid but the embedded checksum is inconsistent \u2014 flag for physical scan.'
    : 'Security tag value is not present in the registry \u2014 counterfeit indicator.';

  // Origin
  const ghost = band === 'caution' && rnd() > 0.5;
  const supplierStatus = band === 'authentic' ? 'authorized' : ghost ? 'ghost' : band === 'caution' ? 'unknown' : 'not';
  const origin = {
    factoryCode: (c.country + '-' + String(Math.floor(rnd() * 9000 + 1000))),
    country: countryName,
    route: countryName + ' \u2192 ' + (c.country === 'US' ? 'Long Beach, US' : 'Rotterdam, NL') + ' \u2192 ' + (rnd() > 0.5 ? 'Newark, US' : 'Felixstowe, UK'),
    supplierStatus,
    supplierValue: supplierStatus === 'authorized' ? 'Authorized VF supplier (Tier 1)' : supplierStatus === 'ghost' ? 'Authorized factory \u2014 unscheduled run' : supplierStatus === 'unknown' ? 'No registry match' : 'Not an authorized supplier',
    supplierSource: 'Authorized Supplier Registry',
    confidence: band === 'authentic' ? 'High' : band === 'caution' ? 'Medium' : 'High',
    ghostAlert: ghost ? 'Potential ghost-shift \u2014 factory is authorized for VF production but this run is outside scheduled volume.' : null
  };

  // Composite verdict
  const composite = band === 'authentic' ? 'Likely Authentic'
    : band === 'caution' ? 'Inconclusive'
    : (c.score >= 85 ? 'Confirmed Counterfeit' : 'Suspected Counterfeit');

  // Key findings (each links to a tab)
  const findings = [
    { tab: 'upc', text: upcStatus === 'match' ? 'UPC validated against SAP MDG master record.' : upcStatus === 'mismatch' ? 'UPC mismatch \u2014 code belongs to a different product.' : 'UPC not found in PIM \u2014 counterfeit indicator.', band: scoreBand(upcStatus === 'match' ? 10 : upcStatus === 'mismatch' ? 50 : 80) },
    { tab: 'construction', text: dims[0].finding, band: dims[0].band },
    { tab: 'construction', text: dims[2].finding, band: dims[2].band },
    { tab: 'security', text: tagStatus === 'match' ? 'Security tag verified in registry.' : 'Security tag could not be verified.', band: scoreBand(tagStatus === 'match' ? 10 : 80) },
    { tab: 'origin', text: origin.ghostAlert ? 'Ghost-shift risk flagged at source factory.' : origin.supplierStatus === 'authorized' ? 'Source factory is an authorized VF supplier.' : 'Source factory not found in supplier registry.', band: origin.supplierStatus === 'authorized' ? 'authentic' : origin.supplierStatus === 'not' ? 'counterfeit' : 'caution' }
  ];

  // Enforcement recommendation
  const tier = c.score == null ? 2 : c.score >= 90 ? 4 : c.score >= 75 ? 3 : c.score >= 50 ? 2 : 1;
  const enforcement = {
    tier,
    rationale: 'Composite score ' + (c.score == null ? '\u2014' : c.score) + '/100, source in ' + countryName + ', ' + (supplierStatus === 'not' ? 'no authorized supplier match, ' : '') + 'repeat-offender source flagged. Decision matrix recommends Tier ' + tier + '.',
    evidenceRefs: [
      { tab: 'upc', label: 'UPC validation result' },
      { tab: 'construction', label: 'Construction deviations (' + dims.filter(d => d.band === 'counterfeit').length + ')' },
      { tab: 'origin', label: 'Origin & supplier check' }
    ]
  };

  // Audit trail
  const audit = [
    { ts: c.date + ' 08:14', user: 'RPA Intake Bot', action: 'Case ingested from seizure payload', detail: 'JSON + 6 attachments parsed; SHA-256 hashes computed.' },
    { ts: c.date + ' 08:15', user: 'Vision API', action: 'Construction analysis completed', detail: '6 dimensions scored.' },
    { ts: c.date + ' 09:02', user: c.assignee, action: 'Case opened for review', detail: 'Assigned via queue.' }
  ];

  return { dims, upcStatus, upcNote, upcBelongs, upcValue, ref, styleRows, tagStatus, tagValue, tagNote, origin, composite, band, findings, enforcement, audit, countryName };
}

// Intake monitor payload log
export const INTAKE_LOG = [
  { id: 'PL-88241', case: 'VF-2026-0412', received: '08:14:02', status: 'Loaded', brand: 'TNF', attachments: 6, hash: 'a3f9\u202608e1' },
  { id: 'PL-88240', case: 'VF-2026-0411', received: '08:13:47', status: 'Loaded', brand: 'Vans', attachments: 4, hash: '7c21\u2026bb90' },
  { id: 'PL-88239', case: 'VF-2026-0409', received: '08:11:09', status: 'Metadata Extracted', brand: 'Timberland', attachments: 5, hash: 'd0e4\u2026 1a77' },
  { id: 'PL-88238', case: '\u2014', received: '08:09:55', status: 'Error', brand: 'Vans', attachments: 3, hash: '\u2014', error: 'Missing security-tag field in payload schema' },
  { id: 'PL-88237', case: 'VF-2026-0395', received: '08:06:31', status: 'Received', brand: 'TNF', attachments: 6, hash: '\u2014' }
];

export const INTAKE_FIELDS = [
  { k: 'Security Tag', v: 'VF-SEC-4471902' },
  { k: 'UPC', v: '193393578024' },
  { k: 'Style #', v: 'NF0A3C8D-LE4' },
  { k: 'Construction', v: '6 dimensions queued' },
  { k: 'Origin', v: 'Shenzhen, CN \u00b7 Factory CN-4471' }
];

// Admin — users
export const USERS = [
  { name: 'Jordan Doe',      email: 'jordan.doe@vfc.com',     group: 'VF-BP-Specialists', role: 'Specialist', active: '2 min ago' },
  { name: 'Maria Alvarez',   email: 'maria.alvarez@vfc.com',  group: 'VF-BP-Specialists', role: 'Specialist', active: '11 min ago' },
  { name: 'Ren Chen',        email: 'ren.chen@vfc.com',       group: 'VF-BP-Managers',    role: 'Manager',    active: '1 hr ago' },
  { name: 'Sana Patel',      email: 'sana.patel@vfc.com',     group: 'VF-BP-Specialists', role: 'Specialist', active: '3 hr ago' },
  { name: 'Karl Larsson',    email: 'karl.larsson@vfc.com',   group: 'VF-BP-Managers',    role: 'Manager',    active: 'Yesterday' },
  { name: 'Tomi Okafor',     email: 'tomi.okafor@vfc.com',    group: 'VF-IT-Admins',      role: 'Admin',      active: 'Yesterday' }
];

// Admin — integration health
export const CONNECTORS = [
  { name: 'PIM',                        desc: 'UPC & style lookups',        status: 'Healthy', sync: '2 min ago' },
  { name: 'DAM',                        desc: 'Reference imagery',          status: 'Healthy', sync: '4 min ago' },
  { name: 'SAP MDG',                    desc: 'Master data (single source)', status: 'Healthy', sync: '1 min ago' },
  { name: 'Casemates',                  desc: 'CRUD + bidirectional sync',  status: 'Healthy', sync: '30 sec ago' },
  { name: 'Authorized Supplier Registry', desc: 'Supplier authorization',   status: 'Degraded', sync: '46 min ago' },
  { name: 'TMS',                        desc: 'Shipping routes',            status: 'Healthy', sync: '8 min ago' },
  { name: 'OpenAI Vision API',          desc: 'Construction analysis',      status: 'Healthy', sync: 'Live' }
];

export const PROMPTS = [
  { name: 'Logo deviation \u2014 The North Face', version: 'v4', updated: '2026-06-10', editor: 'R. Chen' },
  { name: 'Stitch pitch analysis \u2014 footwear', version: 'v2', updated: '2026-05-28', editor: 'K. Larsson' },
  { name: 'Hardware foundry-mark check', version: 'v7', updated: '2026-06-15', editor: 'R. Chen' },
  { name: 'Care-label OCR & RN validation', version: 'v3', updated: '2026-06-01', editor: 'K. Larsson' }
];

// Dashboard KPI + chart data
export const KPIS = [
  { label: 'Cases This Month', value: '342', trend: +12, good: 'up' },
  { label: 'Avg. Resolution', value: '2.4d', trend: -8, good: 'down' },
  { label: 'Enforcement Actions', value: '57', trend: +5, good: 'up' },
  { label: 'Est. Revenue Protected', value: '$4.2M', trend: +18, good: 'up' }
];

export const CASE_VOLUME = {
  months: ['Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'],
  series: {
    TNF:        [22, 28, 31, 26, 35, 42, 38, 44, 51, 47, 55, 61],
    Vans:       [18, 21, 19, 24, 28, 26, 31, 29, 35, 38, 41, 44],
    Timberland: [9, 12, 14, 11, 16, 19, 17, 22, 20, 25, 28, 31]
  }
};

export const BRAND_SPLIT = [
  { brand: 'TNF', label: 'The North Face', value: 47, color: '#1A1A1A' },
  { brand: 'Vans', label: 'Vans', value: 33, color: '#C8102E' },
  { brand: 'Timberland', label: 'Timberland', value: 20, color: '#7B5530' }
];

export const TOP_SOURCES = [
  { source: 'Putian, CN', cases: 41, lastSeen: '2026-06-18', trend: +14 },
  { source: 'Dongguan, CN', cases: 33, lastSeen: '2026-06-17', trend: +6 },
  { source: 'Istanbul, TR', cases: 22, lastSeen: '2026-06-17', trend: -3 },
  { source: 'Dhaka, BD', cases: 18, lastSeen: '2026-06-16', trend: +9 },
  { source: 'Yiwu, CN', cases: 15, lastSeen: '2026-06-14', trend: +2 }
];

// abstract seizure map points (x,y in % of the map box)
export const MAP_POINTS = [
  { x: 78, y: 42, label: 'Shenzhen, CN', count: 41, kind: 'source' },
  { x: 81, y: 38, label: 'Putian, CN', count: 33, kind: 'source' },
  { x: 62, y: 36, label: 'Istanbul, TR', count: 22, kind: 'source' },
  { x: 72, y: 48, label: 'Dhaka, BD', count: 18, kind: 'source' },
  { x: 22, y: 40, label: 'Newark, US', count: 28, kind: 'seizure' },
  { x: 15, y: 38, label: 'Long Beach, US', count: 19, kind: 'seizure' },
  { x: 47, y: 33, label: 'Rotterdam, NL', count: 24, kind: 'transit' }
];
