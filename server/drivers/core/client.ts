/** The presentation half of a driver: exactly what Panels sends to the apps. Plain data only. */
export interface DriverIcon {
  /** File name inside the driver package. */
  readonly file: string;
  readonly media_type: 'image/svg+xml' | 'image/png';
  /** True: the apps may tone or grey the icon. */
  readonly monochrome: boolean;
}
export interface DriverClientDefinition {
  /** The one display name. */
  readonly label: string;
  readonly icon: DriverIcon;
  /** The usage colour. */
  readonly accent_color: string;
}
