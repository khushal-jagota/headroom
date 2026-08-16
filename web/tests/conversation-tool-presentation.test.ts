import { describe, expect, it } from "vitest";

import { presentToolCall } from "../src/lib/conversation/toolCallPresentation";
import type { ToolCallRow } from "../src/lib/conversation/transcript";

const EXECUTE_ICON_PATHS = [
  "m5 7 5 5-5 5",
  "M13 17h6"
] as const;
const READ_ICON_PATHS = [
  "M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z",
  "M14 3v5h5"
] as const;
const UNKNOWN_ICON_PATHS = [
  "M6 12h.01",
  "M12 12h.01",
  "M18 12h.01"
] as const;
const PROTOCOL_ICON_PATHS = {
  read: READ_ICON_PATHS,
  edit: ["M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"],
  delete: [
    "M4 7h16",
    "M9 7V4h6v3",
    "M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13",
    "M10 11v6",
    "M14 11v6"
  ],
  move: ["M9 7h8v8", "M7 17 17 7"],
  search: [
    "M11 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14",
    "m21 21-4.3-4.3"
  ],
  execute: EXECUTE_ICON_PATHS,
  think: [
    "M12 3a5 5 0 0 0-3 9c.6.5 1 1.2 1 2v1h4v-1c0-.8.4-1.5 1-2a5 5 0 0 0-3-9z",
    "M10 20h4"
  ],
  fetch: ["M12 3v12", "m7 10 5 5 5-5", "M5 21h14"],
  switch_mode: [
    "M4 9h13",
    "m14 6 3 3-3 3",
    "M20 15H7",
    "m10 12-3 3 3 3"
  ],
  other: UNKNOWN_ICON_PATHS
} as const;

function toolRow(overrides: Partial<ToolCallRow> = {}): ToolCallRow {
  return {
    key: "e1",
    kind: "tool_call",
    sequence: 1,
    createdAt: 1_700_000_000,
    toolCallId: "t1",
    title: "Read",
    toolKind: "read",
    detail: null,
    startedDetail: null,
    cappedDetailSequence: null,
    status: "completed",
    progress: null,
    ...overrides
  };
}

describe("Conversation tool-call presentation", () => {
  it.each(Object.entries(PROTOCOL_ICON_PATHS))(
    "retains the protocol-native %s icon paths",
    (toolKind, iconPaths) => {
      expect(presentToolCall(toolRow({ toolKind })).iconPaths).toEqual(iconPaths);
    }
  );

  it.each([
    ["WebFetch", PROTOCOL_ICON_PATHS.fetch],
    ["NotebookEdit", PROTOCOL_ICON_PATHS.edit]
  ] as const)("normalizes the backend tool name %s", (toolKind, iconPaths) => {
    expect(presentToolCall(toolRow({ toolKind })).iconPaths).toEqual(iconPaths);
  });

  it("presents a Claude command as one complete renderer result", () => {
    expect(
      presentToolCall(
        toolRow({
          title: "Bash",
          toolKind: "Bash",
          startedDetail: JSON.stringify({
            command: "ls -la /workspace",
            description: "List workspace"
          }),
          detail: "total 0"
        })
      )
    ).toEqual({
      iconPaths: EXECUTE_ICON_PATHS,
      title: "Ran command",
      summary: "ls -la /workspace",
      detail: "total 0",
      canExpand: true
    });
  });

  it("reads only allowed Claude file subjects", () => {
    expect(
      presentToolCall(
        toolRow({
          title: "Read",
          toolKind: "Read",
          startedDetail: '{"file_path":"/workspace/AGENTS.md"}'
        })
      )
    ).toEqual({
      iconPaths: READ_ICON_PATHS,
      title: "Read file",
      summary: "/workspace/AGENTS.md",
      detail: null,
      canExpand: false
    });
    expect(
      presentToolCall(
        toolRow({
          title: "Write",
          toolKind: "Write",
          startedDetail: '{"content":"secret payload","file_path":"/workspace/note.md"}'
        })
      )
    ).toMatchObject({
      title: "Edited file",
      summary: "/workspace/note.md"
    });
  });

  it("does not invent a subject or icon for an unknown tool", () => {
    expect(
      presentToolCall(
        toolRow({
          title: "AskUserQuestion",
          toolKind: "AskUserQuestion",
          startedDetail: '{"questions":[{"question":"Which colour?"}]}'
        })
      )
    ).toEqual({
      iconPaths: UNKNOWN_ICON_PATHS,
      title: "AskUserQuestion",
      summary: null,
      detail: null,
      canExpand: false
    });
  });

  it.each([
    ['bash -c "npm test"', "npm test"],
    ["sh -c 'npm test'", "npm test"],
    ["/bin/zsh -lc 'npm test'", "npm test"],
    ["cmd /c dir", "dir"],
    ['pwsh -Command "Get-ChildItem"', "Get-ChildItem"],
    ['bash -c "echo \\"hi\\""', 'echo "hi"'],
    ["npm test", "npm test"],
    ["bashful -c thing", "bashful -c thing"]
  ])("unwraps command %s as %s", (command, expected) => {
    expect(
      presentToolCall(
        toolRow({
          title: command,
          toolKind: "execute"
        })
      )
    ).toMatchObject({
      title: "Ran command",
      summary: expected
    });
  });

  it("uses Hermes descriptive titles without repeating their kind", () => {
    expect(
      presentToolCall(
        toolRow({
          title: "terminal: ls web/src/lib/conversation",
          toolKind: "execute",
          startedDetail: "$ ls web/src/lib/conversation",
          detail: "composer.ts\nfeed.ts"
        })
      )
    ).toMatchObject({
      title: "Ran command",
      summary: "ls web/src/lib/conversation"
    });
    expect(
      presentToolCall(
        toolRow({
          title: "search: wire.ts",
          toolKind: "search",
          startedDetail: "Searching for 'wire.ts' (files) in /workspace"
        })
      )
    ).toMatchObject({
      title: "Searched files",
      summary: "wire.ts"
    });
  });

  it("keeps summaries on one line and visibly truncates an overlong subject", () => {
    expect(
      presentToolCall(
        toolRow({
          title: "Bash",
          toolKind: "Bash",
          startedDetail: JSON.stringify({ command: "x".repeat(100) })
        })
      ).summary
    ).toBe(`${"x".repeat(79)}…`);
    expect(
      presentToolCall(
        toolRow({
          title: "Bash",
          toolKind: "Bash",
          startedDetail: JSON.stringify({
            command: "cat <<'EOF' > note.txt\nhello\nEOF"
          })
        })
      ).summary
    ).toBe("cat <<'EOF' > note.txt");
  });

  it("keeps a summary when its letters merely occur inside the title", () => {
    expect(
      presentToolCall(
        toolRow({
          title: "Grep",
          toolKind: "Grep",
          startedDetail: '{"pattern":"arch"}'
        })
      )
    ).toMatchObject({ title: "Searched files", summary: "arch" });
    expect(
      presentToolCall(
        toolRow({
          title: "Grep",
          toolKind: "Grep",
          startedDetail: '{"pattern":"search"}'
        })
      )
    ).toMatchObject({ title: "Searched files", summary: "search" });
  });

  it("opens only when the selected detail adds something to the visible line", () => {
    expect(
      presentToolCall(
        toolRow({
          title: "Bash",
          toolKind: "Bash",
          detail: "ls -la /tmp"
        })
      )
    ).toMatchObject({
      summary: "ls -la /tmp",
      detail: "ls -la /tmp",
      canExpand: false
    });
    expect(
      presentToolCall(
        toolRow({
          title: "Bash",
          toolKind: "Bash",
          detail: "ls -la /tmp\nfile one"
        })
      )
    ).toMatchObject({
      title: "Bash",
      summary: null,
      detail: "ls -la /tmp\nfile one",
      canExpand: true
    });
  });

  it("uses progress before recorded detail by nullish precedence", () => {
    expect(
      presentToolCall(
        toolRow({
          title: "Read",
          toolKind: "read",
          detail: "finished output",
          progress: "reading line 20"
        })
      )
    ).toMatchObject({
      detail: "reading line 20",
      canExpand: true
    });
    expect(
      presentToolCall(
        toolRow({
          title: "Read",
          toolKind: "read",
          detail: "finished output",
          progress: ""
        })
      )
    ).toMatchObject({
      detail: null,
      canExpand: false
    });
  });

  it("opens a capped row onto output the reader has not seen", () => {
    // The start of a long output can look like anything, including a repeat of the line
    // itself. The row opens because there is more of it, not because the fragment reads
    // like more of it.
    expect(
      presentToolCall(
        toolRow({
          title: "Bash",
          toolKind: "execute",
          startedDetail: JSON.stringify({ command: "ls -la" }),
          detail: "ls -la",
          cappedDetailSequence: 7
        })
      )
    ).toMatchObject({ summary: "ls -la", canExpand: true });
  });

  it("draws the line of a capped row from what is whole", () => {
    // A cut object no longer parses and a cut first line was never a whole one, so the
    // fragment is not read for the line. What the call was asked to do still says which
    // call it was.
    const cut = JSON.stringify({ command: "gh run view 3037", extra: "x".repeat(40) }).slice(0, 30);
    expect(
      presentToolCall(
        toolRow({
          title: "Bash",
          toolKind: "execute",
          startedDetail: JSON.stringify({ command: "gh run view 3037" }),
          detail: cut,
          cappedDetailSequence: 4
        })
      )
    ).toMatchObject({ title: "Ran command", summary: "gh run view 3037" });
    expect(
      presentToolCall(
        toolRow({
          title: "TaskStop",
          toolKind: "other",
          startedDetail: null,
          detail: cut,
          cappedDetailSequence: 4
        })
      )
    ).toMatchObject({ title: "TaskStop", summary: null, canExpand: true });
  });
});
