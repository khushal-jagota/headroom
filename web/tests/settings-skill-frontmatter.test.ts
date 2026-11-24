import { describe, expect, it } from "vitest";
import { splitSkillFrontmatter } from "../src/lib/settings/skillFrontmatter";

// Live skill bodies begin with one or two copies of their frontmatter. Settings must render
// none of it, and a save that writes the split back must not change what was hidden.
const copy = '---\nname: "panels-manager"\ndescription: "Supervise one Manager."\n---\n';

describe("splitSkillFrontmatter", () => {
  it("hides every leading copy, as the live Manager body holds two", () => {
    const stored = `\n${copy}\n${copy}\n# Manager\n\nYou supervise.\n`;
    const { frontmatter, body } = splitSkillFrontmatter(stored);
    expect(body).toBe("# Manager\n\nYou supervise.\n");
    expect(frontmatter + body).toBe(stored);
  });

  it("leaves a body without frontmatter as it is, past its blank opening lines", () => {
    const { frontmatter, body } = splitSkillFrontmatter("\n# Worker\n\n---\nname: later\n---\n");
    expect(frontmatter).toBe("\n");
    expect(body).toBe("# Worker\n\n---\nname: later\n---\n");
  });

  it("keeps an opening Markdown rule that is not frontmatter", () => {
    for (const stored of ["---\nSome prose.\n---\nMore.", "---\nname: unclosed\n# Heading", "---\n---\nText"]) {
      expect(splitSkillFrontmatter(stored)).toEqual({ frontmatter: "", body: stored });
    }
  });

  it("reads Windows line endings and a body that is only frontmatter", () => {
    expect(splitSkillFrontmatter("---\r\nname: x\r\n---\r\n\r\nBody").body).toBe("Body");
    expect(splitSkillFrontmatter(copy)).toEqual({ frontmatter: copy, body: "" });
  });
});
