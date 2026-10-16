import type { Principal } from "./types";

// Role words are shared across holder controls. Assigned orchestrator identity stays
// available as detail; a foreign principal always retains its identifying value.
export type HolderOption = { value: string; label: string; detail?: string };

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
    { value: "owner", label: "User" },
    { value: "chief", label: "Chief" }
  ];
  if (sprintItem) options.push({ value: sprintItem.id, label: "Orchestrator", detail: sprintItem.title });
  const current = holderValue(holder);
  if (!options.some((option) => option.value === current)) {
    options.push({ value: current, label: holder?.kind === "sprint_item" ? "Orchestrator" : "Worker", detail: holder?.id || current });
  }
  return options;
}

export function holderLabelFor(
  sprintItem: { id: string; title: string } | null,
  holder: Principal | null
): string {
  const option = holderOptionsFor(sprintItem, holder).find((candidate) => candidate.value === holderValue(holder));
  if (!option) return holder?.id || "User";
  // The assigned parent has an identifying navigation link. Foreign holders have no
  // such context, so the face must retain their identity as well as their role.
  return option.detail && option.value !== sprintItem?.id ? `${option.label} · ${option.detail}` : option.label;
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
