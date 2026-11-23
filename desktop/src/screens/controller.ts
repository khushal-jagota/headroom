import type { ScreenMeta } from './contracts';
import { ScreenStore, isDigest } from './store';
import { MAX_SCREEN_BYTES, SCREEN_REQUEST_TIMEOUT_MS } from './configuration';
interface ServerMeta extends ScreenMeta {
  app_sha: string | null;
  compatibility?: { desktop_shell?: { minimum_bridge_version?: unknown } };
}
export interface ScreenPresentation { root: string; appSha: string | null; serverUpdate: boolean }
interface Dependencies {
  address(): string | null;
  canActivate(): boolean;
  request(path: string, signal: AbortSignal): Promise<Response>;
  requireBridgeVersion(required: number): Promise<boolean>;
  present(value: ScreenPresentation, reload: boolean): void;
}
/** Only this native owner selects a new copy. The renderer supplies no activation input. */
export class ScreenController {
  private generation = 0;
  private busy: Promise<void> | null = null;
  private serverUpdate = false;
  private stopped = false;
  private started = false;
  constructor(readonly store: ScreenStore, private readonly dependencies: Dependencies) {}
  private show(reload: boolean): void {
    this.dependencies.present({ root: this.store.root, appSha: this.store.current?.appSha ?? null, serverUpdate: this.serverUpdate }, reload);
  }
  async connect(): Promise<void> {
    this.generation++;
    this.store.select(this.dependencies.address(), !this.started);
    this.started = true;
    this.serverUpdate = false;
    this.show(false);
    // A prior server's pending request must settle before the new probe owns the store.
    await this.busy;
    await this.check();
  }
  private async meta(): Promise<ServerMeta> {
    const response = await this.dependencies.request('/api/meta', AbortSignal.timeout(SCREEN_REQUEST_TIMEOUT_MS));
    if (!response.ok) throw new Error('Screen metadata unavailable');
    return await response.json() as ServerMeta;
  }
  check(): Promise<void> {
    if (this.busy) return this.busy;
    const generation = this.generation;
    const address = this.dependencies.address();
    const current = (): boolean => !this.stopped && generation === this.generation && address === this.dependencies.address();
    if (!address || this.stopped) return Promise.resolve();
    this.busy = (async () => {
      try {
        const meta = await this.meta();
        if (!current()) return;
        if (!isDigest(meta.screens?.sha256)) {
          this.store.waiting = null;
          this.store.cleanup();
          const changed = !this.serverUpdate;
          this.serverUpdate = true;
          this.show(changed);
          return;
        }
        const digest = meta.screens.sha256;
        this.store.waiting = isDigest(meta.waiting_screens?.sha256) ? meta.waiting_screens.sha256 : null;
        const download = async (value: string): Promise<void> => {
          if (this.store.verify(value)) return;
          const response = await this.dependencies.request(`/api/screens/${value}`, AbortSignal.timeout(SCREEN_REQUEST_TIMEOUT_MS));
          if (!response.ok) throw new Error('Screen download unavailable');
          const length = Number(response.headers.get('content-length'));
          if (length > MAX_SCREEN_BYTES * 2) throw new Error('Screen download too large');
          const text = await response.text();
          if (!current()) return;
          if (Buffer.byteLength(text) > MAX_SCREEN_BYTES * 2) throw new Error('Screen download too large');
          this.store.install(JSON.parse(text), value);
        };
        if (digest !== this.store.rejected) {
          await download(digest);
          if (!current()) return;
          const latest = await this.meta();
          if (!current() || latest.screens?.sha256 !== digest || latest.app_sha !== meta.app_sha) return;
          const floor = latest.compatibility?.desktop_shell?.minimum_bridge_version;
          if (typeof floor !== 'number' || !Number.isSafeInteger(floor) || floor < 1) throw new Error('Server bridge floor unavailable');
          const allowed = await this.dependencies.requireBridgeVersion(floor);
          if (!current()) return;
          if (allowed && this.dependencies.canActivate()) {
            const confirmed = await this.meta();
            if (!current() || !this.dependencies.canActivate() || confirmed.screens?.sha256 !== digest || confirmed.app_sha !== latest.app_sha || confirmed.compatibility?.desktop_shell?.minimum_bridge_version !== floor) return;
            const copy = this.store.current;
            if (copy?.digest !== digest || copy.appSha !== latest.app_sha) {
              this.store.activate({ digest, appSha: typeof latest.app_sha === 'string' ? latest.app_sha : null });
              this.serverUpdate = false;
              this.show(true);
            } else if (this.serverUpdate) {
              this.serverUpdate = false;
              this.show(true);
            }
          }
        }
        if (this.store.waiting && this.store.waiting !== this.store.rejected) await download(this.store.waiting);
        if (current()) this.store.cleanup([digest]);
      } catch (error) {
        // Network failures retain the verified local copy or bundled baseline.
        console.warn('Desktop screens:', error);
      }
    })().finally(() => { this.busy = null; });
    return this.busy;
  }
  async prepare(): Promise<void> { await this.busy; await this.check(); }
  async retry(): Promise<void> { await this.busy; this.store.retryRejected(); await this.check(); }
  ready(): void { if (this.store.pending) this.store.ready(); }
  failed(): void {
    if (this.serverUpdate || !this.store.current) return;
    this.store.rollback();
    this.show(true);
  }
  stop(): void { this.stopped = true; this.generation++; }
}
