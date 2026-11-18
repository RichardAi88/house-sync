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
  constructor() { this.value = ''; this.children = []; this.options = []; this.textContent = ''; this.classList = { toggle() {}, add() {}, remove() {} }; this.listeners = {}; this.attributes = {}; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  set id(value) { this._id = value; elements.set('#' + value, this); }
  get id() { return this._id; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  trigger(name) { this.listeners[name]?.({ target: this }); }
  append(...children) { this.children.push(...children); }
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
  querySelectorAll() { return []; }, createElement() { return new Element(); },
};
const appSource = (await readFile(new URL('../housesync/static/app.js', import.meta.url), 'utf8'))
  .replace("import * as THREE from 'three';", 'const THREE = { Vector3: class {} };')
  .replace("import { OrbitControls } from '/vendor/OrbitControls.js';", '')
  .replace("import { PlanEditor } from './plan-editor.js';", '')
  .replace("from './plan-core.js'", "from '" + coreURL + "'")
  .split("form.addEventListener('submit', buildHouse);")[0]
  + '\nexport { fillForm, settingsFromForm, comparableSettings, stableSettings, renderBOQ, renderRates, refreshRateLibrary, resetRateOverride, libraryDiffersFromRevision, rateLibraryState, state, renderComparison };';
const { fillForm, settingsFromForm, comparableSettings, stableSettings, renderBOQ, renderRates, refreshRateLibrary, resetRateOverride, libraryDiffersFromRevision, rateLibraryState, state, renderComparison } = await import('data:text/javascript;base64,' + Buffer.from(appSource).toString('base64'));

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
