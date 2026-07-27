import "@fontsource/newsreader/400.css";
import "@fontsource/newsreader/500.css";
import "@fontsource/newsreader/600.css";
import AppWithQueryClient from "./AppWithQueryClient.svelte";
import { ensureDebug } from "./lib/debug";
import { mount } from "svelte";

ensureDebug();

const app = mount(AppWithQueryClient, {
  target: document.getElementById("app") as HTMLElement
});

export default app;
