import { getContext } from "svelte";
import type { TicketLocale } from "../ticketMachines";
export const MACHINE_ROWS = Symbol("visible machine rows");
export type MachineRowRegistry = { set: (key: symbol, locale: TicketLocale | undefined, rail: boolean) => void; delete: (key:symbol) => void };
export const machineRows = () => getContext<MachineRowRegistry | undefined>(MACHINE_ROWS);
