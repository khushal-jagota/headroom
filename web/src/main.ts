import { mount } from "svelte";

// Setup shares the screens build but never starts server queries or restores app data.
if (location.protocol === "panels:" && location.host === "setup") {
  void import("./desktop/DesktopSetup.svelte").then(({ default: Setup }) => {
    mount(Setup, { target: document.getElementById("app")! });
  });
} else {
  void import("./startApp");
}
