import "@fontsource/newsreader/400.css";
import "@fontsource/newsreader/500.css";
import "@fontsource/newsreader/600.css";
import App from "./App.svelte";
import { ensureDebug } from "./lib/debug";
import { mount } from "svelte";

ensureDebug();

const app = mount(App, {
  target: document.getElementById("app") as HTMLElement
});

export default app;
