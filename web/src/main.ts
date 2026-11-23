import AppWithQueryClient from "./AppWithQueryClient.svelte";
import { ensureDebug } from "./lib/debug";
import { mount, tick } from "svelte";
import { queries } from "./lib/queryCatalogue";
import { queryClient } from "./lib/queryClient";
import { restoreSavedSession } from "./lib/savedSession";

ensureDebug();
// Backend names and marks come from this list. Asking before mount keeps first paint steady.
void queryClient.prefetchQuery(queries.drivers());

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
  });
}

// In the desktop app, the last session goes back into the cache before the first paint.
void restoreSavedSession(queryClient).finally(async () => {
  mount(AppWithQueryClient, { target: document.getElementById("app") as HTMLElement });
  await tick();
  document.documentElement.dataset.panelsReady = "true";
});
