// Native update control. No IPC channel exposes this module to screens or artifacts.
import { BrowserWindow, dialog, autoUpdater as nativeUpdater } from 'electron';
import { autoUpdater } from 'electron-updater';
import { fetchDesktopCompatibility } from '../server_meta';
import { DESKTOP_BRIDGE_VERSION } from '../bridge_version';
import type { ShellUpdates } from './contracts';
import { CHECK_INTERVAL_MS, NO_WINDOW_DELAY_MS } from './configuration';
import { updateFeed } from './feed';
import { windowlessInstall } from './lifecycle';

/** A native caller must wait for true before it activates screens with this bridge floor. */
export function startShellUpdates(serverAddress: () => string | null): ShellUpdates {
  let candidateBridgeVersion = 0;
  let staged = false;
  let downloadedBridgeVersion = 0;
  let checking: Promise<void> | undefined;
  let stopped = false;
  let requiredBridgeVersion = 0;
  let promptedFor = 0;
  let dialogOpen = false;
  let serverGeneration = 0;

  const logError = (error: unknown): void => { console.warn('Desktop update:', error); };
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowPrerelease = false;
  autoUpdater.allowDowngrade = false;

  const installLifecycle = windowlessInstall({
    now: Date.now,
    schedule: (callback, delay) => { const timer = setTimeout(callback, delay); timer.unref(); return timer; },
    cancel: (timer) => clearTimeout(timer as ReturnType<typeof setTimeout>),
  }, () => BrowserWindow.getAllWindows().length, () => {
    if (!dialogOpen) {
      autoUpdater.autoRunAppAfterInstall = false;
      autoUpdater.quitAndInstall();
    }
  }, NO_WINDOW_DELAY_MS);

  async function askForRestart(): Promise<void> {
    if (stopped || downloadedBridgeVersion < requiredBridgeVersion || requiredBridgeVersion <= DESKTOP_BRIDGE_VERSION || promptedFor >= requiredBridgeVersion || dialogOpen) return;
    const generation = serverGeneration;
    const floor = requiredBridgeVersion;
    promptedFor = requiredBridgeVersion;
    dialogOpen = true;
    try {
      const result = await dialog.showMessageBox({
        type: 'info',
        title: 'Restart Panels',
        message: 'Panels needs to restart for its new screens.',
        detail: 'Your agents keep running.',
        buttons: ['Restart Panels', 'Later'],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
      });
      if (!stopped && generation === serverGeneration && floor === requiredBridgeVersion && downloadedBridgeVersion >= floor && result.response === 0) {
        autoUpdater.autoRunAppAfterInstall = true;
        autoUpdater.quitAndInstall();
      }
    } finally {
      dialogOpen = false;
      installLifecycle.windowChanged();
      if (generation !== serverGeneration || floor !== requiredBridgeVersion) void askForRestart().catch(logError);
    }
  }

  function check(): Promise<void> {
    if (checking) return checking;
    // Keep the native signature result tied to one candidate until the next launch.
    if (staged || stopped) return Promise.resolve();
    checking = (async () => {
      if (stopped) return;
      try {
        autoUpdater.setFeedURL({ provider: 'generic', url: await updateFeed() });
        await autoUpdater.checkForUpdates();
      } catch (error) {
        logError(error);
      }
    })().finally(() => { checking = undefined; });
    return checking;
  }

  async function requireBridgeVersion(required: number): Promise<boolean> {
    if (!Number.isSafeInteger(required) || required < 0) throw new Error('Invalid required desktop bridge version');
    requiredBridgeVersion = required;
    if (required <= DESKTOP_BRIDGE_VERSION) return true;
    await check();
    await askForRestart();
    return false;
  }

  async function checkCompatibility(): Promise<void> {
    const address = serverAddress();
    if (!address || stopped) return;
    try {
      const compatibility = await fetchDesktopCompatibility(address);
      // A result for the previous server cannot prompt after Change Server.
      if (stopped || address !== serverAddress()) return;
      await requireBridgeVersion(compatibility.minimum_bridge_version);
    } catch (error) {
      logError(error);
    }
  }

  const candidateDownloaded = (info: unknown): void => {
    staged = true;
    downloadedBridgeVersion = 0;
    installLifecycle.downloadPending();
    const floor = (info as { panelsBridgeVersion?: unknown })?.panelsBridgeVersion;
    candidateBridgeVersion = typeof floor === 'number' && Number.isSafeInteger(floor) && floor >= 1 ? floor : 0;
  };
  // electron-updater emits before Squirrel verifies the signature. The native event is ready.
  const downloaded = (): void => {
    downloadedBridgeVersion = candidateBridgeVersion;
    installLifecycle.downloaded();
    void askForRestart().catch(logError);
  };
  const updateError = (error: unknown): void => {
    staged = false;
    downloadedBridgeVersion = 0;
    installLifecycle.downloadPending();
    logError(error);
  };
  autoUpdater.on('error', updateError);
  autoUpdater.on('update-downloaded', candidateDownloaded);
  nativeUpdater.on('update-downloaded', downloaded);
  const interval = setInterval(() => {
    void check();
    void checkCompatibility();
  }, CHECK_INTERVAL_MS);
  interval.unref();
  void check();
  void checkCompatibility();
  installLifecycle.windowChanged();

  return {
    windowChanged: installLifecycle.windowChanged,
    requireBridgeVersion,
    serverChanged: () => {
      serverGeneration++;
      requiredBridgeVersion = 0;
      promptedFor = 0;
      void checkCompatibility();
    },
    stop: () => {
      stopped = true;
      installLifecycle.stop();
      clearInterval(interval);
      // Downloads can still fail while the process quits. Keep the error listener attached.
      autoUpdater.removeListener('update-downloaded', candidateDownloaded);
      nativeUpdater.removeListener('update-downloaded', downloaded);
    },
  };
}
