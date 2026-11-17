/** Server-side static data about a driver. Readable without loading the driver's runtime. */
export interface DriverManifest {
  readonly executable: {
    readonly name: string;
    /** An environment variable that names the executable instead of PATH. */
    readonly override_environment_name?: string;
  };
  /** The provider state home. */
  readonly home?: {
    readonly environment_name: string;
    readonly extra_environment_names?: readonly string[];
  };
  readonly install?: {
    readonly npm_packages?: readonly string[];
    /** Repository paths a release must ship for this driver. */
    readonly release_files?: readonly string[];
  };
}
