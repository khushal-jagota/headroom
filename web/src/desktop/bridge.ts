import type { PanelsBridge } from "../../../desktop/src/bridge";

export function desktopBridge(): PanelsBridge {
  return (window as unknown as Window & { panels: PanelsBridge }).panels;
}
