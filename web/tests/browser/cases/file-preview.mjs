import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const hostPath = join(scratchRoot, `file-preview-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `file-preview-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `file-preview-index-${process.pid}.html`);

  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import FilePreview from "../src/components/FilePreview.svelte";

  const sharedNote = { kind: "ticket-file", ticketId: "t_probe01", path: "note.md" } as const;
</script>

<div data-probe="missing">
  <FilePreview
    target={{ kind: "external-link", href: "./missing-artifact.png", label: "Missing" }}
  />
</div>

<div data-probe="shared">
  <FilePreview target={sharedNote} />
  <FilePreview target={sharedNote} />
  <FilePreview target={sharedNote} />
</div>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    `import "../../assets/tokens.css";\nimport "../../assets/app.css";\nimport { mount } from "svelte";\nimport Host from "./${hostPath.split("/").at(-1)}";\nmount(Host, { target: document.getElementById("app")! });\n`,
    "utf8"
  );
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath
      .split("/")
      .at(-1)}"></script></body></html>`,
    "utf8"
  );


  return {resources: [{path: "/files/tickets/t_probe01/note.md", body: "# The shared note\n\nRead once, drawn three times.\n", contentType: "text/markdown"}], inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./file-preview.py", import.meta.url))};
}
