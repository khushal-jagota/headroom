import type { Principal } from "./types";

// Who a ceiling is held for, in the words Khushal reads: "then me", "then Chief",
// "then <Sprint Item>". The three options are the only principals worth choosing from
// his screens. A Ticket can hold another Ticket's ceiling and tooling keeps that, but it
// is not something he would pick, so it is not offered here.
//
// A holder already set to something outside the three — another Ticket, a Sprint Item
// that is not this one's — is still shown, so that a control can never silently
// misreport who holds the Ticket in front of it.

export type HolderOption = { value: string; label: string };

export const OWNER_HOLDER: Principal = { kind: "owner", id: "owner" };
export const CHIEF_HOLDER: Principal = { kind: "chief", id: "chief" };

export function holderValue(holder: Principal | null): string {
  if (!holder) return "owner";
  return holder.kind === "owner" || holder.kind === "chief" ? holder.kind : holder.id;
}

export function holderOptionsFor(
  sprintItem: { id: string; title: string } | null,
  holder: Principal | null = null
): HolderOption[] {
  const options: HolderOption[] = [
    { value: "owner", label: "me" },
    { value: "chief", label: "Chief" }
  ];
  if (sprintItem) options.push({ value: sprintItem.id, label: sprintItem.title });
  const current = holderValue(holder);
  if (!options.some((option) => option.value === current)) {
    options.push({ value: current, label: holder?.id || current });
  }
  return options;
}

export function holderFromValue(
  value: string,
  sprintItem: { id: string; title: string } | null
): Principal {
  if (value === "owner") return OWNER_HOLDER;
  if (value === "chief") return CHIEF_HOLDER;
  if (sprintItem && value === sprintItem.id) return { kind: "sprint_item", id: value };
  return { kind: value.startsWith("si_") ? "sprint_item" : "ticket", id: value };
}
