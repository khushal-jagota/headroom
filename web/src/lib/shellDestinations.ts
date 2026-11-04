export type ShellUtility = "review" | "usage" | "ideas" | "vps" | "settings";
export type ShellDestinationDefinition = { key: ShellUtility; label: string; href: string; group: "Work" | "System" | null };
export const SHELL_DESTINATIONS: readonly ShellDestinationDefinition[] = [
  { key: "review", label: "Review", href: "#/review", group: null },
  { key: "usage", label: "Usage", href: "#/backends", group: "System" },
  { key: "ideas", label: "Ideas", href: "#/ideas", group: "Work" },
  { key: "vps", label: "VPS", href: "#/vps", group: "System" },
  { key: "settings", label: "Settings", href: "#/settings", group: "System" }
];
