/** The sandbox can only announce a primary press, never request a state or action. */
export const MANAGED_ARTIFACT_PRIMARY_PRESS = "panels:managed-artifact-primary-press";
export type RegisterManagedArtifactFrame = (frame: HTMLIFrameElement) => () => void;
