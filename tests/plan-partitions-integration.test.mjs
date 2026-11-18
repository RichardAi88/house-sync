import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const repository = fileURLToPath(new URL('../', import.meta.url));
const python = process.env.HOUSESYNC_TEST_PYTHON || [
  join(repository, '.venv', 'Scripts', 'python.exe'),
  join(repository, '.venv', 'bin', 'python'),
].find(existsSync) || 'python';

// Import the real browser controller and its actual helpers, keeping Node's
// module rules independent of the browser-only package.json configuration.
const moduleURLs = new Map();
async function browserModule(name) {
  if (!moduleURLs.has(name)) moduleURLs.set(name, (async () => {
    let source = await readFile(new URL('../housesync/static/' + name, import.meta.url), 'utf8');
    const dependencies = [...source.matchAll(/from\s+(['"])(\.\/[^'"]+)\1/g)];
    for (const match of dependencies) {
      const url = await browserModule(match[2].slice(2));
      source = source.replace(match[0], 'from ' + JSON.stringify(url));
    }
    return 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
  })());
  return moduleURLs.get(name);
}
const { PlanEditor } = await import(await browserModule('plan-editor.js'));
const { cloneSettings, defaultOpenings, DraftHistory } = await import(await browserModule('plan-core.js'));

let service;
let baseURL;
let serviceErrors = '';

before(async () => {
  // This is the production HTTP handler and layout solver. No successful
  // geometry/access response is fabricated by the controller test harness.
  service = spawn(python, ['-u', '-c', `
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from housesync.server import Application, LocalHTTPServer, handler_for
with TemporaryDirectory(prefix="house-sync-plan-ui-") as directory:
    app = Application(Path(directory))
    http = LocalHTTPServer(("127.0.0.1", 0), handler_for(app))
    print(json.dumps({"port": http.server_port}), flush=True)
    try:
        http.serve_forever()
    finally:
        http.server_close()
`], { cwd: repository, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  service.stderr.setEncoding('utf8');
  service.stderr.on('data', value => { serviceErrors += value; });
  const ready = await new Promise((resolve, reject) => {
    let output = '';
    const timeout = setTimeout(() => reject(new Error('Python layout service did not start: ' + serviceErrors)), 30000);
    service.on('error', error => { clearTimeout(timeout); reject(error); });
    service.on('exit', code => {
      clearTimeout(timeout);
      if (!baseURL) reject(new Error('Python layout service exited (' + code + '). Run uv sync or set HOUSESYNC_TEST_PYTHON. ' + serviceErrors));
    });
    service.stdout.setEncoding('utf8');
    service.stdout.on('data', value => {
      output += value;
      const line = output.split('\n').find(line => line.trim().startsWith('{'));
      if (line) {
        clearTimeout(timeout);
        try { resolve(JSON.parse(line)); } catch (error) { reject(error); }
      }
    });
  });
  baseURL = 'http://127.0.0.1:' + ready.port;
});

after(async () => {
  if (!service?.pid || service.exitCode !== null) return;
  const exited = new Promise(resolve => service.once('exit', resolve));
  try {
    if (baseURL) await fetch(baseURL + '/api/shutdown', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ application: 'House Sync' }),
    });
  } finally {
    const timeout = setTimeout(() => service.kill(), 3000);
    await exited;
    clearTimeout(timeout);
  }
});

function controllerFixture() {
  const settings = {
    project_name: 'Partition UI integration', length_mm: 10000, width_mm: 8000,
    height_mm: 3000, wall_thickness_mm: 200, paint_colour: 'Light blue', paint_hex: '#ADD8E6',
    currency: 'AUD', rate_overrides: {}, partitions: [], rooms: [], min_room_width_mm: 900,
    openings: defaultOpenings(),
  };
  const changes = [];
  const requested = [];
  let builds = 0;
  const editor = Object.create(PlanEditor.prototype);
  const pointerLabel = { textContent: '' };
  const captures = new Set();
  Object.assign(editor, {
    draft: cloneSettings(settings), baseline: cloneSettings(settings), history: new DraftHistory(settings),
    selected: null, busy: false, tool: 'select', spacing: 100, gesture: null,
    measurement: null, partitionStart: null, layout: null, layoutRequest: 0,
    feedback: { textContent: '' }, reservedIds: new Set(settings.openings.map(item => item.id)),
    reservedPartitionIds: new Set(),
    callbacks: {
      onChange: value => changes.push(structuredClone(value)), onApply: () => { builds++; }, onReviewSaved: () => {},
      previewLayout: async draft => {
        requested.push(structuredClone(draft));
        const response = await fetch(baseURL + '/api/layout', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft),
        });
        assert.equal(response.status, 200, await response.clone().text());
        return response.json();
      },
    },
    svg: {
      hasPointerCapture: pointer => captures.has(pointer), releasePointerCapture: pointer => captures.delete(pointer),
      setPointerCapture: pointer => captures.add(pointer), focus: () => {},
      classList: { toggle: () => {} }, getBoundingClientRect: () => ({ width: 800, height: 600 }),
    },
    root: { querySelector: () => pointerLabel, setAttribute: () => {}, classList: { toggle: () => {} } },
    // The existing controller harness replaces rendering only. State edits,
    // commands, pointer gestures, history, validation and backend calls remain real.
    render: () => {}, renderCanvas: () => {}, renderInspector: () => {}, renderAudit: () => {}, renderControls: () => {},
  });
  editor.point = event => event.planPoint;
  return { editor, changes, requested, settings, builds: () => builds };
}

function pointer(kind, id, point, pointerId = 1, endpoint = undefined) {
  return { button: 0, pointerId, clientX: 0, clientY: 0, planPoint: point, altKey: false,
    target: { closest: () => kind ? { dataset: { peKind: kind, peId: id, peEnd: endpoint } } : null }, preventDefault: () => {} };
}

test('plan draft draws a partition, resolves access, edits a room, moves and deletes with history', async () => {
  const { editor, changes, requested, builds } = controllerFixture();
  await editor.refreshLayout();
  assert.equal(editor.layout.rooms.length, 1);
  assert.deepEqual(editor.layout.checks.errors, []);

  editor.partitionClick({ x: 4000, y: 0 });
  editor.partitionClick({ x: 4000, y: 8000 });
  assert.equal(editor.draft.partitions.length, 1);
  assert.equal(editor.draft.partitions[0].id, 'P1');
  await editor.refreshLayout();
  assert.equal(editor.layout.rooms.length, 2);
  assert.match(editor.layout.checks.errors.join('\n'), /unreachable/);
  editor.action('apply');
  assert.equal(builds(), 0, 'Unreachable draft must not request publication');

  editor.addOpening('door', 'P1', { x: 4000, y: 2000 });
  const hosted = editor.draft.openings.find(opening => opening.host === 'P1');
  assert.ok(hosted, 'An incomplete-access draft must allow placing its connecting door');
  assert.equal(hosted.offset_mm, 1500);
  await editor.refreshLayout();
  assert.deepEqual(editor.layout.checks.errors, []);
  assert.ok(editor.layout.rooms.every(room => room.reachable));
  assert.ok(editor.layout.connections.some(connection => connection.opening_id === hosted.id && connection.room_ids.length === 2));

  const selectedRoom = editor.layout.rooms.find(room => room.label_point_mm[0] < 4000);
  editor.setRoomProperties(selectedRoom.id, { name: 'Living', finishes: { floor: 'Timber', paint: 'Warm white' } });
  await editor.refreshLayout();
  const renamed = editor.layout.rooms.find(room => room.id === selectedRoom.id);
  assert.equal(renamed.name, 'Living');
  assert.equal(renamed.finishes.floor, 'Timber');
  assert.equal(renamed.finishes.paint, 'Warm white');

  editor.selectPartition('P1');
  const beforeMove = structuredClone(editor.draft);
  const emitted = changes.length;
  editor.pointerDown(pointer('partition', 'P1', { x: 4000, y: 4000 }));
  editor.pointerMove(pointer('partition', 'P1', { x: 3800, y: 4000 }));
  editor.pointerUp({ pointerId: 1 });
  assert.equal(editor.draft.partitions[0].start_mm[0], 3800);
  assert.equal(editor.draft.partitions[0].end_mm[0], 3800);
  assert.equal(changes.length, emitted + 1, 'A completed pointer drag commits one draft transition');
  await editor.refreshLayout();
  assert.deepEqual(editor.layout.checks.errors, []);
  assert.equal(editor.layout.rooms.find(room => room.id === selectedRoom.id).name, 'Living');

  editor.action('undo');
  await editor.refreshLayout();
  assert.deepEqual(editor.draft, beforeMove);
  editor.action('redo');
  await editor.refreshLayout();
  assert.equal(editor.draft.partitions[0].start_mm[0], 3800);
  editor.action('apply');
  assert.equal(builds(), 1, 'Only a solver-validated reachable draft may request publication');

  const beforeGrip = structuredClone(editor.draft);
  editor.pointerDown(pointer('partition-grip', 'P1', { x: 3800, y: 8000 }, 1, 'end'));
  editor.pointerMove(pointer('partition-grip', 'P1', { x: 3800, y: 7000 }, 1, 'end'));
  editor.pointerUp({ pointerId: 1 });
  assert.equal(editor.draft.partitions[0].end_mm[1], 7000, 'Endpoint grip edits the real partition draft');
  await editor.refreshLayout();
  assert.match(editor.layout.checks.errors.join('\n'), /floating endpoint/);
  editor.action('apply');
  assert.equal(builds(), 1, 'A disconnected endpoint draft must remain unpublished');
  editor.action('undo');
  await editor.refreshLayout();
  assert.deepEqual(editor.draft, beforeGrip);
  assert.deepEqual(editor.layout.checks.errors, []);

  editor.deletePartition('P1');
  await editor.refreshLayout();
  assert.equal(editor.draft.partitions.length, 0);
  assert.ok(!editor.draft.openings.some(opening => opening.host === 'P1'), 'Deleting a partition removes its hosted door');
  assert.equal(editor.layout.rooms.length, 1);
  assert.deepEqual(editor.layout.checks.errors, []);
  editor.action('undo');
  await editor.refreshLayout();
  assert.equal(editor.draft.partitions.length, 1);
  assert.ok(editor.draft.openings.some(opening => opening.id === hosted.id && opening.host === 'P1'));
  editor.action('redo');
  await editor.refreshLayout();
  assert.equal(editor.draft.partitions.length, 0);
  assert.ok(requested.some(draft => draft.partitions.length && !draft.openings.some(opening => opening.host === 'P1')),
    'The backend really evaluated the intermediate unreachable layout');
});

