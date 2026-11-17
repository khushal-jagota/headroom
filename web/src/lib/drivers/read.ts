import { fetchJson } from "../api";
import type { DriversResponse } from "./contracts";

/** Every driver this server runs, in its order, with what the apps draw for each. */
export function readDrivers(): Promise<DriversResponse> {
  return fetchJson<DriversResponse>("/api/drivers");
}
