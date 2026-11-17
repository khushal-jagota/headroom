/** Signing a backend in from Panels. */
export type SignInState = 'pending' | 'awaiting_return_url' | 'confirmed' | 'unconfirmed' | 'ended';
export interface SignInSession {
  readonly session_id: string;
  readonly state: SignInState;
  readonly verification_url: string | null;
  readonly user_code: string | null;
  /** ISO datetime strings preserve source microseconds. */
  readonly started_at: Date | string;
  readonly deadline_at: Date | string;
  readonly detail: string | null;
  readonly account_label: string | null;
}
export interface DriverSignInController {
  read(): SignInSession | null;
  start(): Promise<SignInSession>;
  /** Throws UnknownSignInSession when the session is not the one in force. */
  cancel(session_id: string): Promise<SignInSession>;
  /** For a session in 'awaiting_return_url'. Throws UnknownSignInSession like cancel. */
  submit_return_url?(session_id: string, return_url: string): Promise<SignInSession>;
  sign_out?(): Promise<void>;
}
/** The backend cannot start a sign-in now. */
export class SignInUnavailable extends Error {}
export class UnknownSignInSession extends Error {}
