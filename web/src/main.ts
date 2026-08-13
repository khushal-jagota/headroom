import "@fontsource/newsreader/400.css";
import "@fontsource/newsreader/500.css";
import "@fontsource/newsreader/600.css";
import "./atlas/theme.css";
import AppWithQueryClient from "./AppWithQueryClient.svelte";
import { ensureDebug } from "./lib/debug";
import { mount } from "svelte";

ensureDebug();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
  });
}

const app = mount(AppWithQueryClient, {
  target: document.getElementById("app") as HTMLElement
});

export default app;
