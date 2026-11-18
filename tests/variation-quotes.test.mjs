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
  constructor() { this.dataset = {}; this.value = ''; this.children = []; this.options = []; this.textContent = ''; this.classList = { toggle() {}, add() {}, remove() {} }; this.listeners = {}; this.attributes = {}; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  set id(value) { this._id = value; elements.set('#' + value, this); }
  get id() { return this._id; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'href') delete this.href; }
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
  querySelectorAll(selector) { return selector === '[data-change-file]' ? [document.querySelector('#export-client-quote'), document.querySelector('#export-variation-pdf')] : []; }, createElement() { return new Element(); },
};
const appSource = (await readFile(new URL('../housesync/static/app.js', import.meta.url), 'utf8'))
  .replace("import * as THREE from 'three';", 'const THREE = { Vector3: class {} };')
  .replace("import { OrbitControls } from '/vendor/OrbitControls.js';", '')
  .replace("import { PlanEditor } from './plan-editor.js';", '')
  .replace("from './plan-core.js'", "from '" + coreURL + "'")
  .split("form.addEventListener('submit', buildHouse);")[0]
  + '\nexport { fillForm, settingsFromForm, comparableSettings, stableSettings, addAllowance, removeAllowance, renderClientQuote, clientQuoteMatches, setComparisonDownloads, selectedComparisonChanged, compareSavedRevisions, comparison, state }; export function connectPlan(editor) { planEditor = editor; }';
const { fillForm, settingsFromForm, comparableSettings, stableSettings, addAllowance, removeAllowance, renderClientQuote, clientQuoteMatches, setComparisonDownloads, selectedComparisonChanged, compareSavedRevisions, comparison, state, connectPlan } = await import('data:text/javascript;base64,' + Buffer.from(appSource).toString('base64'));


globalThis.window = { location: { origin: 'http://127.0.0.1:8766' } };
const clientLink = document.querySelector('#export-client-quote'); clientLink.dataset = { changeFile: 'client_quote_pdf', clientQuote: 'true' };
const internalLink = document.querySelector('#export-variation-pdf'); internalLink.dataset = { changeFile: 'internal_breakdown_pdf' };
const defaults = () => ({ ...fixture(), currency: 'AUD', rate_overrides: {} });
const childrenText = node => [node.textContent, ...node.children.flatMap(child => childrenText(child))].join(' ');
const allowanceField = (card, key) => card.children.flatMap(child => child.children).find(child => child.dataset?.allowanceField === key);
const before = 'aaaaaaaaaaaa', after = 'bbbbbbbbbbbb';
const quoteResult = () => ({
  before_revision: before, after_revision: after, comparison_id: before + '_' + after + '_abc123', currency: 'AUD',
  files: { client_quote_pdf: '/changes/' + before + '_' + after + '_abc123/client-variation-quote.pdf', internal_breakdown_pdf: '/changes/' + before + '_' + after + '_abc123/internal-variation-breakdown.pdf' },
  quote: { public: { before_revision: before, after_revision: after, currency: 'AUD', status: 'draft', quote_reference: 'VQ-001', builder_name: 'Test Builder', client_description: 'Extend the house.', issued_on: '2026-11-18', valid_until: '2026-12-02', scope: ['Extend the house length.'], credit_policy: 'Credits at cost.', markup_label: 'Combined overhead and margin (markup on cost)', totals: { additions_at_cost: 1000, credits_at_cost: 100, at_cost_delta: 900, additions_markup: 200, credits_markup: 0, markup_amount: 200, admin_fee: 50, subtotal_ex_gst: 1150, gst_rate: 10, gst_amount: 115, total_incl_gst: 1265 }, pc_allowances: [{ id: 'AL1', description: 'Tapware allowance', before: 0, after: 250, movement: 250, notes: 'Selection pending.' }], ps_allowances: [] }, internal: { supplier_costs: 'INTERNAL-SUPPLIER-CANARY', provenance: 'PRIVATE-RATES-CANARY' } },
});
const choosePair = () => { document.querySelector('#compare-before').value = before; document.querySelector('#compare-after').value = after; };

test('old saved designs receive quote defaults without a false dirty or draft change', () => {
  fillForm({ ...defaults(), builder_pricing: { overhead_margin_percent: 33, admin_fee_aud: 75, validity_days: 30, builder_name: 'Old builder' }, allowances: [{ id: 'AL9', kind: 'prime_cost', description: 'Old allowance', amount: 100, note: '' }] });
  fillForm(defaults()); const submitted = settingsFromForm();
  assert.deepEqual(submitted.builder_pricing, { overhead_margin_percent: 20, admin_fee_aud: 0, gst_percent: 10, credit_treatment: 'at_cost', validity_days: 14, builder_name: '', client_description: '' });
  assert.deepEqual(submitted.allowances, []); assert.equal(stableSettings(submitted), stableSettings(comparableSettings(defaults())));
  assert.deepEqual(describeChanges(defaults(), submitted), []);
});

test('estimator form serializes revision-B pricing and fixed GST through the real form path', () => {
  fillForm(defaults());
  for (const [id, value] of [['#quote-markup','25.5'],['#quote-admin','120.25'],['#quote-credit-treatment','including_margin'],['#quote-validity','21'],['#builder-name','Builder Pty Ltd'],['#client-description','Add a window.\nUse a blue finish.'],['#quote-gst','99']]) document.querySelector(id).value = value;
  const pricing = settingsFromForm().builder_pricing;
  assert.equal(pricing.overhead_margin_percent,25.5); assert.equal(pricing.admin_fee_aud,120.25); assert.equal(pricing.gst_percent,10);
  assert.equal(pricing.credit_treatment,'including_margin'); assert.equal(pricing.validity_days,21); assert.equal(pricing.builder_name,'Builder Pty Ltd'); assert.match(pricing.client_description,/\n/);
  const reopened = JSON.parse(JSON.stringify(settingsFromForm())); fillForm(reopened); assert.deepEqual(settingsFromForm().builder_pricing,pricing);
});

test('PC and PS rows edit through actual controls, keep stable IDs and remove independently', () => {
  fillForm(defaults()); state.result = { settings: settingsFromForm(), revision: after }; state.busy = false; comparison.revisions = [];
  addAllowance('prime_cost'); addAllowance('provisional_sum');
  const cards = document.querySelector('#quote-allowances').children; const firstId = cards[0].dataset.allowanceId, secondId = cards[1].dataset.allowanceId;
  assert.notEqual(firstId,secondId);
  for (const [key,value] of [['description','Kitchen tapware'],['amount','425.50'],['note','Selection pending.']]) { const input = allowanceField(cards[0],key); input.value=value; input.trigger('input'); }
  assert.deepEqual(settingsFromForm().allowances[0],{id:firstId,kind:'prime_cost',description:'Kitchen tapware',amount:425.5,note:'Selection pending.'});
  removeAllowance(firstId); assert.deepEqual(settingsFromForm().allowances.map(row=>row.id),[secondId]);
  addAllowance('prime_cost'); assert.notEqual(settingsFromForm().allowances[1].id,firstId); state.result=null;
});

test('quote and allowance edits participate in full plan undo and redo', async () => {
  const controllerSource = (await readFile(new URL('../housesync/static/plan-editor.js',import.meta.url),'utf8')).replace("from './plan-core.js'", "from '"+coreURL+"'");
  const {PlanEditor}=await import('data:text/javascript;base64,'+Buffer.from(controllerSource).toString('base64'));
  fillForm(defaults()); const initial=settingsFromForm(); const editor=Object.create(PlanEditor.prototype);
  Object.assign(editor,{draft:structuredClone(initial),baseline:structuredClone(initial),history:new DraftHistory(initial),reservedIds:new Set(),selected:null,busy:false,gesture:null,feedback:{textContent:''},callbacks:{onChange(settings){fillForm(settings);}},render(){},renderCanvas(){},renderInspector(){},renderAudit(){},fit(){}});
  connectPlan(editor); addAllowance('prime_cost'); document.querySelector('#quote-markup').value='35';
  const current=settingsFromForm(); editor.setDraft(current);
  editor.action('undo'); assert.equal(settingsFromForm().builder_pricing.overhead_margin_percent,20); assert.equal(settingsFromForm().allowances.length,1);
  editor.action('undo'); assert.equal(settingsFromForm().allowances.length,0);
  editor.action('redo'); assert.equal(settingsFromForm().allowances.length,1); connectPlan(null);
});

test('client summary renders only the public quote whitelist and allowance notes', () => {
  const result=quoteResult(); renderClientQuote(result);
  assert.equal(document.querySelector('#client-quote-summary').hidden,false);
  const text=['#quote-waterfall','#quote-description','#quote-allowance-lines','#quote-scope'].map(id=>childrenText(document.querySelector(id))).join(' ');
  assert.match(text,/1,265.00/); assert.match(text,/-[^0-9]*100\.00/); assert.match(text,/Tapware allowance/); assert.match(text,/Selection pending/);
  assert.doesNotMatch(text,/CANARY|supplier_costs|provenance/); assert.match(document.querySelector('#quote-saved-revisions').textContent,/bbbbbbbbbbbb/);
});

test('client download is bound to the selected saved A/B and the dedicated client filename only', () => {
  choosePair(); const result=quoteResult(); comparison.result=result; comparison.busy=false; state.busy=false; state.dirty=false;
  assert.equal(clientQuoteMatches(result),true); setComparisonDownloads(); assert.match(clientLink.href,/client-variation-quote.pdf$/);
  for (const bad of [
    {...result,quote:{public:{...result.quote.public,after_revision:'cccccccccccc'}}},
    {...result,files:{...result.files,client_quote_pdf:result.files.internal_breakdown_pdf}},
    {...result,files:{...result.files,client_quote_pdf:'https://example.com/client-variation-quote.pdf'}},
    {...result,comparison_id:'cccccccccccc_dddddddddddd_abc123'},
  ]) {comparison.result=bad; setComparisonDownloads(); assert.equal(clientQuoteMatches(bad),false); assert.equal(clientLink.getAttribute('aria-disabled'),'true'); assert.equal(clientLink.href,undefined);}
  comparison.result=result; state.dirty=true; setComparisonDownloads(); assert.equal(clientLink.getAttribute('aria-disabled'),'true'); assert.equal(internalLink.getAttribute('aria-disabled'),'false');
  state.dirty=false; selectedComparisonChanged(); assert.equal(clientLink.getAttribute('aria-disabled'),'true'); assert.equal(document.querySelector('#client-quote-summary').hidden,true);
});

test('a late asynchronous comparison cannot publish a client quote after selection changes', async () => {
  choosePair(); comparison.result=null; state.dirty=false; let resolve;
  globalThis.fetch=()=>new Promise(done=>{resolve=done;}); const pending=compareSavedRevisions();
  document.querySelector('#compare-after').value='cccccccccccc'; selectedComparisonChanged();
  resolve({ok:true,json:async()=>quoteResult()}); await pending;
  assert.equal(comparison.result,null); assert.equal(clientLink.getAttribute('aria-disabled'),'true'); assert.equal(document.querySelector('#client-quote-summary').hidden,true);
});

test('actual quote scope containing the client description is displayed once', () => {
  const result=quoteResult(); result.quote.public.client_description='Extend the house by two metres.'; result.quote.public.scope=[result.quote.public.client_description];
  renderClientQuote(result);
  assert.equal(document.querySelector('#quote-description').textContent,'Extend the house by two metres.');
  assert.equal(document.querySelector('#quote-description').hidden,false);
  assert.equal(document.querySelector('#quote-scope').children.length,0); assert.equal(document.querySelector('#quote-scope').hidden,true);
});
