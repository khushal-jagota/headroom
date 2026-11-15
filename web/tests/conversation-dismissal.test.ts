import { afterEach, expect, it, vi } from "vitest";
import { createConversationDismissal } from "../src/lib/conversation/dismissal";
import { MANAGED_ARTIFACT_PRIMARY_PRESS } from "../src/lib/managedArtifactPress";

afterEach(() => vi.unstubAllGlobals());

it("authorizes only the visible registered artifact frame on its own mounted surface", () => {
  const windowEvents = new EventTarget();
  Object.assign(windowEvents, { innerWidth: 1000, innerHeight: 800 });
  vi.stubGlobal("window", windowEvents);
  const documentState = { visibilityState: "visible" };
  vi.stubGlobal("document", documentState);
  vi.stubGlobal("getComputedStyle", () => ({ overflowX: "hidden", overflowY: "hidden" }));
  const dismiss = vi.fn();
  const controller = createConversationDismissal(dismiss);
  const host = Object.assign(new EventTarget(), { contains: (node: unknown) => node === frame });
  const surface = controller.surface(host as unknown as HTMLElement);
  let connected = true, visible = true, inline = false, bottom = 400, clipped = false;
  const source = {} as Window;
  const frame = {
    contentWindow: source,
    get isConnected() { return connected; },
    closest: () => inline ? {} : null,
    checkVisibility: () => visible,
    getBoundingClientRect: () => ({ left: 10, right: 400, top: bottom - 100, bottom }),
    get parentElement() { return clipped ? {
      parentElement: null,
      getBoundingClientRect: () => ({ left: 0, right: 500, top: 0, bottom: 100 })
    } : null; }
  } as unknown as HTMLIFrameElement;
  const send = (data: unknown = MANAGED_ARTIFACT_PRIMARY_PRESS, sender: Window = source) => {
    const event = new Event("message");
    Object.defineProperties(event, { data: { value: data }, source: { value: sender } });
    windowEvents.dispatchEvent(event);
  };
  send(); // Same source, but not registered.
  expect(dismiss).not.toHaveBeenCalled();
  const unregister = controller.registerArtifactFrame(frame);
  send({ type: MANAGED_ARTIFACT_PRIMARY_PRESS });
  send(MANAGED_ARTIFACT_PRIMARY_PRESS, {} as Window);
  expect(dismiss).not.toHaveBeenCalled();
  documentState.visibilityState = "hidden"; send(); documentState.visibilityState = "visible";
  visible = false; send(); visible = true;
  connected = false; send(); connected = true;
  inline = true; send(); inline = false;
  bottom = 1100; send(); bottom = 400;
  clipped = true; send(); clipped = false;
  expect(dismiss).not.toHaveBeenCalled();
  const otherDismiss = vi.fn();
  const otherController = createConversationDismissal(otherDismiss);
  const otherFrame = { ...frame, contentWindow: {} as Window } as HTMLIFrameElement;
  const otherHost = Object.assign(new EventTarget(), { contains: (node: unknown) => node === otherFrame });
  const otherSurface = otherController.surface(otherHost as unknown as HTMLElement);
  otherController.registerArtifactFrame(otherFrame);
  send(); send();
  expect(dismiss).toHaveBeenCalledTimes(2);
  expect(otherDismiss).not.toHaveBeenCalled();
  send(MANAGED_ARTIFACT_PRIMARY_PRESS, otherFrame.contentWindow!);
  expect(dismiss).toHaveBeenCalledTimes(2);
  expect(otherDismiss).toHaveBeenCalledTimes(1);
  otherSurface.destroy();
  unregister(); send();
  expect(dismiss).toHaveBeenCalledTimes(2);
  controller.registerArtifactFrame(frame);
  surface.destroy(); send();
  expect(dismiss).toHaveBeenCalledTimes(2);
});
