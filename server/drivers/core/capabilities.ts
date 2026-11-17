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
