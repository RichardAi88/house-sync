import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../housesync/static/plan-core.js', import.meta.url), 'utf8');
const core = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const { defaultOpenings, cloneSettings, DraftHistory } = core;
const fixture = () => ({
  project_name: 'Plan geometry test', length_mm: 10000, width_mm: 8000, height_mm: 3000,
  wall_thickness_mm: 200, paint_colour: 'Light blue', paint_hex: '#ADD8E6', currency: 'SGD',
  rates: { floor_m2: 80, wall_m3: 300, roof_m2: 120, paint_m2: 12, opening_each: 500 },
  openings: defaultOpenings(),
});

// Exercise the actual controller state machine without substituting browser automation.
const coreURL = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const controllerSource = (await readFile(new URL('../housesync/static/plan-editor.js', import.meta.url), 'utf8')).replace("from './plan-core.js'", "from '" + coreURL + "'");
const { PlanEditor } = await import('data:text/javascript;base64,' + Buffer.from(controllerSource).toString('base64'));
function controllerFixture() {
  const settings = fixture(); const changes = []; const editor = Object.create(PlanEditor.prototype);
  Object.assign(editor, { draft: cloneSettings(settings), baseline: cloneSettings(settings), history: new DraftHistory(settings), selected: { kind: 'wall', id: 'W2' }, busy: false, tool: 'select', spacing: 100, gesture: null, feedback: { textContent: '' }, reservedIds: new Set(settings.openings.map(item => item.id)), callbacks: { onChange: value => changes.push(value), onApply: () => {} }, svg: { hasPointerCapture: () => false, releasePointerCapture: () => {} }, root: { querySelector: () => ({ textContent: '' }), setAttribute: () => {}, classList: { toggle: () => {} } }, render: () => {}, renderCanvas: () => {}, renderAudit: () => {}, renderInspector: () => {}, renderControls: () => {} });
  return { editor, changes, settings };
}

test('controller commits a completed drag once and undo restores the whole draft', () => {
  const { editor, changes, settings } = controllerFixture();
  editor.gesture = { type: 'resize', pointer: 1, base: cloneSettings(settings), moved: true };
  editor.draft.length_mm = 12000;
  editor.pointerUp({ pointerId: 1 });
  assert.equal(changes.length, 1); assert.equal(editor.history.current.length_mm, 12000);
  editor.action('undo'); assert.equal(changes.length, 2); assert.deepEqual(editor.draft, settings);
});

test('controller rejects an invalid release without emitting or creating history', () => {
  const { editor, changes, settings } = controllerFixture();
  editor.gesture = { type: 'resize', pointer: 1, base: cloneSettings(settings), moved: true };
  editor.draft.length_mm = 1000;
  editor.pointerUp({ pointerId: 1 });
  assert.deepEqual(editor.draft, settings); assert.equal(changes.length, 0); assert.equal(editor.history.canUndo, false);
  assert.match(editor.feedback.textContent, /cancelled/);
});

test('controller cancellation restores the pre-gesture state and emits no change', () => {
  const { editor, changes, settings } = controllerFixture();
  editor.gesture = { type: 'opening', pointer: 1, base: cloneSettings(settings), moved: true };
  editor.draft.openings[0].offset_mm = 2000; editor.cancelGesture();
  assert.deepEqual(editor.draft, settings); assert.equal(changes.length, 0); assert.equal(editor.history.canUndo, false);
});

test('controller busy mode prevents arrow edits and cancels unfinished gestures', () => {
  const { editor, changes, settings } = controllerFixture();
  editor.gesture = { type: 'resize', pointer: 1, base: cloneSettings(settings), moved: true }; editor.draft.length_mm = 12000;
  editor.setBusy(true);
  editor.keyDown({ target: { closest: () => null }, key: 'ArrowRight', code: 'ArrowRight', preventDefault: () => assert.fail('Busy edit must be ignored') });
  assert.deepEqual(editor.draft, settings); assert.equal(changes.length, 0); assert.equal(editor.gesture, null);
});

test('controller preserves native Space activation on toolbar buttons', () => {
  const { editor } = controllerFixture();
  editor.keyDown({ target: { closest: selector => selector === 'button,a' ? {} : null }, key: ' ', code: 'Space', preventDefault: () => assert.fail('Native button activation must not be suppressed') });
  assert.notEqual(editor.spaceDown, true);
});

test('controller keeps southwest datum fixed and only nudges movable shell spans', () => {
  const { editor, changes, settings } = controllerFixture();
  editor.selected = { kind: 'wall', id: 'W4' };
  editor.keyDown({ target: { closest: () => null }, key: 'ArrowRight', code: 'ArrowRight', preventDefault: () => {} });
  assert.deepEqual(editor.draft, settings); assert.equal(changes.length, 0); assert.match(editor.feedback.textContent, /datum is fixed/);
  editor.selected = { kind: 'wall', id: 'W2' };
  editor.keyDown({ target: { closest: () => null }, key: 'ArrowRight', code: 'ArrowRight', preventDefault: () => {} });
  assert.equal(editor.draft.length_mm, 10100); assert.equal(changes.length, 1);
});

test('controller drag with snap off uses integer millimetres consistently with the form', () => {
  const { editor, settings } = controllerFixture();
  editor.spacing = 0; editor.point = () => ({ x: 123.7, y: 0 });
  editor.gesture = { type: 'resize', id: 'east', pointer: 1, origin: { x: 0, y: 0 }, base: cloneSettings(settings), moved: false };
  editor.pointerMove({ pointerId: 1, altKey: false });
  assert.equal(editor.draft.length_mm, 10124);
});

test('controller tolerates malformed opening lists while exposing validation issues', () => {
  const { editor, changes, settings } = controllerFixture();
  for (const openings of [null, { unexpected: true }, [null, {}]]) {
    assert.doesNotThrow(() => editor.setDraft({ ...settings, openings }));
    assert.ok(editor.audit().length > 0); assert.ok(Array.isArray(editor.displaySettings().openings));
  }
  assert.equal(changes.length, 0);
});


test('controller add and delete commands preserve stable identity and are undoable', () => {
  const { editor, changes } = controllerFixture(); editor.setTool = () => {};
  editor.addOpening('door', 'W4', { x: 0, y: 4000 });
  assert.equal(changes.length, 1); assert.equal(editor.draft.openings.length, 4);
  const opening = editor.draft.openings.find(item => item.id === 'D2'); assert.equal(opening.host, 'W4'); assert.equal(opening.offset_mm, 3500);
  editor.deleteOpening(); assert.equal(editor.draft.openings.length, 3); assert.equal(changes.length, 2);
  editor.action('undo'); assert.ok(editor.draft.openings.some(item => item.id === 'D2'));
  editor.action('redo'); assert.ok(!editor.draft.openings.some(item => item.id === 'D2'));
  editor.addOpening('door', 'W4', { x: 0, y: 4000 }); assert.ok(editor.draft.openings.some(item => item.id === 'D3'));
});

test('controller moves north-wall openings in the reversed host direction', () => {
  const { editor, settings, changes } = controllerFixture();
  editor.selected = { kind: 'opening', id: 'N1' }; editor.spacing = 0; editor.point = () => ({ x: 200, y: 0 });
  editor.gesture = { type: 'opening', id: 'N1', pointer: 1, origin: { x: 0, y: 0 }, base: cloneSettings(settings), moved: false };
  editor.pointerMove({ pointerId: 1, altKey: false }); editor.pointerUp({ pointerId: 1 });
  assert.equal(editor.draft.openings.find(item => item.id === 'N1').offset_mm, 4050); assert.equal(changes.length, 1);
});

test('controller external form sync records history without emitting another form callback', () => {
  const { editor, settings, changes } = controllerFixture();
  const external = cloneSettings(settings); external.openings[0].width_mm = 1200; external.rates.floor_m2 = 95;
  editor.setDraft(external); external.openings[0].width_mm = 2000;
  assert.equal(editor.draft.openings[0].width_mm, 1200); assert.equal(editor.draft.rates.floor_m2, 95); assert.equal(changes.length, 0);
  editor.action('undo'); assert.deepEqual(editor.draft, settings);
});

test('controller apply invokes regeneration only for a valid non-busy draft', () => {
  const { editor, settings } = controllerFixture(); let builds = 0; editor.callbacks.onApply = () => { builds++; };
  editor.action('apply'); assert.equal(builds, 1);
  editor.setDraft({ ...settings, length_mm: 1000 }); editor.action('apply'); assert.equal(builds, 1);
  editor.setDraft(settings); editor.setBusy(true); editor.action('apply'); assert.equal(builds, 1);
});
