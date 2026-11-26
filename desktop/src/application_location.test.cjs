// Native relocation and dialogs stay stubbed. No Electron process or Mac window starts.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { join } = require('node:path');
const Module = require('node:module');

function loadInstallation(electron) {
  const path = join(process.env.PANELS_DESKTOP_TEST_BUILD, 'application_location.js');
  const originalLoad = Module._load;
  Module._load = function(request, parent, ...rest) {
    if (request === 'electron' && parent?.filename === path) return electron;
    return originalLoad.call(this, request, parent, ...rest);
  };
  try {
    delete require.cache[path];
    return require(path).prepareApplicationLocation;
  } finally { Module._load = originalLoad; }
}

async function withMac(callback) {
  const descriptor = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'darwin' });
  try { await callback(); } finally { Object.defineProperty(process, 'platform', descriptor); }
}

function host({ packaged = true, installed = false, response = 0, move = () => true } = {}) {
  const messages = [];
  let moves = 0;
  return {
    messages,
    moves: () => moves,
    app: {
      isPackaged: packaged,
      isInApplicationsFolder: () => installed,
      moveToApplicationsFolder: () => { moves++; return move(); },
    },
    dialog: { showMessageBox: async options => { messages.push(options); return { response }; } },
  };
}

test('installed apps, development launches, and other platforms never prompt or move', async () => {
  await withMac(async () => {
    for (const options of [{ installed: true }, { packaged: false }]) {
      const native = host(options);
      assert.equal(await loadInstallation(native)(), true);
      assert.equal(native.messages.length, 0);
      assert.equal(native.moves(), 0);
    }
    Object.defineProperty(process, 'platform', { value: 'linux' });
    const native = host();
    assert.equal(await loadInstallation(native)(), true);
    assert.equal(native.messages.length, 0);
    assert.equal(native.moves(), 0);
  });
});

test('declining the offer continues without moving the app', async () => {
  await withMac(async () => {
    const native = host({ response: 1 });
    assert.equal(await loadInstallation(native)(), true);
    assert.equal(native.messages.length, 1);
    assert.equal(native.messages[0].buttons[native.messages[0].cancelId], 'Not Now');
    assert.equal(native.moves(), 0);
  });
});

test('accepting a successful native move stops startup in the old copy', async () => {
  await withMac(async () => {
    const native = host();
    assert.equal(await loadInstallation(native)(), false);
    assert.equal(native.moves(), 1);
    assert.equal(native.messages.length, 1);
  });
});

test('canceling native authorization continues without a second dialog', async () => {
  await withMac(async () => {
    const native = host({ move: () => false });
    assert.equal(await loadInstallation(native)(), true);
    assert.equal(native.moves(), 1);
    assert.equal(native.messages.length, 1);
  });
});

test('a failed native move explains the error and leaves the app usable', async () => {
  await withMac(async () => {
    const native = host({ move: () => { throw new Error('The destination is not writable.'); } });
    assert.equal(await loadInstallation(native)(), true);
    assert.equal(native.moves(), 1);
    assert.equal(native.messages.length, 2);
    assert.equal(native.messages[1].type, 'error');
    assert.match(native.messages[1].detail, /The destination is not writable\./);
    assert.match(native.messages[1].detail, /drag it into Applications/);
  });
});
