import type { Principal } from "./types";

// Role words are shared across holder controls. Assigned manager identity stays
// available as detail; a foreign principal always retains its identifying value.
export type HolderOption = { value: string; label: string; detail?: string };

export const OWNER_HOLDER: Principal = { kind: "owner", id: "owner" };
export const CHIEF_HOLDER: Principal = { kind: "chief", id: "chief" };

export function holderValue(holder: Principal | null): string {
  if (!holder) return "owner";
  return holder.kind === "owner" || holder.kind === "chief" ? holder.kind : holder.id;
}

export function holderOptionsFor(
  manager: { id: string; title: string } | null,
  holder: Principal | null = null
): HolderOption[] {
  const options: HolderOption[] = [
    { value: "owner", label: "User" },
    { value: "chief", label: "Chief" }
  ];
  if (manager) options.push({ value: manager.id, label: "Manager", detail: manager.title });
  const current = holderValue(holder);
  if (!options.some((option) => option.value === current)) {
    options.push({ value: current, label: holder?.kind === "manager" ? "Manager" : "Worker", detail: holder?.id || current });
  }
  return options;
}

export function holderLabelFor(
  manager: { id: string; title: string } | null,
  holder: Principal | null
): string {
  const option = holderOptionsFor(manager, holder).find((candidate) => candidate.value === holderValue(holder));
  if (!option) return holder?.id || "User";
  // The assigned parent has an identifying navigation link. Foreign holders have no
  // such context, so the face must retain their identity as well as their role.
  return option.detail && option.value !== manager?.id ? `${option.label} · ${option.detail}` : option.label;
}

export function holderFromValue(
  value: string,
  manager: { id: string; title: string } | null
): Principal {
  if (value === "owner") return OWNER_HOLDER;
  if (value === "chief") return CHIEF_HOLDER;
  if (manager && value === manager.id) return { kind: "manager", id: value };
  return { kind: value.startsWith("si_") ? "manager" : "ticket", id: value };
}
