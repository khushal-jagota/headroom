export type ShellUtility = "review" | "usage" | "ideas" | "feedback" | "vps" | "settings";
export type ShellDestinationDefinition = { key: ShellUtility; label: string; href: string; group: "Work" | "System" | null };
export const SHELL_DESTINATIONS: readonly ShellDestinationDefinition[] = [
  { key: "review", label: "Review", href: "#/review", group: null },
  { key: "usage", label: "Usage", href: "#/backends", group: "System" },
  { key: "ideas", label: "Ideas", href: "#/ideas", group: "Work" },
  { key: "feedback", label: "Feedback", href: "#/feedback", group: "Work" },
  { key: "vps", label: "VPS", href: "#/vps", group: "System" },
  { key: "settings", label: "Settings", href: "#/settings", group: "System" }
];
