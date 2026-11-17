/** The presentation half of a driver: exactly what Panels sends to the apps. Plain data only. */
export interface DriverIcon {
  /** The icon file: new URL('./icon.png', import.meta.url) in the package's client.ts. SVG or PNG. */
  readonly file: URL;
  /**
   * True: a single-colour glyph the apps may tint or grey. False: a full-colour picture with its
   * own margin (about a tenth of each side), drawn slightly larger, never greyed.
   */
  readonly monochrome: boolean;
}
export interface DriverClientDefinition {
  /** The one display name. */
  readonly label: string;
  readonly icon: DriverIcon;
  /** The usage colour. */
  readonly accent_color: string;
}
