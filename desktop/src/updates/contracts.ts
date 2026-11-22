/** The main process owns this interface. It is never exposed through the preload. */
export interface ShellUpdates {
  windowChanged(): void;
  serverChanged(): void;
  requireBridgeVersion(required: number): Promise<boolean>;
  stop(): void;
}

export interface DesktopRelease {
  tag: string;
  version: readonly [number, number, number];
}

export interface UpdateLifecycle {
  downloaded: boolean;
  noWindowSince: number | null;
}

export interface InstallClock {
  now(): number;
  schedule(callback: () => void, delay: number): unknown;
  cancel(timer: unknown): void;
}
export interface WindowlessInstall {
  windowChanged(): void;
  downloadPending(): void;
  downloaded(): void;
  stop(): void;
}
