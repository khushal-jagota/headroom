/** How a backend key is named and drawn, from the server's driver list.
 *
 * The apps hold no backend names of their own. A key the list names gets that driver's
 * label and icon. A key it does not name still gets something honest: its own name,
 * capitalised, and a mark made of its initials. While the list has not arrived, a mark
 * is an empty box of the same size, so nothing moves when it does.
 */
import type { DriverPresentation } from "./contracts";

export type BackendMarkPresentation =
  | Readonly<{ kind: "icon"; url: string; picture: boolean }>
  | Readonly<{ kind: "initials"; text: string }>
  | Readonly<{ kind: "pending" }>;

/** A default backend instance's id is its driver kind, so a key finds its driver directly. */
export function driverFor(
  key: string,
  drivers: readonly DriverPresentation[] | undefined
): DriverPresentation | null {
  return drivers?.find((driver) => driver.driver_kind === key) ?? null;
}

export function backendLabel(key: string, drivers: readonly DriverPresentation[] | undefined): string {
  return driverFor(key, drivers)?.label ?? (key === "" ? "" : key[0].toUpperCase() + key.slice(1));
}

/** "antigravity" gives "A"; "open-code" gives "OC". */
export function backendInitials(key: string): string {
  return key
    .split(/[-_]/)
    .filter((part) => part !== "")
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

export function backendMark(
  key: string,
  drivers: readonly DriverPresentation[] | undefined
): BackendMarkPresentation {
  if (drivers === undefined) return { kind: "pending" };
  const driver = driverFor(key, drivers);
  if (driver === null) return { kind: "initials", text: backendInitials(key) };
  return { kind: "icon", url: driver.icon.url, picture: !driver.icon.monochrome };
}
