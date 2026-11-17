/** Panels policy bounds, independent of the upstream code's expiry. */
export interface SignInBounds {
  readonly child_startup_seconds: number;
  readonly session_seconds: number;
  readonly cancel_seconds: number;
}
export const DEFAULT_SIGN_IN_BOUNDS: SignInBounds = {
  child_startup_seconds: 20,
  session_seconds: 10 * 60,
  cancel_seconds: 10,
};
