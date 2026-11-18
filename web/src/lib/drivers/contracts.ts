/** The driver list the server sends at GET /api/drivers. The server imports these types. */

export type DriverIconMediaType = "image/svg+xml" | "image/png";

export type DriverIconPresentation = {
  /** Same-origin path carrying a content version: /api/drivers/<kind>/icon?v=<12 hex of sha256>. */
  url: string;
  media_type: DriverIconMediaType;
  /** True: a single-colour glyph the apps may tint or grey. False: a full-colour picture
   *  with its own margin (about a tenth of each side), drawn slightly larger, never greyed. */
  monochrome: boolean;
};

export type DriverPresentation = {
  driver_kind: string;
  label: string;
  icon: DriverIconPresentation;
  accent_color: string;
  /** Panels reads this backend's latest version and can run its updater. */
  checks_for_updates: boolean;
};

export type DriversResponse = {
  drivers: DriverPresentation[];
  /** The backend a conversation starts on when nothing chooses one (DEFAULT_BACKEND_INSTANCE_ID). */
  default_backend_instance_id: string;
};
