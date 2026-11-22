// Compile src into a private TMPDIR folder, then set PANELS_DESKTOP_TEST_BUILD to that folder.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { join } = require('node:path');
const { desktopRelease, newestDesktopRelease } = require(join(process.env.PANELS_DESKTOP_TEST_BUILD, 'updates/logic.js'));
const { windowlessInstall } = require(join(process.env.PANELS_DESKTOP_TEST_BUILD, 'updates/lifecycle.js'));

test('desktop release selection ignores server releases, drafts, and prereleases regardless of listing order', () => {
  const candidates = [
    ['desktop-v0.0.9', false, false], ['release-99999', false, false],
    ['desktop-v0.0.12', true, false], ['desktop-v0.0.11', false, true],
    ['desktop-v0.0.10', false, false], ['desktop-v0.0.8', false, false],
  ].map(args => desktopRelease(...args)).filter(Boolean);
  assert.equal(newestDesktopRelease(candidates).tag, 'desktop-v0.0.10');
  assert.equal(newestDesktopRelease([]), null);
});

test('a download needs ten continuous windowless minutes, reopening resets them, and quit cancels installation', () => {
  let now = 0, windows = 1, installed = 0;
  let nextId = 0;
  const timers = new Map();
  const clock = {
    now: () => now,
    schedule: (callback, delay) => { const id = ++nextId; timers.set(id, { at: now + delay, callback }); return id; },
    cancel: id => timers.delete(id),
  };
  function advance(duration) {
    now += duration;
    for (const [id, timer] of timers) if (timer.at <= now) { timers.delete(id); timer.callback(); }
  }
  const lifecycle = windowlessInstall(clock, () => windows, () => installed++, 600000);
  lifecycle.windowChanged();
  windows = 0;
  lifecycle.windowChanged();
  advance(600000);
  assert.equal(installed, 0, 'no installation without a verified download');
  windows = 1;
  lifecycle.windowChanged();
  lifecycle.downloaded();
  advance(600000);
  assert.equal(installed, 0, 'a visible window survives a ready update');
  windows = 0;
  lifecycle.windowChanged();
  advance(599999);
  assert.equal(installed, 0);
  windows = 1;
  lifecycle.windowChanged();
  windows = 0;
  lifecycle.windowChanged();
  advance(599999);
  assert.equal(installed, 0, 'reopening resets the full interval');
  advance(1);
  assert.equal(installed, 1);
  lifecycle.downloadPending();
  lifecycle.windowChanged();
  advance(600000);
  assert.equal(installed, 1, 'a new candidate cannot reuse previous verified readiness');
  lifecycle.downloaded();
  lifecycle.stop();
  advance(600000);
  assert.equal(installed, 1, 'quit cancels outstanding timer');
});

test('the native restart dialog waits for verified download and consent, and ignores consent for a previous server', async () => {
  const { EventEmitter } = require('node:events');
  const Module = require('node:module');
  const updater = new EventEmitter();
  const nativeUpdater = new EventEmitter();
  let installed = 0, floor = 2, windows = 1;
  const prompts = [];
  updater.setFeedURL = () => {};
  updater.checkForUpdates = async () => {};
  updater.quitAndInstall = () => installed++;
  const originalLoad = Module._load;
  Module._load = function(request, parent, ...rest) {
    if (parent?.filename.endsWith('/updates/index.js')) {
      if (request === 'electron') return {
        BrowserWindow: { getAllWindows: () => Array.from({ length: windows }, () => ({})) },
        autoUpdater: nativeUpdater,
        dialog: { showMessageBox: () => new Promise(resolve => prompts.push(resolve)) },
      };
      if (request === 'electron-updater') return { autoUpdater: updater };
      if (request === '../bridge_version') return { DESKTOP_BRIDGE_VERSION: 1 };
      if (request === '../server_meta') return { fetchDesktopCompatibility: async () => ({ minimum_bridge_version: floor }) };
      if (request === './feed') return { updateFeed: async () => 'https://private-proof.example/' };
    }
    return originalLoad.call(this, request, parent, ...rest);
  };
  let controller;
  try {
    const { startShellUpdates } = require(join(process.env.PANELS_DESKTOP_TEST_BUILD, 'updates/index.js'));
    controller = startShellUpdates(() => 'https://server.example');
    await new Promise(setImmediate);
    assert.equal(prompts.length, 0, 'required shell cannot prompt before verified download');
    updater.emit('update-downloaded', { panelsBridgeVersion: 1 });
    nativeUpdater.emit('update-downloaded');
    assert.equal(prompts.length, 0, 'a downloaded app below the required bridge cannot prompt');
    updater.emit('update-downloaded', {});
    nativeUpdater.emit('update-downloaded');
    assert.equal(prompts.length, 0, 'missing candidate bridge metadata cannot prompt');
    updater.emit('update-downloaded', { panelsBridgeVersion: 4 });
    assert.equal(prompts.length, 0, 'electron-updater download alone precedes Squirrel verification');
    nativeUpdater.emit('update-downloaded');
    assert.equal(prompts.length, 1);
    floor = 1;
    controller.serverChanged();
    await new Promise(setImmediate);
    prompts[0]({ response: 0 });
    await new Promise(setImmediate);
    assert.equal(installed, 0, 'consent for the previous server cannot restart');
    const later = controller.requireBridgeVersion(3);
    await new Promise(setImmediate);
    assert.equal(prompts.length, 2);
    prompts[1]({ response: 1 });
    assert.equal(await later, false);
    assert.equal(installed, 0, 'Later keeps the app running');
    const restart = controller.requireBridgeVersion(4);
    await new Promise(setImmediate);
    assert.equal(prompts.length, 3);
    assert.equal(installed, 0, 'a ready update still requires consent with a window open');
    prompts[2]({ response: 0 });
    assert.equal(await restart, false, 'incompatible screens cannot activate in the old process');
    assert.equal(installed, 1);
    assert.equal(updater.autoRunAppAfterInstall, true, 'explicit Restart brings the app back');
    const originalSetTimeout = global.setTimeout;
    let automaticInstall;
    try {
      global.setTimeout = callback => { automaticInstall = callback; return { unref() {} }; };
      windows = 0;
      controller.windowChanged();
    } finally { global.setTimeout = originalSetTimeout; }
    automaticInstall();
    assert.equal(installed, 2);
    assert.equal(updater.autoRunAppAfterInstall, false, 'windowless installation cannot reopen a window');
  } finally {
    controller?.stop();
    Module._load = originalLoad;
  }
});
