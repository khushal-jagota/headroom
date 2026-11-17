import AppWithQueryClient from "./AppWithQueryClient.svelte";
import { ensureDebug } from "./lib/debug";
import { mount } from "svelte";
import { queries } from "./lib/queryCatalogue";
import { queryClient } from "./lib/queryClient";

ensureDebug();
// Backend names and marks come from this list. Asking before mount keeps first paint steady.
void queryClient.prefetchQuery(queries.drivers());

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
  });
}

const app = mount(AppWithQueryClient, {
  target: document.getElementById("app") as HTMLElement
});

export default app;
