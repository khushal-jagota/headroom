import {
  previewHashHref,
  resolvePreview,
  targetFromHref,
  targetFromPreviewHref,
  type ManagedFileTarget
} from "./filePreview";
import type { PendingTicketProposal, SprintItemArtifact, TicketFieldValues } from "./types";
import { markdownLinkHrefs } from "./markdownPipeline";

export type ArtifactStripItem = {
  key: string;
  path: string;
  label: string;
  kind: string;
  href: string | null;
  target: ManagedFileTarget | null;
  children: ArtifactStripItem[];
};

function targetKey(target: ManagedFileTarget): string {
  return target.kind === "ticket-file"
    ? `ticket:${target.ticketId}:${target.path}`
    : `item:${target.sprintItemId}:${target.path}`;
}

function pathParts(path: string): { stem: string; parent: string; kind: string } {
  const parts = path.split("/");
  const file = parts.at(-1) || path;
  const dot = file.lastIndexOf(".");
  return {
    stem: dot > 0 ? file.slice(0, dot) : file,
    parent: parts.length > 1 ? parts.at(-2) || "" : "",
    kind: dot > 0 ? file.slice(dot + 1) : "file"
  };
}

function rows(
  inputs: Array<{ key: string; path: string; target: ManagedFileTarget | null }>
): ArtifactStripItem[] {
  const stemCounts = new Map<string, number>();
  for (const input of inputs) {
    const stem = pathParts(input.path).stem.toLocaleLowerCase();
    stemCounts.set(stem, (stemCounts.get(stem) || 0) + 1);
  }
  return inputs.map((input) => {
    const parts = pathParts(input.path);
    const collision = (stemCounts.get(parts.stem.toLocaleLowerCase()) || 0) > 1;
    return {
      ...input,
      label: collision && parts.parent ? `${parts.parent} / ${parts.stem}` : parts.stem,
      kind: parts.kind,
      href: input.target ? previewHashHref(input.target) : null,
      children: []
    };
  });
}

export function sprintItemArtifactStripItems(
  sprintItemId: string,
  artifacts: SprintItemArtifact[],
  parentKey = ""
): ArtifactStripItem[] {
  return artifacts.map((artifact) => {
    const key = parentKey ? `${parentKey}/${artifact.name}` : artifact.name;
    const target = artifact.opens
      ? targetFromHref(`/files/sprint-items/${sprintItemId}/${artifact.opens}`)
      : null;
    const managed = target && target.kind === "sprint-item-file" ? target : null;
    const dot = artifact.name.lastIndexOf(".");
    const children = sprintItemArtifactStripItems(sprintItemId, artifact.children, key);
    return {
      key,
      path: artifact.opens || key,
      // The name carries the folder; the extension is already said by the kind beside it.
      label: dot > 0 ? artifact.name.slice(0, dot) : artifact.name,
      // A folder with no index opens nothing, so it says how much is inside instead.
      kind: artifact.opens ? extensionOf(artifact.opens) : String(children.length),
      href: managed ? previewHashHref(managed) : null,
      target: managed,
      children
    };
  });
}

function extensionOf(path: string): string {
  const file = path.split("/").at(-1) || path;
  const dot = file.lastIndexOf(".");
  return dot > 0 ? file.slice(dot + 1) : "file";
}

function managedTargets(markdown: string): ManagedFileTarget[] {
  return markdownLinkHrefs(markdown).flatMap((href) => {
    const preview = targetFromPreviewHref(href);
    if (preview) return [preview];
    const target = targetFromHref(href);
    return target.kind === "ticket-file" || target.kind === "sprint-item-file" ? [target] : [];
  });
}

export function artifactChipIsOverflow(index: number, itemCount: number): boolean {
  return itemCount > 6 && index >= 5;
}

export function managedFileTargetFromLinkHref(href: string | null): ManagedFileTarget | null {
  const preview = targetFromPreviewHref(href);
  if (preview) return preview;
  const direct = href ? targetFromHref(href) : null;
  return direct?.kind === "ticket-file" || direct?.kind === "sprint-item-file" ? direct : null;
}

export function ticketArtifactStripItems(
  fieldIds: string[],
  values: TicketFieldValues,
  proposal: PendingTicketProposal | null
): ArtifactStripItem[] {
  const sources = [proposal?.body || "", ...[...fieldIds].reverse().map((field) => values[field] || "")];
  const seen = new Set<string>();
  const targets = sources.flatMap(managedTargets).filter((target) => {
    const key = targetKey(target);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return rows(
    targets.map((target) => ({
      key: targetKey(target),
      path: target.path,
      target
    }))
  );
}

export function artifactPreviewLabel(target: ManagedFileTarget): string {
  return resolvePreview(target).label;
}
