import { HOSTS, HOST_LABELS, cloneSettings, hostLength, openingOffset, openingSegment, pointToOffset, snap, auditPlan, nextOpeningId, describeChanges, DraftHistory, planHosts, hostDirection, nextPartitionId, snapPoint, movePartition, resizePartition, deletePartition as removePartition } from './plan-core.js';

const NS = 'http://www.w3.org/2000/svg';
let editorCount = 0;
const copy = value => structuredClone(value);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const fmt = (value, digits = 0) => Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
const svgNode = (tag, attributes = {}, text) => {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = String(text);
  return node;
};
const htmlNode = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
};

export class PlanEditor {
  constructor(container, { onChange = () => {}, onApply = () => {}, onReviewSaved = () => {}, previewLayout = null } = {}) {
    this.root = container;
    this.callbacks = { onChange, onApply, onReviewSaved, previewLayout };
    this.draft = null; this.baseline = null; this.revision = '';
    this.history = null; this.selected = { kind: 'wall', id: 'W2' };
    this.busy = false; this.tool = 'select'; this.spacing = 100; this.spaceDown = false;
    this.gesture = null; this.measurement = null; this.reservedIds = new Set(); this.reservedPartitionIds = new Set();
    this.partitionStart = null; this.partitionPreview = null; this.layout = null; this.layoutKey = ''; this.layoutPending = false; this.layoutError = ''; this.layoutRequest = 0;
    this.view = { x: -1700, y: -10000, width: 14000, height: 11000 };
    this.uid = 'pe-' + ++editorCount;
    this.root.classList.add('plan-editor'); this.root.tabIndex = 0;
    // Only fixed interface markup is parsed. Project names and element text use textContent.
    this.root.innerHTML = '<div class="pe-header"><div><span class="pe-kicker">PLAN WORKSPACE</span><h3>Edit the house in plan</h3><p>Select walls and openings. Drag the grips or enter exact dimensions.</p></div><span class="pe-units">MILLIMETRES</span></div><div class="pe-toolbar" role="toolbar" aria-label="Plan drawing tools"><div class="pe-tools"><button type="button" data-tool="select" title="Select and move" aria-pressed="true">Select</button><button type="button" data-tool="pan" title="Pan the plan" aria-pressed="false">Pan</button><button type="button" data-tool="measure" title="Measure between two points" aria-pressed="false">Measure</button><button type="button" data-tool="partition" title="Click two points for an orthogonal partition" aria-pressed="false">+ Partition</button><button type="button" data-tool="door" title="Click a wall to add a door" aria-pressed="false">+ Door</button><button type="button" data-tool="window" title="Click a wall to add a window" aria-pressed="false">+ Window</button></div><div class="pe-view-tools"><button type="button" data-action="undo" title="Undo (Ctrl+Z)">Undo</button><button type="button" data-action="redo" title="Redo (Ctrl+Shift+Z)">Redo</button><button type="button" data-action="zoom-out" title="Zoom out" aria-label="Zoom out">−</button><button type="button" data-action="zoom-in" title="Zoom in" aria-label="Zoom in">+</button><button type="button" data-action="fit">Fit</button></div></div><div class="pe-options"><label>Snap <select class="pe-snap" aria-label="Plan snap spacing"><option value="0">Off</option><option value="10">10 mm</option><option value="50">50 mm</option><option value="100" selected>100 mm</option><option value="500">500 mm</option></select></label><label><input class="pe-show-baseline" type="checkbox" checked> Show saved plan</label><span class="pe-datum-note">Rectangular shell · southwest datum fixed</span></div><div class="pe-layout"><div class="pe-drawing"><svg class="pe-canvas" role="img" tabindex="0" aria-label="Editable house floor plan. Select walls or openings; drag east and north grips to resize."></svg><div class="pe-canvas-status"><span class="pe-pointer">X 0 · Y 0</span><span class="pe-legend"><i></i> Saved revision</span><span>Scroll to zoom · middle-drag to pan</span></div><p class="pe-feedback" aria-live="polite">Loading the saved plan…</p></div><aside class="pe-inspector" aria-label="Plan element properties"><form class="pe-property-form"></form></aside></div><div class="pe-bottom"><section class="pe-audit" aria-label="Geometry checks"><h4>Geometry checks <span class="pe-audit-count"></span></h4><ul class="pe-audit-list"></ul><p>Containment and spacing checks for this model. Engineering and regulatory review are separate.</p></section><section class="pe-review"><h4>Review draft changes <span class="pe-review-revision"></span></h4><ul class="pe-review-list"></ul><div class="pe-review-actions"><button type="button" data-action="review-saved">View saved change & cost</button><button type="button" class="pe-apply" data-action="apply">Update house &amp; documents</button></div><p>The plan is a draft until updated. The model, BOQ, specification and drawings then share one saved revision.</p></section></div>';
    this.svg = this.root.querySelector('.pe-canvas'); this.inspector = this.root.querySelector('.pe-property-form');
    this.feedback = this.root.querySelector('.pe-feedback');
    this.root.querySelectorAll('[data-tool]').forEach(button => button.addEventListener('click', () => this.setTool(button.dataset.tool)));
    this.root.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => this.action(button.dataset.action)));
    this.root.querySelector('.pe-snap').addEventListener('change', event => { this.spacing = Number(event.target.value); this.renderCanvas(); });
    this.root.querySelector('.pe-show-baseline').addEventListener('change', () => this.renderCanvas());
    this.inspector.addEventListener('submit', event => { event.preventDefault(); this.applyProperties(event.submitter?.dataset.action || 'properties'); });
    this.svg.addEventListener('pointerdown', event => this.pointerDown(event));
    this.svg.addEventListener('pointermove', event => this.pointerMove(event));
    this.svg.addEventListener('pointerup', event => this.pointerUp(event));
    this.svg.addEventListener('pointercancel', () => this.cancelGesture());
    this.svg.addEventListener('lostpointercapture', () => { if (this.gesture) this.cancelGesture(); });
    this.svg.addEventListener('wheel', event => { event.preventDefault(); this.zoom(event.deltaY > 0 ? 1.12 : 1 / 1.12, this.point(event)); }, { passive: false });
    this.svg.addEventListener('contextmenu', event => event.preventDefault());
    this.root.addEventListener('keydown', event => this.keyDown(event));
    this.root.addEventListener('keyup', event => { if (event.code === 'Space') { this.spaceDown = false; this.renderControls(); } });
    this.root.addEventListener('focusout', event => { if (!this.root.contains(event.relatedTarget)) this.spaceDown = false; });
    this.resizeObserver = new ResizeObserver(() => { if (!this.draft) return; this.applyView(); this.renderCanvas(); });
    this.resizeObserver.observe(this.svg);
    this.setBusy(true);
  }

  setDraft(settings, { resetHistory = false } = {}) {
    if (this.gesture) this.cancelGesture(false);
    const first = !this.history;
    this.draft = cloneSettings(settings || {});
    for (const item of this.openings()) if (item && typeof item.id === 'string') this.reservedIds.add(item.id);
    this.reservedPartitionIds ??= new Set(); for (const item of this.partitions()) this.reservedPartitionIds.add(item.id);
    if (first) this.history = new DraftHistory(this.draft);
    else if (resetHistory) this.history.reset(this.draft);
    else this.history.push(this.draft);
    if (this.selected?.kind === 'opening' && !this.openings().some(item => item?.id === this.selected.id)) this.selected = { kind: 'wall', id: 'W2' };
    if (first || resetHistory) this.fit();
    this.render(); void this.refreshLayout();
  }

  setBaseline(settings, revision) {
    this.baseline = cloneSettings(settings || {}); this.revision = revision || '';
    this.renderCanvas(); this.renderAudit();
  }

  setBusy(value) {
    this.busy = Boolean(value);
    if (this.busy && this.gesture) this.cancelGesture(false);
    this.root.setAttribute('aria-busy', String(this.busy));
    this.root.classList.toggle('pe-busy', this.busy);
    this.renderControls();
  }

  audit() {
    const issues = auditPlan(this.draft || {});
    if (this.callbacks?.previewLayout) {
      if (this.layoutPending) issues.push('Checking room layout and door access...');
      else if (this.layoutError) issues.push(this.layoutError);
      else if (this.layoutKey === JSON.stringify(this.draft)) issues.push(...(this.layout?.checks?.errors || []));
      else if (this.partitions().length) issues.push('Room layout needs a current backend preview.');
    }
    return [...new Set(issues)];
  }

  partitions(settings = this.draft) { return Array.isArray(settings?.partitions) ? settings.partitions.filter(item => item && typeof item.id === 'string') : []; }
  rooms() { return this.layoutKey === JSON.stringify(this.draft) && Array.isArray(this.layout?.rooms) ? this.layout.rooms : []; }

  async refreshLayout() {
    const request = this.layoutRequest = (this.layoutRequest || 0) + 1;
    const draft = copy(this.draft); const key = JSON.stringify(draft);
    if (!draft || !this.callbacks?.previewLayout) { this.layoutPending = false; return null; }
    this.layoutPending = true; this.layoutError = ''; this.renderAudit();
    try {
      const layout = await this.callbacks.previewLayout(draft);
      if (request !== this.layoutRequest || key !== JSON.stringify(this.draft)) return null;
      if (!layout || !Array.isArray(layout.rooms) || !layout.checks || !Array.isArray(layout.checks.errors)) throw new Error('Invalid room layout response.');
      this.layout = layout; this.layoutKey = key; this.layoutPending = false; this.cleanSelection();
      const editing = this.inspector && typeof document !== 'undefined' && this.inspector.contains?.(document.activeElement);
      this.renderCanvas(); this.renderAudit(); this.renderControls(); if (!editing) this.renderInspector(); return layout;
    } catch (error) {
      if (request !== this.layoutRequest || key !== JSON.stringify(this.draft)) return null;
      this.layoutPending = false; this.layoutError = 'Room preview unavailable: ' + error.message;
      this.renderAudit(); return null;
    }
  }

  selectPartition(id) { this.selected = { kind: 'partition', id }; this.renderInspector(); this.renderCanvas(); }

  partitionClick(point) {
    if (this.busy || !this.draft) return false;
    const clicked = snapPoint(this.draft, point, this.spacing);
    if (!this.partitionStart) { this.partitionStart = clicked; this.partitionPreview = null; this.feedback.textContent = 'Choose the second endpoint; the partition stays horizontal or vertical.'; this.renderCanvas(); return false; }
    const start = this.partitionStart; const end = { ...clicked };
    if (Math.abs(end.x - start.x) >= Math.abs(end.y - start.y)) end.y = start.y; else end.x = start.x;
    if (Math.hypot(end.x - start.x, end.y - start.y) < 100) { this.feedback.textContent = 'Choose an endpoint at least 100 mm away.'; return false; }
    const settings = copy(this.draft); settings.partitions ??= [];
    const id = nextPartitionId(settings, [...(this.reservedPartitionIds || [])]);
    settings.partitions.push({ id, start_mm: [start.x, start.y], end_mm: [end.x, end.y], thickness_mm: 100, height_mm: settings.height_mm, wall_type: 'partition' });
    const previous = this.selected; this.selected = { kind: 'partition', id };
    if (!this.commit(settings, 'Added ' + id + '. Complete connected walls and doors, then resolve the layout checks.')) { this.selected = previous; return false; }
    this.partitionStart = null; this.partitionPreview = null; this.renderCanvas(); return true;
  }

  deletePartition(id = this.selected?.id) {
    if (this.busy || !this.partitions().some(item => item.id === id)) return false;
    const candidate = removePartition(this.draft, id); this.selected = null;
    return this.commit(candidate, 'Removed ' + id + ' and its hosted openings. Review the resulting rooms.');
  }

  setRoomProperties(id, properties, base = this.draft) {
    const room = this.rooms().find(item => item.id === id);
    if (this.busy || !room) return false;
    const settings = copy(base); settings.rooms ??= [];
    const previous = settings.rooms.find(item => item.id === id);
    settings.rooms = settings.rooms.filter(item => item.id !== id);
    settings.rooms.push({ id, name: properties.name ?? previous?.name ?? room.name, finishes: { ...(previous?.finishes || {}), ...(properties.finishes || {}) } });
    return this.commit(settings, 'Updated ' + id + ' name and finish selections.');
  }
  openings(settings = this.draft) { return Array.isArray(settings?.openings) ? settings.openings.filter(item => item && typeof item === "object" && !Array.isArray(item)) : []; }
  focus() { this.svg.focus({ preventScroll: true }); }
  finite(value, fallback, min = -100000, max = 100000) { return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback; }
  displaySettings(settings = this.draft) {
    const source = settings || {};
    return { ...source, length_mm: this.finite(source.length_mm, 10000, 100, 30000), width_mm: this.finite(source.width_mm, 8000, 100, 30000), wall_thickness_mm: this.finite(source.wall_thickness_mm, 200, 20, 500), openings: Array.isArray(source.openings) ? source.openings : [] };
  }

  fit() {
    const settings = this.displaySettings(); const rect = this.svg.getBoundingClientRect();
    const aspect = (rect.width || 800) / (rect.height || 540);
    const pad = Math.max(1400, Math.max(settings.length_mm, settings.width_mm) * .12);
    const drawingWidth = settings.length_mm + pad * 2; const drawingHeight = settings.width_mm + pad * 2;
    const width = Math.max(drawingWidth, drawingHeight * aspect); const height = width / aspect;
    this.view = { x: settings.length_mm / 2 - width / 2, y: -settings.width_mm / 2 - height / 2, width, height };
    this.applyView(); this.renderCanvas();
  }

  applyView() {
    const rect = this.svg.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) this.view.height = this.view.width * rect.height / rect.width;
    this.svg.setAttribute('viewBox', [this.view.x, this.view.y, this.view.width, this.view.height].join(' '));
  }

  point(event) {
    const matrix = this.svg.getScreenCTM();
    if (!matrix) return { x: 0, y: 0 };
    const point = this.svg.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
    const transformed = point.matrixTransform(matrix.inverse());
    return { x: transformed.x, y: -transformed.y };
  }

  zoom(factor, anchor = null) {
    const width = Math.max(2500, Math.min(100000, this.view.width * factor));
    const ratio = width / this.view.width;
    const center = anchor ? { x: anchor.x, y: -anchor.y } : { x: this.view.x + this.view.width / 2, y: this.view.y + this.view.height / 2 };
    this.view.x = center.x + (this.view.x - center.x) * ratio;
    this.view.y = center.y + (this.view.y - center.y) * ratio;
    this.view.width = width; this.view.height *= ratio;
    this.applyView(); this.renderCanvas();
  }

  setTool(tool) {
    if (this.busy) return;
    this.cancelGesture(false); this.tool = tool; this.measurement = null; this.partitionStart = null; this.partitionPreview = null;
    this.feedback.textContent = ({ select: 'Select an element. Drag openings along their wall or resize using the north and east grips.', pan: 'Drag the plan to pan. Scroll to zoom.', measure: 'Click two points to measure. Escape clears the measurement.', partition: 'Click the start and end of an orthogonal partition. Snap to shell or partition centrelines.', door: 'Click a wall to place a door. Adjust its dimensions in the inspector.', window: 'Click a wall to place a window. Adjust its dimensions in the inspector.' })[tool];
    this.renderControls(); this.renderCanvas(); this.focus();
  }

  action(action) {
    if (action === 'fit') { this.fit(); return; }
    if (action === 'zoom-in' || action === 'zoom-out') { this.zoom(action === 'zoom-in' ? 1 / 1.2 : 1.2); return; }
    if (this.busy || !this.draft) return;
    if (action === 'undo' || action === 'redo') {
      this.cancelGesture(false);
      const settings = action === 'undo' ? this.history.undo() : this.history.redo();
      if (settings) { this.draft = settings; this.cleanSelection(); this.render(); this.callbacks.onChange(copy(settings)); void this.refreshLayout(); this.feedback.textContent = action === 'undo' ? 'Undid the last plan edit.' : 'Redid the plan edit.'; }
      return;
    }
    if (action === 'review-saved') { this.callbacks.onReviewSaved(); return; }
    if (action === 'apply') {
      const issues = this.audit();
      if (issues.length) { this.feedback.textContent = issues[0]; return; }
      this.callbacks.onApply();
    }
  }

  cleanSelection() {
    if (this.selected?.kind === 'opening' && !this.openings().some(item => item?.id === this.selected.id)) this.selected = { kind: 'wall', id: 'W2' };
    if (this.selected?.kind === 'partition' && !this.partitions().some(item => item.id === this.selected.id)) this.selected = null;
    if (this.selected?.kind === 'room' && !this.layoutPending && this.layoutKey === JSON.stringify(this.draft) && !this.rooms().some(item => item.id === this.selected.id)) this.selected = null;
  }

  commit(settings, message) {
    const candidate = cloneSettings(settings); const issues = auditPlan(candidate);
    if (issues.length) { this.feedback.textContent = issues[0]; return false; }
    this.draft = candidate; this.history.push(candidate); this.cleanSelection();
    for (const item of candidate.openings || []) this.reservedIds.add(item.id);
    this.reservedPartitionIds ??= new Set(); for (const item of this.partitions(candidate)) this.reservedPartitionIds.add(item.id);
    this.render(); this.feedback.textContent = message || 'Draft updated. Review the changes, then update the house and documents.';
    this.callbacks.onChange(copy(candidate)); void this.refreshLayout(); return true;
  }

  pointerDown(event) {
    if (!this.draft || this.busy || event.button > 1) return;
    this.focus(); const point = this.point(event);
    const element = event.target.closest('[data-pe-kind]');
    const kind = element?.dataset.peKind; const id = element?.dataset.peId;
    if (event.button === 1 || this.tool === 'pan' || this.spaceDown) {
      event.preventDefault(); this.gesture = { type: 'pan', pointer: event.pointerId, start: { x: event.clientX, y: event.clientY }, view: { ...this.view } }; this.svg.setPointerCapture(event.pointerId); return;
    }
    if (this.tool === 'partition') { this.partitionClick(point); return; }
    if (this.tool === 'measure') {
      const measured = { x: snap(point.x, this.spacing), y: snap(point.y, this.spacing) };
      if (!this.measurement || this.measurement.end) this.measurement = { start: measured, end: null };
      else this.measurement.end = measured;
      this.renderCanvas(); return;
    }
    if (this.tool === 'door' || this.tool === 'window') {
      const host = (kind === 'wall' || kind === 'partition') ? id : kind === 'opening' ? this.openings().find(item => item?.id === id)?.host : null;
      if (!host) { this.feedback.textContent = 'Click a shell or partition wall band to place the opening.'; return; }
      this.addOpening(this.tool, host, point); return;
    }
    if (kind === 'dimension') {
      this.selected = { kind: 'wall', id: id === 'length_mm' ? 'W2' : 'W3' }; this.renderInspector(); this.renderCanvas();
      const input = this.inspector.querySelector('[name="' + id + '"]'); input?.focus(); input?.select(); return;
    }
    if (kind === 'partition-grip') this.selected = { kind: 'partition', id };
    else if (kind === 'partition' || kind === 'room') this.selected = { kind, id };
    else if (kind === 'opening') this.selected = { kind, id };
    else if (kind === 'wall') this.selected = { kind, id };
    else if (kind === 'resize') this.selected = { kind: 'wall', id: id === 'north' ? 'W3' : 'W2' };
    else { this.selected = null; this.render(); return; }
    this.renderInspector(); this.renderCanvas();
    if (['opening', 'resize', 'partition', 'partition-grip'].includes(kind)) {
      event.preventDefault();
      this.gesture = { type: kind, id, endpoint: element?.dataset.peEnd, pointer: event.pointerId, origin: point, base: copy(this.draft), moved: false };
      this.svg.setPointerCapture(event.pointerId);
    }
  }

  pointerMove(event) {
    const point = this.point(event);
    this.root.querySelector('.pe-pointer').textContent = 'X ' + fmt(snap(point.x, this.spacing)) + ' · Y ' + fmt(snap(point.y, this.spacing));
    if (this.tool === 'measure' && this.measurement?.start && !this.measurement.end) { this.measurement.preview = { x: snap(point.x, this.spacing), y: snap(point.y, this.spacing) }; this.renderCanvas(); }
    if (this.tool === 'partition' && this.partitionStart) {
      this.partitionPreview = snapPoint(this.draft, point, this.spacing);
      if (Math.abs(this.partitionPreview.x - this.partitionStart.x) >= Math.abs(this.partitionPreview.y - this.partitionStart.y)) this.partitionPreview.y = this.partitionStart.y; else this.partitionPreview.x = this.partitionStart.x;
      this.renderCanvas();
    }
    const gesture = this.gesture; if (!gesture || gesture.pointer !== event.pointerId) return;
    if (gesture.type === 'pan') {
      const rect = this.svg.getBoundingClientRect();
      this.view.x = gesture.view.x - (event.clientX - gesture.start.x) * gesture.view.width / rect.width;
      this.view.y = gesture.view.y - (event.clientY - gesture.start.y) * gesture.view.height / rect.height;
      this.applyView(); this.renderCanvas(); return;
    }
    if (Math.hypot(point.x - gesture.origin.x, point.y - gesture.origin.y) < 1) return;
    gesture.moved = true; const candidate = copy(gesture.base); const spacing = event.altKey ? 0 : this.spacing;
    if (gesture.type === 'resize') {
      if (gesture.id === 'east' || gesture.id === 'corner') candidate.length_mm = Math.round(snap(gesture.base.length_mm + point.x - gesture.origin.x, spacing));
      if (gesture.id === 'north' || gesture.id === 'corner') candidate.width_mm = Math.round(snap(gesture.base.width_mm + point.y - gesture.origin.y, spacing));
    } else if (gesture.type === 'partition' || gesture.type === 'partition-grip') {
      this.draft = gesture.type === 'partition' ? movePartition(gesture.base, gesture.id, point.x - gesture.origin.x, point.y - gesture.origin.y, spacing) : resizePartition(gesture.base, gesture.id, gesture.endpoint, point, spacing);
      this.renderCanvas(); this.renderAudit(); this.renderInspector(); return;
    } else {
      const opening = candidate.openings.find(item => item?.id === gesture.id);
      const initial = gesture.base.openings.find(item => item?.id === gesture.id);
      const direction = hostDirection(candidate, opening.host);
      opening.offset_mm = Math.round(snap(openingOffset(gesture.base, initial) + (point.x - gesture.origin.x) * direction.x + (point.y - gesture.origin.y) * direction.y, spacing));
    }
    this.draft = candidate; this.renderCanvas(); this.renderAudit(); this.renderInspector();
  }

  pointerUp(event) {
    const gesture = this.gesture; if (!gesture || gesture.pointer !== event.pointerId) return;
    this.gesture = null;
    if (this.svg.hasPointerCapture(event.pointerId)) this.svg.releasePointerCapture(event.pointerId);
    if (gesture.type === 'pan') return;
    if (!gesture.moved) { this.draft = gesture.base; this.render(); return; }
    const candidate = copy(this.draft); const issues = auditPlan(candidate);
    this.draft = gesture.base;
    if (issues.length) { this.render(); this.feedback.textContent = 'Edit cancelled: ' + issues[0]; return; }
    this.commit(candidate, gesture.type === 'resize' ? 'Wall dimensions changed in the draft. Review the changes below.' : 'Opening moved in the draft. Review the changes below.');
  }

  cancelGesture(render = true) {
    const gesture = this.gesture; this.gesture = null;
    if (!gesture) return;
    if (gesture.base) this.draft = gesture.base;
    if (this.svg.hasPointerCapture(gesture.pointer)) this.svg.releasePointerCapture(gesture.pointer);
    if (render) { this.render(); this.feedback.textContent = 'Gesture cancelled. The draft is unchanged.'; }
    if (gesture.base) void this.refreshLayout();
  }

  addOpening(kind, host, point = null) {
    if (!Array.isArray(this.draft.openings) || auditPlan(this.draft).length) { this.feedback.textContent = 'Resolve the geometry issues before adding an opening.'; return; }
    const settings = copy(this.draft); const width = kind === 'door' ? 1000 : 1500;
    const start = point ? Math.round(snap(pointToOffset(settings, host, point, width), this.spacing)) : null;
    const opening = { id: nextOpeningId(settings, kind, [...this.reservedIds]), kind, host, offset_mm: start, width_mm: width, height_mm: kind === 'door' ? 2100 : 1200, sill_mm: kind === 'door' ? 0 : 900 };
    settings.openings.push(opening); const oldSelection = this.selected; this.selected = { kind: 'opening', id: opening.id };
    if (this.commit(settings, 'Added ' + opening.id + ' to the draft. Use the inspector to set its exact size and position.')) this.setTool('select');
    else { this.selected = oldSelection; this.feedback.textContent += ' Choose a clear wall position or move an existing opening first.'; }
  }

  deleteOpening() {
    if (this.busy || this.selected?.kind !== 'opening') return;
    const id = this.selected.id; const settings = copy(this.draft); settings.openings = settings.openings.filter(item => item?.id !== id);
    const host = this.openings().find(item => item?.id === id)?.host || 'W2'; this.selected = { kind: host.startsWith('P') ? 'partition' : 'wall', id: host };
    this.commit(settings, 'Removed ' + id + ' from the draft.');
  }

  applyProperties(action) {
    if (this.busy || !this.draft || !this.inspector.reportValidity()) return;
    if (action === 'delete') { this.deleteOpening(); return; }
    if (action === 'add-door' || action === 'add-window') {
      const host = ['wall', 'partition'].includes(this.selected?.kind) ? this.selected.id : 'W1'; this.addOpening(action === 'add-door' ? 'door' : 'window', host); return;
    }
    const settings = copy(this.draft); const data = new FormData(this.inspector);
    for (const field of ['length_mm', 'width_mm', 'height_mm', 'wall_thickness_mm']) settings[field] = Number(data.get(field));
    if (this.selected?.kind === 'opening') {
      const opening = settings.openings.find(item => item?.id === this.selected.id);
      opening.host = String(data.get('host'));
      for (const field of ['width_mm', 'height_mm', 'sill_mm']) opening[field] = Number(data.get('opening_' + field));
      opening.offset_mm = data.get('centred') ? null : Number(data.get('offset_mm'));
    }
    if (data.has('min_room_width_mm')) settings.min_room_width_mm = Number(data.get('min_room_width_mm'));
    if (this.selected?.kind === 'partition') {
      const partition = settings.partitions.find(item => item.id === this.selected.id);
      partition.start_mm = [Number(data.get('partition_start_x')), Number(data.get('partition_start_y'))];
      partition.end_mm = [Number(data.get('partition_end_x')), Number(data.get('partition_end_y'))];
      partition.thickness_mm = Number(data.get('partition_thickness_mm')); partition.height_mm = Number(data.get('partition_height_mm')); partition.wall_type = String(data.get('wall_type')).trim();
    }
    if (this.selected?.kind === 'room') {
      return this.setRoomProperties(this.selected.id, { name: String(data.get('room_name')).trim(), finishes: Object.fromEntries(['floor', 'ceiling', 'skirting', 'paint'].map(key => [key, String(data.get('finish_' + key) || '').trim() || null])) }, settings);
    }
    this.commit(settings, 'Exact properties applied to the draft.');
  }

  keyDown(event) {
    if (event.target.closest('input,select,textarea,[contenteditable="true"]')) return;
    if (event.code === 'Space') { if (event.target.closest('button,a')) return; event.preventDefault(); this.spaceDown = true; this.renderControls(); return; }
    if (event.key === 'Escape') { event.preventDefault(); this.cancelGesture(); this.measurement = null; this.partitionStart = null; this.partitionPreview = null; this.tool = 'select'; this.renderControls(); this.renderCanvas(); return; }
    if (this.busy || !this.draft) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); this.action(event.shiftKey ? 'redo' : 'undo'); return; }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') { event.preventDefault(); this.action('redo'); return; }
    if (event.key === 'Delete' || event.key === 'Backspace') { if (this.selected?.kind === 'opening') { event.preventDefault(); this.deleteOpening(); } else if (this.selected?.kind === 'partition') { event.preventDefault(); this.deletePartition(); } return; }
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) || !this.selected) return;
    event.preventDefault(); const settings = copy(this.draft); const step = this.spacing || 10;
    if (this.selected.kind === 'opening') {
      const opening = settings.openings.find(item => item?.id === this.selected.id);
      const direction = hostDirection(settings, opening.host); const delta = ({ ArrowRight: step * direction.x, ArrowLeft: -step * direction.x, ArrowUp: step * direction.y, ArrowDown: -step * direction.y })[event.key];
      if (!delta) return; opening.offset_mm = openingOffset(settings, opening) + delta;
    } else if (this.selected.kind === 'partition') {
      const delta = { ArrowRight: [step, 0], ArrowLeft: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[event.key];
      this.commit(movePartition(settings, this.selected.id, ...delta, this.spacing), 'Nudged partition. Review the room checks.'); return;
    } else {
      if (!['W2', 'W3'].includes(this.selected.id)) { this.feedback.textContent = 'The southwest datum is fixed. Select W2 east or W3 north to resize the shell with arrow keys.'; return; }
      const horizontal = this.selected.id === 'W2';
      const delta = horizontal ? ({ ArrowRight: step, ArrowLeft: -step })[event.key] : ({ ArrowUp: step, ArrowDown: -step })[event.key];
      if (!delta) return; const field = horizontal ? 'length_mm' : 'width_mm'; settings[field] += delta;
    }
    this.commit(settings, this.selected.kind === 'opening' ? 'Nudged the selected opening by ' + step + ' mm.' : 'Changed the shell span by ' + step + ' mm; southwest datum remains fixed.');
  }

  render() { if (!this.draft) return; this.renderCanvas(); this.renderInspector(); this.renderAudit(); this.renderControls(); }

  renderControls() {
    this.root.querySelectorAll('[data-tool]').forEach(button => { button.disabled = this.busy; button.setAttribute('aria-pressed', String(button.dataset.tool === this.tool)); });
    this.root.querySelectorAll('[data-action]').forEach(button => {
      const action = button.dataset.action;
      button.disabled = action === 'undo' ? this.busy || !this.history?.canUndo : action === 'redo' ? this.busy || !this.history?.canRedo : action === 'apply' ? this.busy || !this.draft || this.audit().length > 0 : ['fit', 'zoom-in', 'zoom-out'].includes(action) ? !this.draft : this.busy || !this.baseline;
    });
    this.root.querySelectorAll('.pe-property-form input,.pe-property-form select,.pe-property-form button').forEach(element => { element.disabled = this.busy || element.dataset.centredDisabled === 'true'; });
    this.svg.classList.toggle('pe-pan-tool', this.tool === 'pan' || this.spaceDown);
    this.svg.classList.toggle('pe-add-tool', this.tool === 'door' || this.tool === 'window' || this.tool === 'measure' || this.tool === 'partition');
  }

  renderInspector() {
    if (!this.draft) return;
    const form = this.inspector; form.replaceChildren();
    const header = htmlNode('h4', '', 'Plan properties'); form.append(header);
    const selection = htmlNode('label', 'pe-field', 'Selected element'); const select = document.createElement('select'); select.setAttribute('aria-label', 'Selected plan element');
    const empty = new Option('House geometry', ''); select.append(empty);
    for (const host of HOSTS) select.append(new Option(host + ' · ' + HOST_LABELS[host], 'wall:' + host));
    for (const partition of this.partitions()) select.append(new Option(partition.id + ' - partition', 'partition:' + partition.id));
    for (const room of this.rooms()) select.append(new Option(room.name + ' - ' + room.id, 'room:' + room.id));
    for (const opening of this.openings()) if (opening && typeof opening.id === 'string') select.append(new Option(opening.id + ' · ' + opening.kind + ' on ' + opening.host, 'opening:' + opening.id));
    select.value = this.selected ? this.selected.kind + ':' + this.selected.id : '';
    select.addEventListener('change', () => { const [kind, id] = select.value.split(':'); this.selected = id ? { kind, id } : null; this.renderInspector(); this.renderCanvas(); });
    selection.append(select); form.append(selection);
    const caption = htmlNode('h5', '', 'House geometry · mm'); form.append(caption);
    const grid = htmlNode('div', 'pe-field-grid');
    for (const [field, label, min, max] of [['length_mm', 'Internal length', 3000, 30000], ['width_mm', 'Internal width', 3000, 30000], ['height_mm', 'Wall height', 2400, 5000], ['wall_thickness_mm', 'Wall thickness', 100, 500]]) grid.append(this.numberField(field, label, this.draft[field], min, max));
    grid.append(this.numberField('min_room_width_mm', 'Minimum clear room width', this.draft.min_room_width_mm ?? 900, 300, 10000));
    form.append(grid);
    if (this.selected?.kind === 'partition') {
      const partition = this.partitions().find(item => item.id === this.selected.id);
      if (partition) {
        form.append(htmlNode('h5', '', partition.id + ' - partition centreline'));
        const fields = htmlNode('div', 'pe-field-grid');
        for (const [name, label, value, min, max] of [
          ['partition_start_x', 'Start X', partition.start_mm[0], 0, this.draft.length_mm], ['partition_start_y', 'Start Y', partition.start_mm[1], 0, this.draft.width_mm],
          ['partition_end_x', 'End X', partition.end_mm[0], 0, this.draft.length_mm], ['partition_end_y', 'End Y', partition.end_mm[1], 0, this.draft.width_mm],
          ['partition_thickness_mm', 'Partition thickness', partition.thickness_mm, 50, 500], ['partition_height_mm', 'Partition height', partition.height_mm, 300, this.draft.height_mm],
        ]) fields.append(this.numberField(name, label, value, min, max));
        form.append(fields); const type = htmlNode('label', 'pe-field', 'Wall type'); const input = document.createElement('input'); input.name = 'wall_type'; input.value = partition.wall_type; input.maxLength = 80; input.required = true; type.append(input); form.append(type);
        form.append(htmlNode('p', 'pe-small-note', 'Endpoints define opening direction. Drag the wall to move it or drag either endpoint grip to resize. Orthogonal lines only.'));
      }
    } else if (this.selected?.kind === 'room') {
      const room = this.rooms().find(item => item.id === this.selected.id);
      if (room) {
        form.append(htmlNode('h5', '', 'Derived room ' + room.id));
        const metadata = (this.draft.rooms || []).find(item => item.id === room.id);
        for (const [name, label, value, required] of [
          ['room_name', 'Room name', metadata?.name ?? room.name, true],
          ...['floor', 'ceiling', 'skirting', 'paint'].map(key => ['finish_' + key, key[0].toUpperCase() + key.slice(1) + ' finish override', metadata?.finishes?.[key] ?? '', false]),
        ]) { const field = htmlNode('label', 'pe-field', label); const input = document.createElement('input'); input.name = name; input.value = value; input.required = required; input.maxLength = 120; input.placeholder = required ? '' : 'Use project default'; field.append(input); form.append(field); }
        form.append(htmlNode('p', 'pe-small-note', fmt(room.area_m2, 3) + ' m2 clear area; ' + fmt(room.perimeter_m, 3) + ' m perimeter. Blank finish overrides use project defaults. Finish text does not infer product prices.'));
      } else form.append(htmlNode('p', 'pe-small-note', 'Waiting for the current room layout preview.'));
    }
    if (this.selected?.kind === 'opening') {
      const opening = this.openings().find(item => item?.id === this.selected.id);
      if (opening) {
        form.append(htmlNode('h5', '', opening.id + ' · ' + opening.kind));
        const hostLabel = htmlNode('label', 'pe-field', 'Host wall'); const hostSelect = document.createElement('select'); hostSelect.name = 'host';
        for (const host of planHosts(this.draft)) hostSelect.append(new Option(host + ' - ' + (HOST_LABELS[host] || 'partition'), host)); hostSelect.value = opening.host; hostLabel.append(hostSelect); form.append(hostLabel);
        const openingGrid = htmlNode('div', 'pe-field-grid');
        for (const [field, label, min, max] of [['width_mm', 'Opening width', 300, 30000], ['height_mm', 'Opening height', 300, 4900], ['sill_mm', 'Sill / threshold', 0, 4500]]) openingGrid.append(this.numberField('opening_' + field, label, opening[field], min, max));
        const offsetField = this.numberField('offset_mm', 'Start along host', openingOffset(this.draft, opening), 100, 30000);
        openingGrid.append(offsetField); form.append(openingGrid);
        const centre = htmlNode('label', 'pe-centre-check'); const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.name = 'centred'; checkbox.checked = opening.offset_mm === null || opening.offset_mm === undefined;
        centre.append(checkbox, document.createTextNode('Keep centred on wall')); form.append(centre);
        const offsetInput = offsetField.querySelector('input'); const disableOffset = () => { offsetInput.disabled = checkbox.checked || this.busy; offsetInput.dataset.centredDisabled = String(checkbox.checked); }; checkbox.addEventListener('change', disableOffset); disableOffset();
        form.append(htmlNode('p', 'pe-small-note', 'Start follows the wall direction shown in plan. A fixed start stays fixed when the shell is resized.'));
      }
    } else if (this.selected?.kind === 'wall') {
      form.append(htmlNode('p', 'pe-small-note', this.selected.id + ': ' + HOST_LABELS[this.selected.id] + '. Change the house span above. Use the east and north grips to resize the rectangular shell.'));
    }
    const apply = htmlNode('button', 'pe-property-apply', 'Apply to draft'); apply.type = 'submit'; apply.dataset.action = 'properties'; form.append(apply);
    if (this.selected?.kind === 'partition') { const remove = htmlNode('button', 'pe-remove', 'Remove partition and hosted openings'); remove.type = 'button'; remove.addEventListener('click', () => this.deletePartition()); form.append(remove); }
    if (this.selected?.kind === 'opening') { const remove = htmlNode('button', 'pe-remove', 'Remove opening'); remove.type = 'button'; remove.addEventListener('click', () => this.deleteOpening()); form.append(remove); }
    form.append(htmlNode('p', 'pe-small-note', 'Select elements above for keyboard editing. Arrow keys move openings or resize east/north spans. Ctrl+Z undoes; Escape cancels. Alt-drag uses 1 mm precision.'));
    this.renderControls();
  }

  numberField(name, label, value, min, max) {
    const field = htmlNode('label', 'pe-field', label); const input = document.createElement('input'); input.type = 'number'; input.name = name; input.min = min; input.max = max; input.step = '1'; input.required = true; input.value = Number.isFinite(value) ? value : ''; field.append(input); return field;
  }

  renderAudit() {
    if (!this.draft) return;
    const issues = this.audit(); const section = this.root.querySelector('.pe-audit'); section.classList.toggle('pe-audit-failed', issues.length > 0);
    this.root.querySelector('.pe-audit-count').textContent = issues.length ? issues.length + ' to resolve' : 'Passed';
    const list = this.root.querySelector('.pe-audit-list'); list.replaceChildren();
    for (const issue of issues.length ? issues : ['Current geometry and room access checks passed.']) list.append(htmlNode('li', '', issue));
    if (this.layoutKey === JSON.stringify(this.draft)) for (const warning of this.layout?.checks?.warnings || []) list.append(htmlNode('li', 'pe-audit-warning', 'Note: ' + warning));
    const changes = this.baseline ? describeChanges(this.baseline, this.draft) : ['Generate a saved revision to establish the comparison baseline.'];
    this.root.querySelector('.pe-review-revision').textContent = this.revision ? 'vs ' + this.revision : '';
    const review = this.root.querySelector('.pe-review-list'); review.replaceChildren();
    for (const description of changes.length ? changes : ['No changes from the saved plan.']) review.append(htmlNode('li', '', description));
    this.renderControls();
  }

  renderCanvas() {
    if (!this.draft) return;
    const settings = this.displaySettings(); const L = settings.length_mm, W = settings.width_mm, t = settings.wall_thickness_mm;
    const rect = this.svg.getBoundingClientRect(); const unit = this.view.width / (rect.width || 800); const font = 11 * unit;
    this.svg.replaceChildren(); this.applyView();
    const definitions = svgNode('defs'); const spacing = Math.max(this.spacing || 100, 50, Math.ceil(unit * 8 / 50) * 50);
    const pattern = svgNode('pattern', { id: this.uid + '-grid', width: spacing, height: spacing, patternUnits: 'userSpaceOnUse' });
    pattern.append(svgNode('path', { d: 'M ' + spacing + ' 0 L 0 0 0 ' + spacing, fill: 'none', stroke: '#e5ebe7', 'stroke-width': .5, 'vector-effect': 'non-scaling-stroke' })); definitions.append(pattern); this.svg.append(definitions);
    this.svg.append(svgNode('rect', { x: this.view.x, y: this.view.y, width: this.view.width, height: this.view.height, fill: 'url(#' + this.uid + '-grid)' }));
    this.svg.append(svgNode('rect', { x: 0, y: -W, width: L, height: W, fill: '#fbfdfb', stroke: '#b7c7be', 'stroke-width': 1, 'vector-effect': 'non-scaling-stroke' }));
    for (const room of this.rooms()) {
      const rings = [room.outline_mm || [], ...(room.holes_mm || [])];
      const d = rings.filter(ring => ring.length >= 3).map(ring => 'M ' + ring.map(point => point[0] + ' ' + -point[1]).join(' L ') + ' Z').join(' ');
      const selected = this.selected?.kind === 'room' && this.selected.id === room.id;
      const path = svgNode('path', { d, fill: selected ? '#d0e7f2' : '#edf4e8', stroke: '#b5ccaf', 'stroke-width': 1, 'fill-rule': 'evenodd', 'vector-effect': 'non-scaling-stroke', 'data-pe-kind': 'room', 'data-pe-id': room.id, class: 'pe-room' });
      path.append(svgNode('title', {}, room.name + ' - ' + fmt(room.area_m2, 3) + ' m2')); this.svg.append(path);
      if (room.label_point_mm) {
        const [x, y] = room.label_point_mm;
        const group = svgNode('g', { 'data-pe-kind': 'room', 'data-pe-id': room.id, class: 'pe-room-label' });
        group.append(svgNode('text', { x, y: -y, 'text-anchor': 'middle', 'font-size': font * 1.15, fill: '#315c41', 'font-weight': 600 }, room.name));
        group.append(svgNode('text', { x, y: -y + font * 1.7, 'text-anchor': 'middle', 'font-size': font * .9, fill: '#64806a' }, fmt(room.area_m2, 3) + ' m2'));
        this.svg.append(group);
      }
    }
    if (this.baseline && this.root.querySelector('.pe-show-baseline').checked && !same(this.baseline, this.draft)) {
      const base = this.displaySettings(this.baseline); const ghost = svgNode('g', { 'pointer-events': 'none', opacity: '.85' });
      ghost.append(svgNode('rect', { x: -base.wall_thickness_mm / 2, y: -base.width_mm - base.wall_thickness_mm / 2, width: base.length_mm + base.wall_thickness_mm, height: base.width_mm + base.wall_thickness_mm, fill: 'none', stroke: '#bd8b3e', 'stroke-width': 2, 'stroke-dasharray': '7 5', 'vector-effect': 'non-scaling-stroke' }));
      for (const partition of this.partitions(base)) if (Array.isArray(partition.start_mm) && Array.isArray(partition.end_mm)) {
        ghost.append(svgNode('line', { x1: partition.start_mm[0], y1: -partition.start_mm[1], x2: partition.end_mm[0], y2: -partition.end_mm[1], stroke: '#bd8b3e', 'stroke-width': 2, 'stroke-dasharray': '7 5', 'vector-effect': 'non-scaling-stroke' }));
      }
      for (const opening of base.openings) {
        if (!opening || !planHosts(base).includes(opening.host) || !Number.isFinite(opening.width_mm)) continue;
        const segment = openingSegment(base, opening);
        if (![segment.start.x, segment.start.y, segment.end.x, segment.end.y].every(Number.isFinite)) continue;
        ghost.append(svgNode('line', { x1: segment.start.x, y1: -segment.start.y, x2: segment.end.x, y2: -segment.end.y, stroke: '#bd8b3e', 'stroke-width': 6, 'stroke-dasharray': '3 3', 'vector-effect': 'non-scaling-stroke' }));
      }
      this.svg.append(ghost);
    }
    const walls = [ ['W1', -t, 0, L + 2 * t, t], ['W2', L, -W, t, W], ['W3', -t, -W - t, L + 2 * t, t], ['W4', -t, -W, t, W] ];
    for (const [id, x, y, width, height] of walls) {
      const selected = this.selected?.kind === 'wall' && this.selected.id === id;
      const wall = svgNode('rect', { x, y, width, height, fill: selected ? '#317e72' : '#526f62', stroke: selected ? '#0a7066' : '#3e5b50', 'stroke-width': selected ? 2 : 1, 'vector-effect': 'non-scaling-stroke', 'data-pe-kind': 'wall', 'data-pe-id': id, class: 'pe-wall' });
      wall.append(svgNode('title', {}, id + ' · ' + HOST_LABELS[id] + ' wall. Select to edit the span.')); this.svg.append(wall);
    }
    for (const partition of this.partitions(settings)) {
      if (!Array.isArray(partition.start_mm) || !Array.isArray(partition.end_mm)) continue;
      const [x1, y1] = partition.start_mm, [x2, y2] = partition.end_mm;
      const selected = this.selected?.kind === 'partition' && this.selected.id === partition.id;
      const group = svgNode('g', { 'data-pe-kind': 'partition', 'data-pe-id': partition.id, class: 'pe-partition' });
      group.append(svgNode('line', { x1, y1: -y1, x2, y2: -y2, stroke: selected ? '#247f8f' : '#66788a', 'stroke-width': partition.thickness_mm, 'stroke-linecap': 'butt' }));
      group.append(svgNode('line', { x1, y1: -y1, x2, y2: -y2, stroke: 'transparent', 'stroke-width': Math.max(partition.thickness_mm, unit * 12) }));
      group.append(svgNode('text', { x: (x1 + x2) / 2 + font, y: -(y1 + y2) / 2 - font, 'font-size': font, fill: '#456b80' }, partition.id));
      this.svg.append(group);
      if (selected) for (const [endpoint, point] of [['start', partition.start_mm], ['end', partition.end_mm]]) {
        const grip = svgNode('circle', { cx: point[0], cy: -point[1], r: unit * 6, fill: '#fff', stroke: '#176f9a', 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke', 'data-pe-kind': 'partition-grip', 'data-pe-id': partition.id, 'data-pe-end': endpoint, class: 'pe-partition-grip' });
        grip.append(svgNode('title', {}, partition.id + ' ' + endpoint + ' endpoint')); this.svg.append(grip);
      }
    }
    if (this.partitionStart) {
      const end = this.partitionPreview || this.partitionStart;
      this.svg.append(svgNode('line', { x1: this.partitionStart.x, y1: -this.partitionStart.y, x2: end.x, y2: -end.y, stroke: '#247f8f', 'stroke-width': 3, 'stroke-dasharray': '5 4', 'vector-effect': 'non-scaling-stroke', 'pointer-events': 'none' }));
      this.svg.append(svgNode('circle', { cx: this.partitionStart.x, cy: -this.partitionStart.y, r: unit * 5, fill: '#247f8f', 'pointer-events': 'none' }));
    }
    for (const opening of settings.openings) {
      if (!opening || !planHosts(settings).includes(opening.host) || !Number.isFinite(opening.width_mm) || opening.width_mm <= 0 || opening.width_mm > 30000) continue;
      const segment = openingSegment(settings, opening); if (![segment.start.x, segment.start.y, segment.end.x, segment.end.y].every(Number.isFinite)) continue;
      const tangent = { x: (segment.end.x - segment.start.x) / opening.width_mm, y: (segment.end.y - segment.start.y) / opening.width_mm }; const normal = { x: -tangent.y, y: tangent.x };
      const partitionHost = this.partitions(settings).find(item => item.id === opening.host); const openingThickness = partitionHost?.thickness_mm ?? t;
      const outside = partitionHost ? { x: 0, y: 0 } : { x: -normal.x * t / 2, y: -normal.y * t / 2 };
      const selected = this.selected?.kind === 'opening' && this.selected.id === opening.id; const colour = selected ? '#0c6fbd' : opening.kind === 'door' ? '#966b35' : '#3a8a9c';
      const group = svgNode('g', { 'data-pe-kind': 'opening', 'data-pe-id': opening.id, class: 'pe-opening' });
      group.append(svgNode('title', {}, opening.id + ' · ' + opening.kind + ' · ' + fmt(opening.width_mm) + ' mm wide'));
      group.append(svgNode('line', { x1: segment.start.x + outside.x, y1: -segment.start.y - outside.y, x2: segment.end.x + outside.x, y2: -segment.end.y - outside.y, stroke: '#fbfdfb', 'stroke-width': openingThickness + 25 }));
      if (opening.kind === 'door') {
        const leaf = { x: segment.start.x + normal.x * opening.width_mm, y: segment.start.y + normal.y * opening.width_mm };
        group.append(svgNode('path', { d: 'M ' + segment.start.x + ' ' + -segment.start.y + ' L ' + leaf.x + ' ' + -leaf.y + ' M ' + segment.end.x + ' ' + -segment.end.y + ' A ' + opening.width_mm + ' ' + opening.width_mm + ' 0 0 0 ' + leaf.x + ' ' + -leaf.y, fill: 'none', stroke: colour, 'stroke-width': selected ? 2 : 1.3, 'vector-effect': 'non-scaling-stroke' }));
      } else {
        for (const offset of partitionHost ? [-openingThickness * .3, openingThickness * .3] : [t * .2, t * .8]) group.append(svgNode('line', { x1: segment.start.x - normal.x * offset, y1: -segment.start.y + normal.y * offset, x2: segment.end.x - normal.x * offset, y2: -segment.end.y + normal.y * offset, stroke: colour, 'stroke-width': selected ? 2 : 1.4, 'vector-effect': 'non-scaling-stroke' }));
      }
      group.append(svgNode('line', { x1: segment.start.x + outside.x, y1: -segment.start.y - outside.y, x2: segment.end.x + outside.x, y2: -segment.end.y - outside.y, stroke: 'transparent', 'stroke-width': Math.max(openingThickness, unit * 16), class: 'pe-opening-hit' }));
      const labelOffset = opening.kind === 'door' ? Math.max(350, opening.width_mm * .6) : 350;
      group.append(svgNode('text', { x: segment.center.x + normal.x * labelOffset, y: -segment.center.y - normal.y * labelOffset, 'text-anchor': 'middle', 'dominant-baseline': 'middle', fill: colour, 'font-size': font, 'font-weight': selected ? 700 : 500 }, opening.id));
      if (selected) group.append(svgNode('circle', { cx: segment.center.x + outside.x, cy: -segment.center.y - outside.y, r: unit * 5, fill: '#fff', stroke: colour, 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke' }));
      this.svg.append(group);
    }
    const label = (x, y, text, attributes = {}) => this.svg.append(svgNode('text', { x, y, 'font-size': font, fill: '#6a8175', 'text-anchor': 'middle', ...attributes }, text));
    if (!this.partitions(settings).length && !this.rooms().length) {
    label(L / 2, -W / 2 - font * .4, 'CLEAR INTERNAL PLAN', { 'font-size': font * .9, 'letter-spacing': font * .13 });
    label(L / 2, -W / 2 + font * 1.3, fmt(L / 1000, 2) + ' × ' + fmt(W / 1000, 2) + ' m', { 'font-size': font * 1.5, fill: '#254d3d' });
    label(L / 2, -W / 2 + font * 3.1, fmt(L * W / 1e6, 2) + ' m² clear floor area');
    }
    const line = attributes => this.svg.append(svgNode('line', { stroke: '#6d9d96', 'stroke-width': 1, 'vector-effect': 'non-scaling-stroke', ...attributes }));
    const dimY = t + unit * 42;
    line({ x1: 0, y1: dimY - unit * 10, x2: 0, y2: dimY + unit * 10 }); line({ x1: L, y1: dimY - unit * 10, x2: L, y2: dimY + unit * 10 }); line({ x1: 0, y1: dimY, x2: L, y2: dimY });
    const dimLength = svgNode('text', { x: L / 2, y: dimY - unit * 8, 'text-anchor': 'middle', 'font-size': font, fill: '#1b6d65', 'data-pe-kind': 'dimension', 'data-pe-id': 'length_mm', class: 'pe-dimension' }, fmt(L) + ' mm'); dimLength.append(svgNode('title', {}, 'Click to enter internal length')); this.svg.append(dimLength);
    const dimX = -t - unit * 42;
    line({ x1: dimX - unit * 10, y1: 0, x2: dimX + unit * 10, y2: 0 }); line({ x1: dimX - unit * 10, y1: -W, x2: dimX + unit * 10, y2: -W }); line({ x1: dimX, y1: 0, x2: dimX, y2: -W });
    const dimWidth = svgNode('text', { x: dimX - unit * 8, y: -W / 2, transform: 'rotate(-90 ' + (dimX - unit * 8) + ' ' + -W / 2 + ')', 'text-anchor': 'middle', 'font-size': font, fill: '#1b6d65', 'data-pe-kind': 'dimension', 'data-pe-id': 'width_mm', class: 'pe-dimension' }, fmt(W) + ' mm'); dimWidth.append(svgNode('title', {}, 'Click to enter internal width')); this.svg.append(dimWidth);
    for (const [id, x, y, title] of [['east', L + t + unit * 18, -W / 2, 'Resize east wall / internal length'], ['north', L / 2, -W - t - unit * 18, 'Resize north wall / internal width'], ['corner', L + t + unit * 18, -W - t - unit * 18, 'Resize length and width']]) {
      const handle = svgNode('rect', { x: x - unit * 6, y: y - unit * 6, width: unit * 12, height: unit * 12, rx: unit * 2, fill: '#fff', stroke: '#0c776b', 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke', 'data-pe-kind': 'resize', 'data-pe-id': id, class: 'pe-resize pe-resize-' + id }); handle.append(svgNode('title', {}, title)); this.svg.append(handle);
    }
    label(-t - unit * 8, t + unit * 17, '0,0', { 'text-anchor': 'end', 'font-size': font * .85 });
    this.svg.append(svgNode('circle', { cx: 0, cy: 0, r: unit * 3, fill: '#194c43', 'pointer-events': 'none' }));
    const northX = this.view.x + this.view.width - unit * 26, northY = this.view.y + unit * 34;
    this.svg.append(svgNode('path', { d: 'M ' + northX + ' ' + (northY + unit * 15) + ' L ' + northX + ' ' + (northY - unit * 5) + ' M ' + (northX - unit * 4) + ' ' + northY + ' L ' + northX + ' ' + (northY - unit * 5) + ' L ' + (northX + unit * 4) + ' ' + northY, fill: 'none', stroke: '#547265', 'stroke-width': 1.5, 'vector-effect': 'non-scaling-stroke', 'pointer-events': 'none' })); label(northX, northY - unit * 12, 'N', { 'font-size': font * .85 });
    if (this.measurement?.start) {
      const start = this.measurement.start, end = this.measurement.end || this.measurement.preview;
      if (end) {
        this.svg.append(svgNode('line', { x1: start.x, y1: -start.y, x2: end.x, y2: -end.y, stroke: '#aa6c2b', 'stroke-width': 2, 'stroke-dasharray': '5 4', 'vector-effect': 'non-scaling-stroke', 'pointer-events': 'none' }));
        label((start.x + end.x) / 2, -(start.y + end.y) / 2 - unit * 10, fmt(Math.hypot(end.x - start.x, end.y - start.y), 1) + ' mm', { fill: '#975e21', 'font-weight': 700, 'pointer-events': 'none' });
      }
    }
    this.svg.classList.toggle('pe-invalid', this.audit().length > 0);
  }
}
