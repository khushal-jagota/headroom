import {
  previewHashHref,
  resolvePreview,
  targetFromHref,
  targetFromPreviewHref,
  type ManagedFileTarget
} from "./filePreview";
import type { PendingTicketProposal, ManagerArtifact, TicketFieldValues } from "./types";
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
    : `manager:${target.managerId}:${target.path}`;
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

export function managerArtifactStripItems(
  managerId: string,
  artifacts: ManagerArtifact[],
  parentKey = ""
): ArtifactStripItem[] {
  return artifacts.map((artifact) => {
    const key = parentKey ? `${parentKey}/${artifact.name}` : artifact.name;
    const target = artifact.opens
      ? targetFromHref(`/files/managers/${managerId}/${artifact.opens}`)
      : null;
    const managed = target && target.kind === "manager-file" ? target : null;
    const dot = artifact.name.lastIndexOf(".");
    const children = managerArtifactStripItems(managerId, artifact.children, key);
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
    return target.kind === "ticket-file" || target.kind === "manager-file" ? [target] : [];
  });
}

export function artifactChipIsOverflow(index: number, itemCount: number): boolean {
  return itemCount > 6 && index >= 5;
}

export function managedFileTargetFromLinkHref(href: string | null): ManagedFileTarget | null {
  const preview = targetFromPreviewHref(href);
  if (preview) return preview;
  const direct = href ? targetFromHref(href) : null;
  return direct?.kind === "ticket-file" || direct?.kind === "manager-file" ? direct : null;
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

/** Canonical managed artifacts remain discoverable even without a Markdown link.
 * Historical links to other managed files remain alongside them. */
export function ticketPageArtifactItems(
  ticketId: string,
  artifacts: ManagerArtifact[] | undefined,
  fieldIds: string[],
  values: TicketFieldValues,
  proposal: PendingTicketProposal | null
): ArtifactStripItem[] {
  const seen = new Set<string>();
  function canonical(entries: ManagerArtifact[], parentKey = ""): ArtifactStripItem[] {
    return entries.map((artifact) => {
      const key = parentKey ? `${parentKey}/${artifact.name}` : artifact.name;
      const target = artifact.opens ? managedFileTargetFromLinkHref(`/files/tickets/${ticketId}/${artifact.opens}`) : null;
      if (target) seen.add(targetKey(target));
      const children = canonical(artifact.children, key);
      const dot = artifact.name.lastIndexOf(".");
      return {key:`managed:${key}`, path:artifact.opens || key,
        label:dot > 0 ? artifact.name.slice(0,dot) : artifact.name,
        kind:artifact.opens ? extensionOf(artifact.opens) : String(children.length),
        href:target ? previewHashHref(target) : null, target, children};
    });
  }
  const managed = canonical(artifacts ?? []);
  const linked = ticketArtifactStripItems(fieldIds, values, proposal).filter((item) => {
    if (!item.target) return true;
    const key = targetKey(item.target);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return [...managed, ...linked];
}

export function artifactPreviewLabel(target: ManagedFileTarget): string {
  return resolvePreview(target).label;
}
