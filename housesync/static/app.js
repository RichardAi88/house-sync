import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';
import { PlanEditor } from './plan-editor.js';

const $ = (selector) => document.querySelector(selector);
const form = $('#house-form');
const state = { result: null, dirty: false, busy: false, selected: null, settingsURL: null };
let planEditor = null;
const legacyOpenings = () => [
  { id: 'D1', kind: 'door', host: 'W1', offset_mm: null, width_mm: 1000, height_mm: 2100, sill_mm: 0 },
  { id: 'N1', kind: 'window', host: 'W3', offset_mm: null, width_mm: 1500, height_mm: 1200, sill_mm: 900 },
  { id: 'N2', kind: 'window', host: 'W2', offset_mm: null, width_mm: 1500, height_mm: 1200, sill_mm: 900 },
];
let draftOpenings = legacyOpenings();
const stableSettings = value => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
const comparableSettings = settings => ({ ...settings, openings: settings.openings ?? legacyOpenings() });
const comparison = { revisions: [], result: null, busy: false, request: 0, refresh: 0, selection: 0 };
const fields = ['#project-name', '#length', '#width', '#height', '#thickness', '#paint-colour', '#paint-hex', '#currency', '#rate-floor', '#rate-wall', '#rate-roof', '#rate-paint', '#rate-opening'];
const rateFields = { floor_m2: '#rate-floor', wall_m3: '#rate-wall', roof_m2: '#rate-roof', paint_m2: '#rate-paint', opening_each: '#rate-opening' };
const viewer = { scene: null, camera: null, renderer: null, controls: null, group: null, parts: [], selected: null, size: 12, target: new THREE.Vector3() };
const number = (value, digits = 2) => Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const money = (value, currency = 'SGD') => `${currency} ${number(value)}`;

function settingsFromForm() {
  return { project_name: $('#project-name').value.trim(), length_mm: Math.round(Number($('#length').value) * 1000), width_mm: Math.round(Number($('#width').value) * 1000), height_mm: Math.round(Number($('#height').value) * 1000), wall_thickness_mm: Math.round(Number($('#thickness').value) * 1000), paint_colour: $('#paint-colour').value.trim(), paint_hex: $('#paint-hex').value.toUpperCase(), currency: $('#currency').value, rates: Object.fromEntries(Object.entries(rateFields).map(([key, id]) => [key, Number($(id).value)])), openings: structuredClone(draftOpenings) };
}

function fillForm(settings) {
  draftOpenings = structuredClone(settings.openings ?? legacyOpenings());
  if (settings.currency && ![...$('#currency').options].some(option => option.value === settings.currency)) {
    const option = document.createElement('option'); option.value = settings.currency; option.textContent = settings.currency; $('#currency').append(option);
  }
  const map = { '#project-name': settings.project_name, '#length': settings.length_mm / 1000, '#width': settings.width_mm / 1000, '#height': settings.height_mm / 1000, '#thickness': settings.wall_thickness_mm / 1000, '#paint-colour': settings.paint_colour, '#paint-hex': settings.paint_hex, '#currency': settings.currency };
  for (const [id, value] of Object.entries(map)) if (value !== undefined && value !== null) $(id).value = value;
  for (const [key, id] of Object.entries(rateFields)) if (settings.rates?.[key] !== undefined) $(id).value = settings.rates[key];
  updateSwatches();
}

function updateSwatches() {
  document.querySelectorAll('.paint-swatch').forEach(button => {
    const selected = button.dataset.colour.toLowerCase() === $('#paint-hex').value.toLowerCase();
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
}

function setDirty(syncPlan = true) {
  const settings = settingsFromForm();
  if (syncPlan) planEditor?.setDraft(settings);
  state.dirty = !state.result || stableSettings(settings) !== stableSettings(comparableSettings(state.result.settings));
  $('#sync-status').textContent = state.dirty ? 'Draft plan · update to coordinate outputs' : 'Model & documents in sync';
  $('#sync-status').classList.toggle('pending', state.dirty);
  $('#form-status').textContent = state.dirty ? 'Review the draft, then update the model and all documents.' : 'Saved locally. All outputs share this revision.';
  setDownloadLinks();
  $('#comparison-draft-note').hidden = !state.dirty;
}

function setBusy(value) {
  state.busy = value;
  form.querySelectorAll('input, select, button').forEach(element => { element.disabled = value; });
  $('#load-settings').disabled = value;
  planEditor?.setBusy(value);
  document.body.classList.toggle('busy', value);
  $('#build-button').disabled = value;
  $('#build-label').textContent = value ? 'Updating house & documents…' : 'Update house & documents';
  $('#house-form').setAttribute('aria-busy', String(value));
  if (value) {
    $('#form-status').textContent = 'Building geometry and generating your document set…';
    $('#sync-status').textContent = 'Updating all outputs';
    $('#sync-status').classList.add('pending');
  }
  setDownloadLinks();
  setComparisonDownloads();
}

function errorMessage(message) {
  const element = $('#error-message');
  element.textContent = message || '';
  element.hidden = !message;
}

function downloadURL(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin ? url.href : null;
  } catch { return null; }
}

function setDownloadLinks() {
  document.querySelectorAll('[data-file]').forEach(link => {
    const url = downloadURL(state.result?.files?.[link.dataset.file]);
    const enabled = Boolean(url && !state.dirty && !state.busy);
    link.classList.toggle('disabled', !enabled);
    link.setAttribute('aria-disabled', String(!enabled));
    if (enabled) { link.href = url; link.setAttribute('download', ''); link.tabIndex = 0; }
    else { link.removeAttribute('href'); link.removeAttribute('download'); link.tabIndex = -1; }
  });
  $('#save-settings').disabled = !state.result || state.dirty || state.busy;
}

function resultReceived(result, populate = false) {
  if (!result || !result.settings || !result.revision) throw new Error('The server returned an incomplete house revision.');
  const previousRevision = state.result?.revision;
  state.result = result;
  state.dirty = false;
  if (populate) fillForm(result.settings);
  if (populate) planEditor?.setDraft(settingsFromForm(), { resetHistory: true });
  planEditor?.setBaseline(result.settings, result.revision);
  $('#project-title').textContent = result.settings.project_name;
  $('#revision-label').textContent = `Revision ${result.revision}`;
  $('#sync-status').textContent = 'Model & documents in sync';
  $('#sync-status').classList.remove('pending');
  $('#form-status').textContent = 'Saved locally. All outputs share this revision.';
  $('#download-revision').textContent = `Revision ${result.revision} · generated together`;
  $('#model-dimensions').textContent = `${number(result.settings.length_mm / 1000, 1)} × ${number(result.settings.width_mm / 1000, 1)} m clear`;
  const quantities = result.quantities || {};
  metric('#metric-floor', quantities.floor_area_m2, 'm²');
  metric('#metric-paint', quantities.paint_area_m2, 'm²');
  $('#metric-openings').textContent = `${quantities.door_count ?? 1} door · ${quantities.window_count ?? 2} windows`;
  const currency = result.totals?.currency || result.settings.currency;
  const total = result.totals?.subtotal ?? result.totals?.total ?? 0;
  $('#metric-cost').textContent = money(total, currency);
  $('#boq-total').textContent = money(total, currency);
  renderBOQ(result);
  renderSpec(result);
  const drawing = downloadURL(result.files?.drawings_svg);
  if (drawing) $('#drawing-preview').src = `${drawing}${drawing.includes('?') ? '&' : '?'}revision=${encodeURIComponent(result.revision)}`;
  renderModel(result.mesh);
  setDownloadLinks();
  $('#comparison-draft-note').hidden = true;
  void refreshComparisons(result, previousRevision, !populate);
}

function metric(selector, value, unit) {
  const element = $(selector);
  element.replaceChildren(document.createTextNode(`${number(value)} `));
  const small = document.createElement('small');
  small.textContent = unit;
  element.append(small);
}

function renderBOQ(result) {
  const body = $('#boq-body');
  body.replaceChildren();
  for (const row of result.boq || []) {
    const tr = document.createElement('tr');
    const values = [row.code, row.description, number(row.quantity, 3), row.unit, number(row.rate), number(row.amount)];
    for (const value of values) { const td = document.createElement('td'); td.textContent = String(value ?? ''); tr.append(td); }
    body.append(tr);
  }
  $('#measurement-note').textContent = result.measurement_rule || result.measurement_rules || 'Paint includes internal and external net wall faces, deducting openings. Reveals, wastage and other construction work are excluded unless stated. Rates are illustrative allowances.';
}

function renderSpec(result) {
  const settings = result.settings;
  const drawing = result.drawing || {};
  $('#spec-title').textContent = settings.project_name;
  $('#spec-revision').textContent = `Design revision ${result.revision} · dimensions in millimetres`;
  const container = $('#spec-content');
  container.replaceChildren();
  const section = (title, paragraphs) => {
    const part = document.createElement('section'); part.className = 'spec-section';
    const heading = document.createElement('h4'); heading.textContent = title; part.append(heading);
    paragraphs.forEach(text => { const p = document.createElement('p'); p.textContent = text; part.append(p); });
    container.append(part);
    return part;
  };
  section('01 · Scope & setting out', [`Single-storey rectangular house with clear internal dimensions of ${number(settings.length_mm, 0)} × ${number(settings.width_mm, 0)} mm. Wall height ${number(settings.height_mm, 0)} mm; wall thickness ${number(settings.wall_thickness_mm, 0)} mm.`, 'Refer to the coordinated floor plan and elevations for element locations and overall dimensions.']);
  const finish = section('02 · Wall finish', [`Selected paint: ${settings.paint_colour}. Colour reference ${settings.paint_hex}. Apply the coordinated finish to the internal and external wall faces identified by this model.`, 'Confirm the manufacturer, product, substrate preparation, primer and coating system before construction.']);
  const colour = document.createElement('span'); colour.className = 'spec-colour'; colour.style.backgroundColor = settings.paint_hex; finish.querySelector('p').prepend(colour);
  const openingDescriptions = (drawing.openings || []).map(opening => `${opening.id} on ${opening.host}: ${number(opening.width_mm, 0)} mm wide × ${number(opening.height_mm, 0)} mm high; start ${number(opening.start_mm, 0)} mm from the host inner reference${opening.sill_mm ? `; sill ${number(opening.sill_mm, 0)} mm` : ''}.`);
  section('03 · Openings', openingDescriptions.length ? openingDescriptions : ['No doors or windows are included in this saved revision.']);
  section('04 · Quantities & pricing', [result.measurement_rule || 'Quantities are derived from the compiled house geometry. Paint excludes openings and reveals.', `BOQ values use editable illustrative allowances in ${settings.currency}; they do not represent a complete tender estimate. All issued files should retain design revision ${result.revision}.`]);
}

function signedMoney(value, currency) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'Not separated';
  const amount = Number(value);
  return `${amount > 0 ? '+' : amount < 0 ? '\u2212' : ''}${money(Math.abs(amount), currency)}`;
}

function deltaClass(value) {
  return Number(value) > 0 ? 'delta-positive' : Number(value) < 0 ? 'delta-negative' : 'delta-zero';
}

function comparisonPair() {
  return { before: $('#compare-before').value, after: $('#compare-after').value };
}

function rememberComparison() {
  try { localStorage.setItem('house-sync-comparison', JSON.stringify(comparisonPair())); } catch { /* Storage is optional. */ }
}

function savedComparisonPair() {
  try { return JSON.parse(localStorage.getItem('house-sync-comparison')) || {}; } catch { return {}; }
}

function comparisonError(message) {
  $('#comparison-error').textContent = message || '';
  $('#comparison-error').hidden = !message;
}

function setComparisonDownloads() {
  const pair = comparisonPair();
  const matches = comparison.result && comparison.result.before_revision === pair.before && comparison.result.after_revision === pair.after;
  document.querySelectorAll('[data-change-file]').forEach(link => {
    const url = downloadURL(comparison.result?.files?.[link.dataset.changeFile]);
    const enabled = Boolean(matches && !comparison.busy && !state.busy && url);
    link.classList.toggle('disabled', !enabled);
    link.setAttribute('aria-disabled', String(!enabled));
    if (enabled) { link.href = url; link.setAttribute('download', ''); link.tabIndex = 0; }
    else { link.removeAttribute('href'); link.removeAttribute('download'); link.tabIndex = -1; }
  });
  $('#compare-button').disabled = comparison.busy || !pair.before || !pair.after;
  $('#compare-button').textContent = comparison.busy ? 'Comparing...' : 'Compare revisions';
}

function selectedComparisonChanged() {
  comparison.selection += 1;
  comparison.request += 1;
  comparison.busy = false;
  comparison.result = null;
  $('#comparison-result').hidden = true;
  $('#change-ready-dot').hidden = true;
  $('#comparison-status').textContent = 'Select Compare revisions to capture this A to B change.';
  comparisonError('');
  rememberComparison();
  setComparisonDownloads();
}

function revisionLabel(record) {
  const settings = record.settings || {};
  const dimensions = settings.length_mm && settings.width_mm ? `${number(settings.length_mm / 1000, 1)} x ${number(settings.width_mm / 1000, 1)} m` : '';
  const date = record.created_at ? new Date(record.created_at) : null;
  const time = date && !Number.isNaN(date.getTime()) ? date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
  return [record.revision, dimensions, settings.paint_colour, time].filter(Boolean).join(' | ');
}

async function refreshComparisons(result, previousRevision, fromBuild = false) {
  const refresh = ++comparison.refresh;
  const selection = comparison.selection;
  comparison.request += 1;
  comparison.busy = false;
  const previousPair = comparisonPair();
  try {
    const revisions = await fetchJSON('/api/revisions');
    if (refresh !== comparison.refresh || state.result?.revision !== result.revision) return;
    comparison.revisions = Array.isArray(revisions) ? revisions : revisions.revisions || [];
    const valid = new Set(comparison.revisions.map(record => record.revision));
    const preferredPair = selection === comparison.selection ? previousPair : comparisonPair();
    for (const id of ['#compare-before', '#compare-after']) {
      const select = $(id); select.replaceChildren();
      for (const record of comparison.revisions) { const option = document.createElement('option'); option.value = record.revision; option.textContent = revisionLabel(record); select.append(option); }
      if (!select.options.length) { const option = document.createElement('option'); option.value = ''; option.textContent = 'No saved revision yet'; select.append(option); }
    }
    const remembered = savedComparisonPair();
    const autoBefore = result.previous_revision || previousRevision;
    const lastChange = result.last_change;
    const automatic = fromBuild && selection === comparison.selection && autoBefore && valid.has(autoBefore) && autoBefore !== result.revision;
    let before, after;
    if (automatic) { before = autoBefore; after = result.revision; }
    else if (valid.has(preferredPair.before) && valid.has(preferredPair.after)) { before = preferredPair.before; after = preferredPair.after; }
    else if (valid.has(remembered.before) && valid.has(remembered.after)) { before = remembered.before; after = remembered.after; }
    else if (lastChange && valid.has(lastChange.before_revision) && valid.has(lastChange.after_revision)) { before = lastChange.before_revision; after = lastChange.after_revision; }
    else {
      after = valid.has(result.revision) ? result.revision : comparison.revisions[0]?.revision;
      const candidates = comparison.revisions.filter(record => record.revision !== after && record.settings?.project_name === result.settings.project_name && record.settings?.currency === result.settings.currency);
      before = candidates[0]?.revision || comparison.revisions.find(record => record.revision !== after)?.revision || after;
    }
    $('#compare-before').value = before || '';
    $('#compare-after').value = after || '';
    rememberComparison();
    comparisonError('');
    setComparisonDownloads();
    if (lastChange && lastChange.before_revision === before && lastChange.after_revision === after) renderComparison(lastChange);
    else if (before && after) await compareSavedRevisions();
    else $('#comparison-status').textContent = 'Update the house to save a revision, then compare its changes here.';
  } catch (error) {
    if (refresh !== comparison.refresh) return;
    comparisonError(`Saved revisions could not be loaded: ${error.message}`);
    setComparisonDownloads();
  }
}

async function compareSavedRevisions() {
  const pair = comparisonPair();
  if (!pair.before || !pair.after) return;
  const request = ++comparison.request;
  comparison.busy = true;
  $('#comparison-result').hidden = true;
  $('#change-ready-dot').hidden = true;
  comparisonError('');
  $('#comparison-status').textContent = 'Comparing descriptions, quantities and unit rates...';
  setComparisonDownloads();
  try {
    const result = await fetchJSON(`/api/compare?before=${encodeURIComponent(pair.before)}&after=${encodeURIComponent(pair.after)}`);
    const current = comparisonPair();
    if (request !== comparison.request || current.before !== pair.before || current.after !== pair.after) return;
    if (result.before_revision !== pair.before || result.after_revision !== pair.after) throw new Error('The comparison returned different revisions. Compare again to refresh the report.');
    renderComparison(result);
  } catch (error) {
    if (request !== comparison.request) return;
    comparison.result = null;
    $('#comparison-result').hidden = true;
    $('#change-ready-dot').hidden = true;
    $('#comparison-status').textContent = 'Comparison needs attention.';
    comparisonError(error.message);
  } finally {
    if (request === comparison.request) { comparison.busy = false; setComparisonDownloads(); }
  }
}

function renderComparison(result) {
  comparison.result = result;
  const totals = result.totals || {};
  const currency = result.currency || 'SGD';
  $('#comparison-result').hidden = false;
  $('#compare-cost-before').textContent = money(totals.before, currency);
  $('#compare-cost-after').textContent = money(totals.after, currency);
  $('#compare-cost-delta').textContent = signedMoney(totals.net_change, currency);
  $('#compare-cost-delta').className = deltaClass(totals.net_change);
  $('#compare-net-label').textContent = totals.net_change > 0 ? 'Net estimate increase' : totals.net_change < 0 ? 'Net estimate saving' : 'No estimate change';
  $('#compare-additions').textContent = money(totals.additions, currency);
  $('#compare-omissions').textContent = money(Math.abs(totals.omissions || 0), currency);
  $('#comparison-revisions').textContent = `A ${result.before_revision} \u2192 B ${result.after_revision}`;
  $('#comparison-status').textContent = result.has_changes ? 'Change captured. The report describes the selected saved revisions.' : 'Comparison complete. No changes between these saved revisions.';
  $('#comparison-no-changes').hidden = Boolean(result.has_changes);
  $('#change-ready-dot').hidden = !result.has_changes;
  const summaries = Array.isArray(result.summary) && result.summary.length ? result.summary : (result.setting_changes || []).map(item => item.description || `${item.label}: ${item.before} \u2192 ${item.after}`);
  const list = $('#comparison-summary'); list.replaceChildren();
  for (const text of summaries) { const item = document.createElement('li'); item.textContent = String(text); list.append(item); }
  const body = $('#comparison-items'); body.replaceChildren();
  for (const item of result.items || []) {
    const row = document.createElement('tr');
    const description = document.createElement('td');
    const title = document.createElement('strong'); title.textContent = item.code || 'BOQ item'; description.append(title);
    const status = document.createElement('span'); status.className = 'change-item-status'; status.textContent = String(item.status || 'modified').replaceAll('_', ' '); description.append(status);
    const detail = document.createElement('p'); detail.textContent = item.description || item.after_description || item.before_description || ''; description.append(detail);
    if (item.before_description && item.after_description && item.before_description !== item.after_description) {
      const before = document.createElement('p'); before.className = 'change-description-before'; before.textContent = `A: ${item.before_description}`; description.append(before);
      const after = document.createElement('p'); after.className = 'change-description-after'; after.textContent = `B: ${item.after_description}`; description.append(after);
    }
    if (item.element_ids?.length) { const ids = document.createElement('small'); ids.textContent = `Elements: ${item.element_ids.join(', ')}`; description.append(ids); }
    row.append(description);
    const formatQuantity = value => value === null || value === undefined ? '\u2014' : number(value, 3);
    const formatRate = value => value === null || value === undefined ? '\u2014' : number(value);
    const beforeUnit = item.before_unit ?? item.unit ?? '';
    const afterUnit = item.after_unit ?? item.unit ?? '';
    const quantityChange = beforeUnit === afterUnit ? `${formatQuantity(item.before_quantity)} \u2192 ${formatQuantity(item.after_quantity)} ${afterUnit}` : `${formatQuantity(item.before_quantity)} ${beforeUnit} \u2192 ${formatQuantity(item.after_quantity)} ${afterUnit}`;
    const values = [quantityChange, `${formatRate(item.before_rate)} \u2192 ${formatRate(item.after_rate)}`, money(item.before_amount, currency), money(item.after_amount, currency), signedMoney(item.cost_delta, currency)];
    for (const [index, value] of values.entries()) { const cell = document.createElement('td'); cell.textContent = value; if (index === 4) cell.className = deltaClass(item.cost_delta); row.append(cell); }
    body.append(row);
  }
  if (!body.children.length) { const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan = 6; cell.className = 'no-boq-changes'; cell.textContent = result.has_changes ? 'No BOQ price, quantity or description changes. See the design changes above.' : 'No BOQ changes.'; row.append(cell); body.append(row); }
  $('#comparison-table-total').textContent = signedMoney(totals.net_change, currency);
  $('#comparison-table-total').className = deltaClass(totals.net_change);
  for (const [id, key] of [['#compare-quantity-effect', 'quantity_effect'], ['#compare-rate-effect', 'rate_effect'], ['#compare-rounding-effect', 'rounding_effect']]) $(id).textContent = signedMoney(totals[key], currency);
  const notes = $('#comparison-notes'); notes.replaceChildren();
  const noteTexts = Array.isArray(result.notes) ? result.notes : result.notes ? [result.notes] : [];
  for (const text of noteTexts) { const item = document.createElement('li'); item.textContent = String(text); notes.append(item); }
  rememberComparison();
  setComparisonDownloads();
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { throw new Error(`The server did not return a valid response (${response.status}).`); }
  if (!response.ok) {
    let message = data.error || data.detail || `Request failed (${response.status}).`;
    if (Array.isArray(message)) message = message.map(item => `${(item.loc || []).join('.')}: ${item.msg || item}`).join('; ');
    if (typeof message === 'object') message = JSON.stringify(message);
    throw new Error(message);
  }
  return data;
}

async function buildHouse(event) {
  event?.preventDefault();
  if (state.busy || !form.reportValidity()) return;
  const planIssues = planEditor?.audit() || [];
  if (planIssues.length) { errorMessage('Plan needs attention: ' + planIssues.join(' ')); showView('plan'); return; }
  errorMessage('');
  const submitted = settingsFromForm();
  setBusy(true);
  try {
    const result = await fetchJSON('/api/build', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(submitted) });
    const current = JSON.stringify(settingsFromForm());
    resultReceived(result);
    if (current !== JSON.stringify(submitted)) setDirty();
  } catch (error) {
    errorMessage(error.message);
    $('#form-status').textContent = 'The previous revision is preserved. Check the values and try again.';
    $('#sync-status').textContent = 'Update needs attention';
    $('#sync-status').classList.add('pending');
  } finally { setBusy(false); }
}

function initializeViewer() {
  const viewport = $('#viewport');
  try {
    viewer.scene = new THREE.Scene();
    viewer.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
    viewer.camera.up.set(0, 0, 1);
    viewer.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    viewer.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    viewer.renderer.outputColorSpace = THREE.SRGBColorSpace;
    viewer.renderer.shadowMap.enabled = true;
    viewer.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    viewport.append(viewer.renderer.domElement);
    viewer.scene.add(new THREE.AmbientLight(0xffffff, 1.9));
    const sun = new THREE.DirectionalLight(0xfff7e7, 3.1);
    sun.position.set(-9, -13, 19); sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.left = -40; sun.shadow.camera.right = 40; sun.shadow.camera.top = 40; sun.shadow.camera.bottom = -40;
    sun.shadow.normalBias = .025; viewer.scene.add(sun);
    const fill = new THREE.DirectionalLight(0xc8e6ee, 1.25); fill.position.set(12, 8, 10); viewer.scene.add(fill);
    const grid = new THREE.GridHelper(150, 150, 0xc5d4cc, 0xdbe5df); grid.rotation.x = Math.PI / 2; grid.position.z = -.20; grid.material.transparent = true; grid.material.opacity = .28; viewer.scene.add(grid);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(160, 160), new THREE.ShadowMaterial({ opacity: .12 })); ground.position.z = -.17; ground.receiveShadow = true; viewer.scene.add(ground);
    viewer.controls = new OrbitControls(viewer.camera, viewer.renderer.domElement);
    viewer.controls.enableDamping = true; viewer.controls.dampingFactor = .08; viewer.controls.maxPolarAngle = Math.PI / 2 - .02;
    viewer.controls.minDistance = 2; viewer.controls.maxDistance = 160;
    const resize = () => { const width = viewport.clientWidth, height = viewport.clientHeight; if (width && height) { viewer.renderer.setSize(width, height); viewer.camera.aspect = width / height; viewer.camera.updateProjectionMatrix(); } };
    new ResizeObserver(resize).observe(viewport);
    viewer.renderer.setAnimationLoop(() => { viewer.controls.update(); viewer.renderer.render(viewer.scene, viewer.camera); });
    let pointerDown = null;
    viewport.addEventListener('pointerdown', event => { pointerDown = [event.clientX, event.clientY]; });
    viewport.addEventListener('pointerup', event => {
      if (!pointerDown || Math.hypot(event.clientX - pointerDown[0], event.clientY - pointerDown[1]) > 5) return;
      const rect = viewport.getBoundingClientRect();
      const ray = new THREE.Raycaster(); ray.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), viewer.camera);
      const hit = ray.intersectObjects(viewer.parts.filter(part => part.visible), false)[0];
      if (viewer.selected) viewer.selected.material.emissive.set(0x000000);
      viewer.selected = hit?.object || null;
      if (viewer.selected) { viewer.selected.material.emissive.set(0x143d36); $('#selected-element').textContent = `${viewer.selected.userData.id} · ${viewer.selected.userData.kind}`; }
      else $('#selected-element').textContent = 'Select an element to inspect';
    });
    resetCamera();
  } catch (error) {
    viewer.renderer = null;
    $('#viewer-message').textContent = '3D display is unavailable in this browser. You can still edit the house and use the drawings and documents.';
    console.error('House Sync viewer:', error);
  }
}

function resetCamera() {
  if (!viewer.camera) return;
  const size = viewer.size;
  viewer.camera.position.copy(viewer.target).add(new THREE.Vector3(size * .95, -size * 1.32, size * 1.08));
  viewer.controls?.target.copy(viewer.target);
  viewer.controls?.update();
}

function renderModel(mesh) {
  if (!viewer.renderer) return;
  const keepView = Boolean(viewer.group);
  const previousTarget = viewer.target.clone();
  const previousSize = viewer.size;
  if (viewer.group) {
    viewer.scene.remove(viewer.group);
    viewer.group.traverse(object => { object.geometry?.dispose(); if (Array.isArray(object.material)) object.material.forEach(material => material.dispose()); else object.material?.dispose(); });
  }
  viewer.group = new THREE.Group(); viewer.parts = []; viewer.selected = null;
  for (const item of mesh?.objects || []) {
    if (!item.vertices?.length || !item.faces?.length) continue;
    const vertices = Array.isArray(item.vertices[0]) ? item.vertices.flat() : item.vertices;
    const faces = Array.isArray(item.faces[0]) ? item.faces.flat() : item.faces;
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3)); geometry.setIndex(faces); geometry.computeVertexNormals();
    const isGlass = /glaz|glass/i.test(`${item.kind} ${item.id}`);
    const material = new THREE.MeshStandardMaterial({ color: item.colour || '#d5ded6', roughness: isGlass ? .14 : .83, metalness: isGlass ? .05 : 0, side: THREE.DoubleSide, transparent: isGlass, opacity: isGlass ? .48 : 1 });
    const part = new THREE.Mesh(geometry, material); part.userData = { id: item.id, kind: item.kind }; part.castShadow = !isGlass; part.receiveShadow = true;
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 35), new THREE.LineBasicMaterial({ color: 0x485e55, transparent: true, opacity: .18 })); part.add(edges);
    viewer.parts.push(part); viewer.group.add(part);
  }
  viewer.scene.add(viewer.group);
  const bounds = new THREE.Box3().setFromObject(viewer.group);
  if (!bounds.isEmpty()) { const size = bounds.getSize(new THREE.Vector3()); viewer.target.copy(bounds.getCenter(new THREE.Vector3())); viewer.target.z = size.z * .35; viewer.size = Math.max(size.x, size.y, size.z, 5); }
  roofVisibility();
  if (!keepView) resetCamera();
  else {
    const offset = viewer.camera.position.clone().sub(previousTarget).multiplyScalar(viewer.size / previousSize);
    viewer.camera.position.copy(viewer.target).add(offset);
    viewer.controls.target.copy(viewer.target);
    viewer.controls.update();
  }
  $('#viewer-message').hidden = Boolean(viewer.parts.length);
  $('#selected-element').textContent = 'Select an element to inspect';
}

function roofVisibility() {
  for (const part of viewer.parts) if (/roof/i.test(`${part.userData.kind} ${part.userData.id}`)) part.visible = $('#roof-toggle').checked;
}

form.addEventListener('submit', buildHouse);
$('#compare-before').addEventListener('change', selectedComparisonChanged);
$('#compare-after').addEventListener('change', selectedComparisonChanged);
$('#compare-button').addEventListener('click', compareSavedRevisions);
document.querySelectorAll('[data-change-file]').forEach(link => link.addEventListener('click', event => { if (link.getAttribute('aria-disabled') === 'true') event.preventDefault(); }));
fields.forEach(id => $(id).addEventListener('input', () => { updateSwatches(); setDirty(); }));
document.querySelectorAll('.paint-swatch').forEach(button => button.addEventListener('click', () => { $('#paint-hex').value = button.dataset.colour; $('#paint-colour').value = button.dataset.name; updateSwatches(); setDirty(); }));
function showView(name) {
  document.querySelectorAll('.view-tab').forEach(tab => { const active = tab.dataset.panel === name; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); });
  document.querySelectorAll('.view-panel').forEach(panel => { panel.hidden = panel.id !== 'panel-' + name; });
  if (name === 'plan') planEditor?.focus();
}
document.querySelectorAll('.view-tab').forEach(button => button.addEventListener('click', () => showView(button.dataset.panel)));

document.querySelectorAll('[data-file]').forEach(link => link.addEventListener('click', event => { if (link.getAttribute('aria-disabled') === 'true') event.preventDefault(); }));
$('#roof-toggle').addEventListener('change', roofVisibility);
$('#reset-camera').addEventListener('click', resetCamera);
$('#save-settings').addEventListener('click', () => {
  if (!state.result || state.dirty || state.busy) return;
  if (state.settingsURL) URL.revokeObjectURL(state.settingsURL);
  state.settingsURL = URL.createObjectURL(new Blob([JSON.stringify(state.result.settings, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = state.settingsURL; link.download = `house-sync-${state.result.revision}.json`; link.click();
});
$('#load-settings').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    if (file.size > 100000) throw new Error('Please choose a House Sync design JSON smaller than 100 KB.');
    const parsed = JSON.parse(await file.text()); const settings = parsed.settings || parsed;
    if (!settings.project_name || !Number.isFinite(settings.length_mm) || !Number.isFinite(settings.width_mm) || !Number.isFinite(settings.height_mm) || !Number.isFinite(settings.wall_thickness_mm) || !/^#[0-9a-f]{6}$/i.test(settings.paint_hex)) throw new Error('This file is missing valid house dimensions or a paint colour.');
    if (settings.openings !== undefined && !Array.isArray(settings.openings)) throw new Error('Opening data must be an array of doors/windows.');
    fillForm(settings); setDirty(); errorMessage(''); $('#form-status').textContent = 'Design loaded. Review the plan and update to generate its model and documents.';
  } catch (error) { errorMessage(error.message); }
  event.target.value = '';
});

function initialFormLoading(value) {
  form.querySelectorAll('input, select, button').forEach(element => { element.disabled = value; });
  $('#load-settings').disabled = value;
}

planEditor = new PlanEditor($('#plan-editor'), {
  onChange(settings) { if (state.busy) return; fillForm(settings); setDirty(false); },
  onApply() { void buildHouse(); },
  onReviewSaved() { showView('changes'); },
});
planEditor.setDraft(settingsFromForm(), { resetHistory: true });
initialFormLoading(true);
initializeViewer();
setBusy(true);
try { resultReceived(await fetchJSON('/api/state'), true); }
catch (error) { errorMessage(error.message); $('#form-status').textContent = 'Use Update house & documents to try generating your house.'; $('#sync-status').textContent = 'Ready to generate'; }
finally { initialFormLoading(false); setBusy(false); }
