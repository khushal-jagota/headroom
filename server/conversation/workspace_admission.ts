/** One admission barrier shared by conversations and background starts. */
import type { WorkspaceAdmission as HttpWorkspaceAdmission } from '../core/http/middleware.ts';
export class WorkspaceAdmission implements HttpWorkspaceAdmission {
  private _closed = false;
  private _active = 0;
  get active(): boolean {
    return this._active !== 0;
  }
  get closed(): boolean {
    return this._closed;
  }

  // The callback owns the Python context manager's try/finally scope, including awaits.
  operation(): ReturnType<HttpWorkspaceAdmission['operation']>;
  operation<T>(operation: (admitted: boolean) => T): T;
  operation<T>(
    operation?: (admitted: boolean) => T,
  ): T | ReturnType<HttpWorkspaceAdmission['operation']> {
    const admitted = !this._closed;
    if (admitted) this._active += 1;
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      if (admitted) this._active -= 1;
    };
    if (operation === undefined) return { admitted, release };
    let result: T;
    try {
      result = operation(admitted);
    } catch (error) {
      release();
      throw error;
    }
    if (result instanceof Promise) return result.finally(release) as T;
    release();
    return result;
  }

  claim(operation: () => void, options: { idle: () => boolean; require_idle: boolean }): boolean {
    if (this._closed || (options.require_idle && (this._active !== 0 || !options.idle())))
      return false;
    operation();
    this._closed = true;
    return true;
  }

  reopen(): void {
    this._closed = false;
  }
}
