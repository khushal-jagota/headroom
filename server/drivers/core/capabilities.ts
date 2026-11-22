/** T3 names and grouping, with only the fields Panels reads. All required: each driver decides each one. */
export interface BackendCapabilities {
  readonly sessions: {
    /** A running session can take a new model without a restart. */
    readonly supportsModelSwitchInSession: boolean;
  };
  readonly turns: {
    /** A prompt can steer the active turn. */
    readonly supportsActiveSteering: boolean;
  };
}

/**
 * What every child of the shared ACP base can do. It sets the model and reasoning effort through
 * session config options in a running session, and it has no steer: a steer is refused with
 * `backend_cannot_steer` and the message waits for the next prompt.
 */
export const ACP_CHILD_CAPABILITIES: BackendCapabilities = {
  sessions: { supportsModelSwitchInSession: true },
  turns: { supportsActiveSteering: false },
};
