import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const coreSource = await readFile(new URL('../housesync/static/plan-core.js', import.meta.url), 'utf8');
const coreURL = 'data:text/javascript;base64,' + Buffer.from(coreSource).toString('base64');
const { normalizeBoqSettings, RATE_ITEMS, WASTAGE_DEFAULTS, defaultOpenings, describeChanges, DraftHistory } = await import(coreURL);
const fixture = () => ({
  project_name: 'Legacy design', length_mm: 10000, width_mm: 8000, height_mm: 3000,
  wall_thickness_mm: 200, paint_colour: 'Light blue', paint_hex: '#ADD8E6', currency: 'SGD',
  rates: { floor_m2: 80, wall_m3: 300, roof_m2: 120, paint_m2: 12, opening_each: 500 },
});
const modern = () => normalizeBoqSettings({ ...fixture(), currency: 'AUD', rate_overrides: {} });
const library = (sha = 'a'.repeat(64), floorRate = 95) => ({ sha256: sha, currency: 'AUD', rows: {
  FLOOR: { code: 'FLOOR', description: 'Floor slab', unit: 'm2', rate: floorRate, currency: 'AUD', source: 'Illustrative allowance', effective_date: '2026-11-17', notes: 'Not a supplier quote' },
  DOOR: { code: 'DOOR', description: 'Door allowance', unit: 'each', rate: 650, currency: 'AUD', source: 'Door allowance source', effective_date: '2026-11-18', notes: '' },
} });

// Run the real app form, reset and table functions against a small DOM without a renderer.
class Element {
  constructor(tagName) { this.tagName = tagName; this.value = ''; this.children = []; this.options = []; this.textContent = ''; this.style = {}; this.classList = { toggle() {}, add() {}, remove() {} }; this.listeners = {}; this.attributes = {}; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  set id(value) { this._id = value; elements.set('#' + value, this); }
  get id() { return this._id; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  trigger(name) { this.listeners[name]?.({ target: this }); }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  querySelectorAll(tagName) { return this.children.filter(child => child.tagName === tagName); }
  replaceChildren(...children) { this.children = children; }
}
const elements = new Map();
const html = await readFile(new URL('../housesync/static/index.html', import.meta.url), 'utf8');
for (const match of html.matchAll(/<input\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const element = new Element(); element.value = /\bvalue="([^"]*)"/.exec(match[0])?.[1] ?? '';
  elements.set('#' + match[1], element);
}
const currency = new Element(); currency.value = 'AUD'; currency.options = ['AUD', 'SGD', 'MYR', 'USD', 'GBP'].map(value => ({ value })); elements.set('#currency', currency);
globalThis.document = {
  querySelector(selector) { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); },
  querySelectorAll() { return []; }, createElement(tagName) { return new Element(tagName); },
};
// Capture the preview's RGB writes without requiring WebGL in the Node suite.
class TestColour {
  constructor(hex) { this.rgb = hex.slice(1).match(/../g).map(pair => Number.parseInt(pair, 16)); }
  toArray(array, offset) { array.set(this.rgb, offset); }
}
const appSource = (await readFile(new URL('../housesync/static/app.js', import.meta.url), 'utf8'))
  .replace("import * as THREE from 'three';", `const THREE = { Vector3: class {}, Color: ${TestColour.toString()} };`)
  .replace("import { OrbitControls } from '/vendor/OrbitControls.js';", '')
  .replace("import { PlanEditor } from './plan-editor.js';", '')
  .replace("from './plan-core.js'", "from '" + coreURL + "'")
  .split("form.addEventListener('submit', buildHouse);")[0]
  + '\nexport { fillForm, settingsFromForm, comparableSettings, stableSettings, renderBOQ, renderRates, refreshRateLibrary, resetRateOverride, libraryDiffersFromRevision, rateLibraryState, state, renderComparison, setDirty, viewer, legacyWallFaceSlots };';
const { fillForm, settingsFromForm, comparableSettings, stableSettings, renderBOQ, renderRates, refreshRateLibrary, resetRateOverride, libraryDiffersFromRevision, rateLibraryState, state, renderComparison, setDirty, viewer, legacyWallFaceSlots } = await import('data:text/javascript;base64,' + Buffer.from(appSource).toString('base64'));

test('legacy entered rates migrate to independent overrides without inventing missing trade rates', () => {
  const legacy = fixture(); const normalized = normalizeBoqSettings(legacy);
  assert.equal(normalized.currency, 'SGD'); assert.equal(normalized.rate_overrides.FLOOR, 80);
  assert.equal(normalized.rate_overrides.DOOR, 500); assert.equal(normalized.rate_overrides.WINDOW, 500);
  assert.equal(normalized.paint_coats, 2); assert.equal(Object.hasOwn(normalized.rate_overrides, 'FLOOR_FINISH'), false);
  assert.deepEqual(normalized.wastage, WASTAGE_DEFAULTS); assert.equal(legacy.paint_coats, undefined);
  assert.equal(normalized.rates, undefined); normalized.rate_overrides.FLOOR = 999;
  assert.equal(legacy.rates.floor_m2, 80);
});

test('explicit empty overrides keep a modern design library-driven despite a stale rates mirror', () => {
  const normalized = normalizeBoqSettings({ ...fixture(), currency: 'AUD', rate_overrides: {} });
  assert.deepEqual(normalized.rate_overrides, {}); assert.equal(normalized.rates, undefined);
  fillForm(normalized); const submitted = settingsFromForm();
  assert.deepEqual(submitted.rate_overrides, {}); assert.equal(submitted.rates, undefined);
  for (const [code] of RATE_ITEMS) assert.equal(document.querySelector('#override-' + code.toLowerCase().replaceAll('_', '-')).value, '');
});

test('sidebar saves each leaf independently and restores its controls when a design is reopened', () => {
  const design = { ...modern(), exterior_finish: { kind: 'facebrick', name: 'Charcoal', hex: '#575656' } };
  fillForm(design);
  assert.equal(document.querySelector('#exterior-name').attributes.list, 'brick-product-names');
  document.querySelector('#paint-hex').value = '#F3EDE0';
  document.querySelector('#paint-colour').value = 'Warm white';
  const painted = settingsFromForm();
  assert.deepEqual(painted.exterior_finish, design.exterior_finish);
  fillForm({ ...painted, inner_leaf_finish: 'bare_brick', inner_brick_hex: '#B68061' });
  assert.equal(document.querySelector('#bare-brick-controls').hidden, false);
  assert.equal(document.querySelector('#interior-paint-controls').hidden, true);
  assert.equal(document.querySelector('#paint-coats-field').hidden, true);
  const bare = settingsFromForm();
  assert.deepEqual(bare.exterior_finish, design.exterior_finish);
  assert.equal(bare.inner_brick_hex, '#B68061');
  fillForm({ ...bare, inner_leaf_finish: 'plaster_paint' });
  assert.equal(document.querySelector('#interior-paint-controls').hidden, false);
  assert.equal(settingsFromForm().paint_hex, '#F3EDE0');
});

test('editing a finish updates only its rendered surfaces before save, including undo and label-only edits', t => {
  const saved = { ...modern(), exterior_finish: { kind: 'facebrick', name: 'Terracotta', hex: '#AD684F' } };
  const snapshot = { revision: 'original', settings: structuredClone(saved) };
  const attribute = { array: new Uint8Array(27) };
  const shell = { userData: { id: 'W1', kind: 'wall', faceSlots: [2, 0, 1], savedColours: ['#AD684F', '#ADD8E6', '#B88465'] },
    geometry: { getAttribute: name => { assert.equal(name, 'color'); return attribute; } } };
  const partition = { userData: { kind: 'wall', partition: true, savedColour: '#ADD8E6' },
    material: { color: { set(value) { this.value = value; } } } };
  const nonWall = { userData: { kind: 'roof' } };
  state.result = snapshot; viewer.parts = [shell, partition, nonWall]; viewer.selected = shell; viewer.selectedFace = 1;
  t.after(() => { state.result = null; state.dirty = false; viewer.parts = []; viewer.selected = null; viewer.finishKey = null; });
  const readTriangle = face => Array.from({ length: 3 }, (_, vertex) => {
    const i = (face * 3 + vertex) * 3;
    return '#' + [...attribute.array.subarray(i, i + 3)].map(channel => channel.toString(16).padStart(2, '0')).join('').toUpperCase();
  });
  const check = (brick, outside, inside, paint) => {
    for (const [face, hex] of [brick, outside, inside].entries()) assert.deepEqual(readTriangle(face), [hex, hex, hex]);
    assert.equal(partition.material.color.value, paint);
  };
  fillForm(saved);
  document.querySelector('#exterior-hex').value = '#575656';
  document.querySelector('#exterior-name').value = 'Charcoal';
  setDirty();
  check('#B88465', '#575656', '#ADD8E6', '#ADD8E6');
  assert.match(document.querySelector('#selected-element').textContent, /W1.*External.*Charcoal/);
  assert.equal(document.querySelector('#save-settings').disabled, true);
  assert.equal(state.dirty, true);
  const coloured = settingsFromForm();
  fillForm({ ...coloured, paint_hex: '#F3EDE0', inner_leaf_finish: 'bare_brick', inner_brick_hex: '#915B44' }); setDirty(false);
  check('#915B44', '#575656', '#915B44', '#F3EDE0');
  attribute.needsUpdate = false;
  document.querySelector('#exterior-name').value = 'Selected facebrick'; setDirty();
  assert.match(document.querySelector('#selected-element').textContent, /Selected facebrick/);
  assert.equal(attribute.needsUpdate, false);
  // The plan history and JSON import both restore the complete draft through fillForm.
  fillForm(coloured); setDirty(false);
  check('#B88465', '#575656', '#ADD8E6', '#ADD8E6');
  fillForm(saved); setDirty(false);
  check('#B88465', '#AD684F', '#ADD8E6', '#ADD8E6');
  assert.equal(state.dirty, false);
  assert.deepEqual(state.result, { revision: 'original', settings: saved });
});

test('the drawings tab follows draft finishes and geometry, discards late responses and restores the saved sheet on undo', async t => {
  const saved = modern();
  state.result = { revision: 'original', settings: saved, files: { drawings_svg: '/files/original/drawings.svg' } };
  const previousWindow = globalThis.window;
  globalThis.window = { location: new URL('http://localhost:8766') };
  const panel = document.querySelector('#panel-drawings'), image = document.querySelector('#drawing-preview');
  const status = document.querySelector('#drawing-status');
  image.style = {};
  let scheduled;
  t.mock.method(globalThis, 'setTimeout', callback => { scheduled = callback; return 1; });
  t.mock.method(globalThis, 'clearTimeout', () => { scheduled = null; });
  const requests = [];
  t.mock.method(globalThis, 'fetch', (url, options) => new Promise(resolve => {
    requests.push({ url, design: JSON.parse(options.body), signal: options.signal, resolve });
  }));
  t.after(() => { state.result = null; state.dirty = false; panel.hidden = true; globalThis.window = previousWindow; });
  fillForm(saved);
  panel.hidden = false;
  setDirty();
  const savedURL = image.src;
  assert.equal(savedURL, 'http://localhost:8766/files/original/drawings.svg?revision=original');
  panel.hidden = true;
  document.querySelector('#exterior-hex').value = '#81551B';
  setDirty();
  assert.equal(scheduled, null);
  panel.hidden = false;
  setDirty();
  const first = scheduled();
  assert.equal(requests[0].url, '/api/drawings-preview');
  assert.equal(requests[0].design.exterior_finish.hex, '#81551B');
  assert.equal(image.style.visibility, 'hidden');
  document.querySelector('#exterior-name').value = 'Ochre brick';
  document.querySelector('#length').value = '12';
  setDirty();
  const latest = scheduled();
  assert.equal(requests[0].signal.aborted, true);
  assert.equal(requests[1].design.exterior_finish.name, 'Ochre brick');
  assert.equal(requests[1].design.length_mm, 12000);
  const svg = () => new Response('<svg xmlns="http://www.w3.org/2000/svg"/>', { headers: { 'Content-Type': 'image/svg+xml' } });
  requests[1].resolve(svg()); await latest;
  const draftURL = image.src;
  assert.match(draftURL, /^blob:/);
  assert.match(status.textContent, /Draft drawings/);
  assert.equal(image.style.visibility, '');
  requests[0].resolve(svg()); await first;
  assert.equal(image.src, draftURL);
  document.querySelector('#length').value = '1';
  setDirty();
  const invalid = scheduled();
  requests[2].resolve(Response.json({ error: 'Check the clear length.' }, { status: 422 }));
  await invalid;
  assert.match(status.textContent, /Draft drawings unavailable.*Check the clear length/);
  assert.equal(image.style.visibility, 'hidden');
  fillForm(saved); setDirty(false);
  assert.equal(image.src, savedURL);
  assert.match(status.textContent, /saved revision original/);
  assert.equal(image.style.visibility, '');
  document.querySelector('#override-floor').value = '500'; setDirty();
  assert.equal(scheduled, null);
  assert.equal(image.src, savedURL);
  assert.equal(state.dirty, true);
});

test('a legacy shell mesh identifies external faces and corner returns, internal faces and reveals', () => {
  const vertices = [
    [3, -.2, 0], [4, -.2, 0], [3, -.2, 2],  // exterior
    [3, 0, 0], [3, 0, 2], [4, 0, 0],        // interior
    [-.2, -.2, 0], [-.2, 0, 0], [-.2, 0, 3], // external corner return
    [3, -.2, 0], [3, 0, 0], [3, 0, 2],      // door reveal
  ].flat();
  const indices = Array.from({ length: 12 }, (_, i) => i);
  assert.deepEqual(legacyWallFaceSlots({ id: 'W1', kind: 'wall' }, vertices, indices, fixture()), [0, 1, 0, 2]);
  assert.equal(legacyWallFaceSlots({ id: 'P1', kind: 'wall' }, vertices, indices, fixture()), null);
});

test('sidebar rate edits retain partition geometry, room selections and minimum width', () => {
  const design = { ...modern(), min_room_width_mm: 1200,
    partitions: [{ id: 'P1', start_mm: [4000, 0], end_mm: [4000, 8000], thickness_mm: 100, height_mm: 3000, wall_type: 'Stud' }],
    rooms: [{ id: 'ROOM_ABCDEF123456', name: 'Study', finishes: { floor: 'Cork' } }],
  };
  fillForm(design);
  document.querySelector('#override-skirting').value = '24';
  const saved = settingsFromForm();
  assert.deepEqual(saved.partitions, design.partitions);
  assert.deepEqual(saved.rooms, design.rooms);
  assert.equal(saved.min_room_width_mm, 1200);
  assert.equal(saved.rate_overrides.SKIRTING, 24);
  fillForm(modern());
  assert.deepEqual(settingsFromForm().partitions, []);
});

test('legacy JSON import resets absent controls and produces no false draft changes', () => {
  fillForm({ ...modern(), paint_coats: 7, wastage: { floor_finish: 90 }, rate_overrides: { CEILING: 999 }, rate_source: 'Previous file', rate_date: '2025-01-01' });
  const legacy = fixture(); fillForm(legacy); const submitted = settingsFromForm();
  assert.equal(submitted.paint_coats, 2); assert.equal(submitted.wastage.floor_finish, 10);
  assert.equal(document.querySelector('#override-ceiling').value, ''); assert.equal(submitted.currency, 'SGD');
  assert.equal(submitted.rate_date, '2026-11-17'); assert.match(submitted.rate_source, /no supplier quotes/);
  assert.equal(stableSettings(submitted), stableSettings(comparableSettings({ ...legacy, openings: defaultOpenings() })));
  assert.deepEqual(describeChanges(legacy, submitted), []);
});

test('sidebar serializes all eleven overrides independently, plus wastage and metadata', () => {
  fillForm(modern());
  for (const [index, [code]] of RATE_ITEMS.entries()) document.querySelector('#override-' + code.toLowerCase().replaceAll('_', '-')).value = index + 0.125;
  document.querySelector('#paint-coats').value = 3; document.querySelector('#wastage-ceiling').value = 7.5;
  document.querySelector('#wastage-window').value = 2; document.querySelector('#rate-source').value = 'User-entered allowance';
  document.querySelector('#rate-date').value = '2026-11-18'; const submitted = settingsFromForm();
  assert.equal(submitted.paint_coats, 3); assert.equal(submitted.wastage.ceiling, 7.5); assert.equal(submitted.wastage.window, 2);
  assert.deepEqual(Object.keys(submitted.rate_overrides).sort(), RATE_ITEMS.map(([code]) => code).sort());
  assert.notEqual(submitted.rate_overrides.DOOR, submitted.rate_overrides.WINDOW);
  assert.deepEqual(Object.keys(submitted.wastage).sort(), Object.keys(WASTAGE_DEFAULTS).sort());
  assert.equal(submitted.rate_source, 'User-entered allowance'); assert.equal(submitted.rate_date, '2026-11-18');
});

test('zero is an override, and the individual Use library action removes only that override', () => {
  const settings = { ...modern(), rate_overrides: { FLOOR: 0, DOOR: 700, WINDOW: 800 } };
  fillForm(settings); state.result = { settings, revision: 'saved' }; state.busy = false;
  assert.equal(settingsFromForm().rate_overrides.FLOOR, 0);
  resetRateOverride('DOOR'); assert.deepEqual(settingsFromForm().rate_overrides, { FLOOR: 0, WINDOW: 800 });
  assert.equal(state.dirty, true); assert.equal(document.querySelector('#reset-door').disabled, true);
  state.result = null;
});

test('BOQ displays net/waste/gross and each saved rate provenance/source/date', () => {
  renderBOQ({ settings: modern(), rate_library: library(), boq: [
    { code: 'FLOOR', description: 'Floor slab', net_quantity: 80, wastage_percent: 10, gross_quantity: 88, quantity: 88, unit: 'm2', rate: 95, amount: 8360, rate_origin: 'library', rate_source: 'CSV source', rate_effective_date: '2026-11-17' },
    { code: 'DOOR', description: 'Door', net_quantity: 1, wastage_percent: 0, gross_quantity: 1, unit: 'each', rate: 700, amount: 700, rate_origin: 'override', rate_source: 'Design allowance', rate_effective_date: '2026-11-18' },
  ] });
  const cells = document.querySelector('#boq-body').children[0].children.map(cell => cell.textContent);
  assert.equal(cells.length, 9); assert.match(cells[2], /80/); assert.match(cells[3], /10/); assert.match(cells[4], /88/);
  assert.equal(cells[5], 'm2'); assert.match(cells[6], /95/); assert.match(cells[7], /Library.*CSV source.*2026-11-17/); assert.match(cells[8], /8.?360/);
  assert.match(document.querySelector('#boq-body').children[1].children[7].textContent, /Override.*Design allowance.*2026-11-18/);
});

test('Rates view distinguishes current CSV values and currency from the saved revision fingerprint', () => {
  state.result = { revision: 'saved', settings: modern(), rate_library: library('b'.repeat(64)) };
  rateLibraryState.current = library('a'.repeat(64), 105); renderRates();
  assert.equal(libraryDiffersFromRevision(), true);
  assert.match(document.querySelector('#rate-library-hash').textContent, /Current server library.*aaaa.*AUD/);
  assert.match(document.querySelector('#rate-library-saved').textContent, /Saved revision saved.*bbbb/);
  assert.match(document.querySelector('#rate-library-difference').textContent, /differs.*Update house/);
  const cells = document.querySelector('#rate-library-body').children[0].children.map(cell => cell.textContent);
  assert.deepEqual(cells.slice(0, 8), ['FLOOR', 'Floor slab', 'm2', '105.00', 'AUD', 'Illustrative allowance', '2026-11-17', 'Not a supplier quote']);
  state.result = null;
});

test('refreshing a changed CSV marks a new priced revision pending without altering overrides', async () => {
  const settings = { ...modern(), rate_overrides: { DOOR: 700 } }; fillForm(settings);
  state.result = { revision: 'saved', settings, rate_library: library('a'.repeat(64)) }; rateLibraryState.current = library('a'.repeat(64));
  globalThis.fetch = async url => { assert.equal(url, '/api/rates'); return { ok: true, json: async () => library('b'.repeat(64), 110) }; };
  await refreshRateLibrary(); assert.equal(state.dirty, true); assert.equal(rateLibraryState.current.rows.FLOOR.rate, 110);
  assert.deepEqual(settingsFromForm().rate_overrides, { DOOR: 700 }); assert.match(document.querySelector('#library-rate-floor').textContent, /110.*AUD/);
  assert.equal(state.result.rate_library.sha256, 'a'.repeat(64)); state.result = null;
});

test('plan review separates library resets, independent overrides, coats and wastage with full undo', () => {
  const before = { ...modern(), rate_overrides: { DOOR: 650, WINDOW: 650 } }; const after = structuredClone(before);
  after.paint_coats = 3; after.wastage.skirting = 15; after.rate_overrides.WINDOW = 800; delete after.rate_overrides.DOOR;
  after.rate_source = 'Edited allowance'; after.rate_date = '2026-11-18'; const descriptions = describeChanges(before, after);
  assert.ok(descriptions.some(text => /Paint coats: 2 to 3/.test(text)));
  assert.ok(descriptions.some(text => /skirting wastage: 10% to 15%/.test(text)));
  assert.ok(descriptions.some(text => /WINDOW rate: 650 override to 800 override/.test(text)));
  assert.ok(descriptions.some(text => /DOOR rate: 650 override to library/.test(text)));
  const history = new DraftHistory(before); history.push(after);
  assert.deepEqual(history.undo(), { ...before, openings: defaultOpenings() }); assert.deepEqual(history.redo(), { ...after, openings: defaultOpenings() });
});

test('comparison view separates CSV changes from design edits and labels library versus override costs', () => {
  renderComparison({
    before_revision: 'aaa', after_revision: 'bbb', currency: 'AUD', has_changes: true,
    summary: ['CSV updated'], setting_changes: [], library_changes: [{ field: 'rate_library.sha256', description: 'CSV library content changed.' }],
    totals: { before: 7600, after: 8000, net_change: 400, additions: 400, omissions: 0, quantity_effect: 0, rate_effect: 400, rounding_effect: 0, library_rate_effect: 400, override_rate_effect: 0, unattributed_rate_effect: 0 },
    items: [{ code: 'FLOOR', description: 'Library floor rate changed.', before_quantity: 80, after_quantity: 80, before_rate: 95, after_rate: 100, before_amount: 7600, after_amount: 8000, cost_delta: 400, unit: 'm2', before_rate_origin: 'library', after_rate_origin: 'library', before_rate_source: 'CSV source A', after_rate_source: 'CSV source B', before_rate_effective_date: '2026-11-17', after_rate_effective_date: '2026-11-18', library_rate_effect: 400, override_rate_effect: 0 }],
  });
  assert.equal(document.querySelector('#comparison-summary').children.length, 0);
  assert.equal(document.querySelector('#comparison-library-section').hidden, false);
  assert.match(document.querySelector('#comparison-library-summary').children[0].textContent, /CSV library content changed/);
  assert.match(document.querySelector('#comparison-no-changes').textContent, /No design or override settings changed/);
  const details = document.querySelector('#comparison-items').children[0].children[0].children.map(child => child.textContent).join(' ');
  assert.match(details, /CSV source A.*CSV source B/); assert.match(details, /Library rate effect.*400/);
  assert.match(document.querySelector('#compare-library-effect').textContent, /400/);
  assert.match(document.querySelector('#compare-override-effect').textContent, /0/);
});

test('spec previews both wall swatches and keeps the saved schedule accessible until update', t => {
  const saved = { ...modern(), exterior_finish: { kind: 'facebrick', name: 'Terracotta', hex: '#AD684F' } };
  state.result = { settings: structuredClone(saved), revision: 'original',
    rooms: [{ name: 'Living', id: 'ROOM1', area_m2: 80, finishes: { paint: 'Light blue' } }] };
  const panel = document.querySelector('#panel-spec');
  panel.hidden = false;
  t.after(() => { panel.hidden = true; state.result = null; state.dirty = false; });
  const content = document.querySelector('#spec-content');
  const sections = (parent = content) => parent.querySelectorAll('section').map(section => section.children[0].textContent);
  const wallDescriptions = (parent = content) => parent.querySelectorAll('section').find(section => section.children[0].textContent.includes('Wall finishes')).children.slice(1);
  const colours = (parent = content) => wallDescriptions(parent).map(p => p.children[0].style.backgroundColor);
  const draft = { ...saved, inner_leaf_finish: 'bare_brick', inner_brick_hex: '#996622',
    exterior_finish: { kind: 'rendered_paint', name: 'Ochre', hex: '#AA6633' },
    rooms: [{ id: 'ROOM1', name: 'Living', finishes: { floor: 'Oak', paint: 'Chalk' } }] };
  fillForm(draft); setDirty();
  assert.match(document.querySelector('#spec-revision').textContent, /Draft finish preview/);
  assert.deepEqual(sections(), ['Wall finishes']);
  assert.deepEqual(colours(), ['#AA6633', '#996622']);
  assert.match(wallDescriptions()[0].textContent, /Render and paint.*Ochre/);
  assert.match(wallDescriptions()[1].textContent, /Bare common brick/);
  const prior = content.querySelectorAll('details')[0];
  assert.equal(prior.open, false);
  assert.match(prior.children[0].textContent, /Saved specification.*revision original/);
  assert.ok(sections(prior).includes('Room finishes schedule'));
  assert.deepEqual(colours(prior), ['#AD684F', '#ADD8E6']);
  fillForm(saved); setDirty(false);
  assert.match(document.querySelector('#spec-revision').textContent, /Design revision original/);
  assert.ok(sections().some(heading => heading.includes('Quantities & pricing')));
  assert.equal(content.querySelectorAll('details').length, 0);
  assert.deepEqual(colours(), ['#AD684F', '#ADD8E6']);
});

test('Rates panel override editor and reset share sidebar state and reflect load/undo', () => {
  const before = { ...modern(), rate_overrides: { DOOR: 700 } }; fillForm(before);
  rateLibraryState.current = library(); state.result = { settings: before, revision: 'saved', rate_library: library() }; state.busy = false; renderRates();
  const panelInput = document.querySelector('#rates-override-door');
  assert.equal(panelInput.value, '700'); assert.equal(document.querySelector('#rates-override-currency-door').textContent, 'AUD/each');
  panelInput.value = '725'; panelInput.trigger('input');
  assert.equal(document.querySelector('#override-door').value, '725'); assert.equal(settingsFromForm().rate_overrides.DOOR, 725);
  document.querySelector('#rates-reset-door').trigger('click');
  assert.equal(panelInput.value, ''); assert.equal(document.querySelector('#override-door').value, '');
  assert.equal(Object.hasOwn(settingsFromForm().rate_overrides, 'DOOR'), false); assert.equal(document.querySelector('#rates-reset-door').disabled, true);
  const history = new DraftHistory(before); history.push({ ...before, rate_overrides: { DOOR: 800 } });
  fillForm(history.current); assert.equal(panelInput.value, '800');
  fillForm(history.undo()); assert.equal(panelInput.value, '700');
  fillForm({ ...modern(), currency: 'SGD', rate_overrides: { FLOOR: 0 } });
  assert.equal(document.querySelector('#rates-override-floor').value, '0');
  assert.equal(document.querySelector('#rates-override-currency-floor').textContent, 'SGD/m2');
  assert.equal(document.querySelector('#rate-library-body').children[0].children[4].textContent, 'AUD'); state.result = null;
});
