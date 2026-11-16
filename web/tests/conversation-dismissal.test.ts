import { afterEach, expect, it, vi } from "vitest";
import { createConversationDismissal } from "../src/lib/conversation/dismissal";

afterEach(() => vi.unstubAllGlobals());

it("dismisses for a primary press in a visible registered artifact and removes listeners", () => {
  const windowEvents = Object.assign(new EventTarget(), { innerWidth: 1000, innerHeight: 800 });
  vi.stubGlobal("window", windowEvents);
  vi.stubGlobal("document", { visibilityState: "visible" });
  vi.stubGlobal("getComputedStyle", () => ({ overflowX: "visible", overflowY: "visible" }));
  const dismiss = vi.fn();
  const controller = createConversationDismissal(dismiss);
  const contents = new EventTarget();
  const frame = Object.assign(new EventTarget(), {
    contentDocument: contents, isConnected: true, closest: () => null,
    checkVisibility: () => true,
    getBoundingClientRect: () => ({ left: 10, right: 400, top: 10, bottom: 400 }),
    parentElement: null
  });
  const host = Object.assign(new EventTarget(), { contains: (node: unknown) => node === frame });
  const surface = controller.surface(host as unknown as HTMLElement);
  const unregister = controller.registerArtifactFrame(frame as unknown as HTMLIFrameElement);
  const press = (primary: boolean, button: number) => {
    const event = new Event("pointerdown");
    Object.defineProperties(event, { isPrimary: { value: primary }, button: { value: button } });
    contents.dispatchEvent(event);
  };
  press(false, 0); press(true, 1);
  expect(dismiss).not.toHaveBeenCalled();
  press(true, 0);
  expect(dismiss).toHaveBeenCalledTimes(1);
  unregister(); press(true, 0);
  expect(dismiss).toHaveBeenCalledTimes(1);
  surface.destroy();
});
