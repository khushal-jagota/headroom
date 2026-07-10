import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../../assets/markdown.js", import.meta.url), "utf8");
const sandbox = {
  window: {},
  document: {
    createElement() {
      return { className: "", innerHTML: "" };
    }
  }
};
vm.runInNewContext(source, sandbox);

const rendered = sandbox.window.Planner.markdown.render(
  "[**Spec & Notes**](/files/tickets/t_tok123/notes/space%20name.md?raw=1#part)"
);

assert.match(
  rendered.innerHTML,
  /data-markdown-source-token="\[\*\*Spec &amp; Notes\*\*\]\(\/files\/tickets\/t_tok123\/notes\/space%20name\.md\?raw=1#part\)"/
);
assert.match(rendered.innerHTML, /<strong>Spec &amp; Notes<\/strong>/);

const unsupported = sandbox.window.Planner.markdown.render(
  '[Doc](/files/tickets/t_tok123/notes/file.md "Title")'
);
assert.equal(
  unsupported.innerHTML,
  '<p>[Doc](/files/tickets/t_tok123/notes/file.md &quot;Title&quot;)</p>'
);
