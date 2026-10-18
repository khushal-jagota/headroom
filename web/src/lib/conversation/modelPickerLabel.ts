import type { BackendModel } from "./wire";

/** Match Claude's versioned_display_name: dates are release stamps, [1m] is context. */
function readableResolvedModel(identifier: string): string {
  const largeContext = identifier.endsWith("[1m]");
  const base = largeContext ? identifier.slice(0, -4) : identifier;
  const [provider, family, ...parts] = base.split("-");
  if (provider === "claude" && family) {
    const version = parts.filter((part) => !/^\d{8}$/.test(part));
    if (version.every((part) => /^\d+$/.test(part))) {
      const name = family[0].toUpperCase() + family.slice(1);
      return `${name}${version.length ? ` ${version.join(".")}` : ""}${largeContext ? " (1M)" : ""}`;
    }
  }
  if (/^gpt-\d+(?:\.\d+)*(?:-[a-z0-9]+)*$/.test(identifier)) {
    return `GPT ${identifier.slice(4).split("-").map((part) =>
      part[0].toUpperCase() + part.slice(1)
    ).join(" ")}`;
  }
  return identifier;
}

/** The closed face names the resolution; the open list still names the choice. */
export function resolvedModelPickerLabel(
  models: readonly BackendModel[],
  value: string
): string {
  const selected = models.find((model) => model.model_id === value);
  const resolved = selected?.resolved_model_id;
  if (!resolved) return selected?.display_name ?? value;
  const alias = selected?.selected_variant;
  if (resolved === alias) return selected?.display_name ?? resolved;

  const concrete = models.find((model) => model.model_id === resolved);
  const label = concrete?.display_name;
  // Cached catalogue decoration can give a concrete entry its bare variant name.
  // That name is not evidence that the resolved version is unknown.
  if (label && label !== resolved && (!alias || label.toLowerCase() !== alias.toLowerCase())) {
    return label;
  }
  return readableResolvedModel(resolved);
}
