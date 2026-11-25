/** One admission owner separates new starts from already accepted saves. */
import type { WorkspaceAdmission as HttpWorkspaceAdmission } from '../core/http/middleware.ts';
import { AsyncLocalStorage } from 'node:async_hooks';
import { PlannerError } from '../core/contracts.ts';

export class WorkspaceAdmission implements HttpWorkspaceAdmission {
  private readonly send_scope = new AsyncLocalStorage<{ active: boolean }>();
  private _closed = false;
  private _starts_closed = false;
  private _active = 0;
  private readonly operations_finished = new Set<() => void>();
  get active(): boolean {
    return this._active !== 0;
  }
  get closed(): boolean {
    return this._closed;
  }
  get starts_closed(): boolean {
    return this._starts_closed;
  }
  refusal(): PlannerError {
    return new PlannerError(
      'update_in_progress',
      'Workspace admission is closed for an owner update.',
    );
  }
  close_starts(): boolean {
    if (this._closed || this._starts_closed) return false;
    this._starts_closed = true;
    return true;
  }
  subscribe_operations_finished(callback: () => void): () => void {
    this.operations_finished.add(callback);
    return () => this.operations_finished.delete(callback);
  }
  seal(): void {
    this._closed = this._starts_closed = true;
  }

  /** A send handler retains its admission through content preparation and dispatch. */
  run_send<T>(operation: (admitted: boolean) => T): T {
    return this.operation(operation, { starts: true });
  }

  operation(options?: { starts?: boolean }): ReturnType<HttpWorkspaceAdmission['operation']>;
  operation<T>(operation: (admitted: boolean) => T, options?: { starts?: boolean }): T;
  operation<T>(
    operation_or_options?: ((admitted: boolean) => T) | { starts?: boolean },
    options: { starts?: boolean } = {},
  ): T | ReturnType<HttpWorkspaceAdmission['operation']> {
    const operation = typeof operation_or_options === 'function' ? operation_or_options : undefined;
    const starts = (
      operation === undefined ? (operation_or_options as { starts?: boolean } | undefined) : options
    )?.starts;
    const inherited = this.send_scope.getStore()?.active === true;
    const admitted = !this._closed && !(starts && this._starts_closed && !inherited);
    if (admitted) this._active += 1;
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      if (!admitted) return;
      this._active -= 1;
      if (this._active === 0) {
        for (const callback of [...this.operations_finished]) {
          try {
            callback();
          } catch (error) {
            console.error('Workspace completion listener failed', error);
          }
        }
      }
    };
    if (operation === undefined) return { admitted, release };
    const scope = admitted && starts && !inherited ? { active: true } : null;
    const finish = () => {
      if (scope) scope.active = false;
      release();
    };
    let result: T;
    try {
      result = scope ? this.send_scope.run(scope, () => operation(admitted)) : operation(admitted);
    } catch (error) {
      finish();
      throw error;
    }
    if (result instanceof Promise) return result.finally(finish) as T;
    finish();
    return result;
  }

  claim(operation: () => void, options: { idle: () => boolean; require_idle: boolean }): boolean {
    if (this._closed || (options.require_idle && (this._active !== 0 || !options.idle())))
      return false;
    operation();
    this.seal();
    return true;
  }

  reopen(): void {
    this._closed = this._starts_closed = false;
  }
}
