import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';

const $ = (selector) => document.querySelector(selector);
const form = $('#house-form');
const state = { result: null, dirty: false, busy: false, selected: null, settingsURL: null };
const fields = ['#project-name', '#length', '#width', '#height', '#thickness', '#paint-colour', '#paint-hex', '#currency', '#rate-floor', '#rate-wall', '#rate-roof', '#rate-paint', '#rate-opening'];
const rateFields = { floor_m2: '#rate-floor', wall_m3: '#rate-wall', roof_m2: '#rate-roof', paint_m2: '#rate-paint', opening_each: '#rate-opening' };
const viewer = { scene: null, camera: null, renderer: null, controls: null, group: null, parts: [], selected: null, size: 12, target: new THREE.Vector3() };
const number = (value, digits = 2) => Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const money = (value, currency = 'SGD') => `${currency} ${number(value)}`;

function settingsFromForm() {
  return { project_name: $('#project-name').value.trim(), length_mm: Math.round(Number($('#length').value) * 1000), width_mm: Math.round(Number($('#width').value) * 1000), height_mm: Math.round(Number($('#height').value) * 1000), wall_thickness_mm: Math.round(Number($('#thickness').value) * 1000), paint_colour: $('#paint-colour').value.trim(), paint_hex: $('#paint-hex').value.toUpperCase(), currency: $('#currency').value, rates: Object.fromEntries(Object.entries(rateFields).map(([key, id]) => [key, Number($(id).value)])) };
}

function fillForm(settings) {
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

function setDirty() {
  state.dirty = true;
  $('#sync-status').textContent = 'Changes ready to update';
  $('#sync-status').classList.add('pending');
  $('#form-status').textContent = 'Update to regenerate the model and all documents.';
  setDownloadLinks();
}

function setBusy(value) {
  state.busy = value;
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
  state.result = result;
  state.dirty = false;
  if (populate) fillForm(result.settings);
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
  const openingDescriptions = (drawing.openings || []).map(opening => `${opening.id} on ${opening.host}: ${number(opening.width_mm, 0)} mm wide × ${number(opening.height_mm, 0)} mm high${opening.sill_mm ? `; sill ${number(opening.sill_mm, 0)} mm` : ''}.`);
  section('03 · Openings', openingDescriptions.length ? openingDescriptions : ['One entrance door and two windows are shown in the concept model. Refer to the drawing schedule for sizes.']);
  section('04 · Quantities & pricing', [result.measurement_rule || 'Quantities are derived from the compiled house geometry. Paint excludes openings and reveals.', `BOQ values use editable illustrative allowances in ${settings.currency}; they do not represent a complete tender estimate. All issued files should retain design revision ${result.revision}.`]);
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
fields.forEach(id => $(id).addEventListener('input', () => { updateSwatches(); setDirty(); }));
document.querySelectorAll('.paint-swatch').forEach(button => button.addEventListener('click', () => { $('#paint-hex').value = button.dataset.colour; $('#paint-colour').value = button.dataset.name; updateSwatches(); setDirty(); }));
document.querySelectorAll('.view-tab').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.view-tab').forEach(tab => { const active = tab === button; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); });
  document.querySelectorAll('.view-panel').forEach(panel => { panel.hidden = panel.id !== `panel-${button.dataset.panel}`; });
}));
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
    fillForm(settings); setDirty(); errorMessage(''); $('#form-status').textContent = 'Design loaded. Update to generate its model and documents.';
  } catch (error) { errorMessage(error.message); }
  event.target.value = '';
});

initializeViewer();
setBusy(true);
try { resultReceived(await fetchJSON('/api/state'), true); }
catch (error) { errorMessage(error.message); $('#form-status').textContent = 'Use Update house & documents to try generating your house.'; $('#sync-status').textContent = 'Ready to generate'; }
finally { setBusy(false); }
