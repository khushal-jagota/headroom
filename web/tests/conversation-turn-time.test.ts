import { describe, expect, it } from "vitest";

import {
  emptyConversationFeed,
  feedWithCommittedEvents
} from "../src/lib/conversation/feed";
import {
  elapsedSecondsSince,
  formatDuration,
  millisecondsUntilNextSecond,
  stoppedSentence,
  threadItems,
  transcriptRows,
  turnFoldLabel,
  workedSentence,
  workingSentence
} from "../src/lib/conversation/transcript";
import type { ConversationEvent } from "../src/lib/conversation/wire";
import {
  promptEvent,
  turnEndedEvent
} from "./support/conversationEvents";

type TurnItem = Extract<
  ReturnType<typeof threadItems>[number],
  { kind: "turn" }
>;

function turnsFrom(events: readonly ConversationEvent[]): TurnItem[] {
  const feed = feedWithCommittedEvents(emptyConversationFeed(), events);
  return threadItems(transcriptRows(feed)).filter(
    (item): item is TurnItem => item.kind === "turn"
  );
}

function anchorFrom(
  sentAtUnixMilliseconds: number | null,
  createdAt = 1_700_000_000
): TurnItem {
  const payload =
    sentAtUnixMilliseconds === null
      ? {}
      : { sent_at_unix_milliseconds: sentAtUnixMilliseconds };
  const turns = turnsFrom([
    promptEvent(1, "go", "run_when_free", { createdAt, payload })
  ]);
  expect(turns).toHaveLength(1);
  return turns[0]!;
}

describe("Conversation turn time", () => {
  it.each([
    [0, "0s"],
    [59, "59s"],
    [60, "1m"],
    [61, "1m 1s"],
    [80, "1m 20s"],
    [120, "2m"],
    [3_600, "60m"]
  ])("formats %i seconds as %s", (seconds, sentence) => {
    expect(formatDuration(seconds)).toBe(sentence);
  });

  it("uses running, settled, and stopped wording for concrete turn states", () => {
    expect(workedSentence(0)).toBe("Worked");
    expect(workedSentence(null)).toBe("Worked");
    expect(workedSentence(1)).toBe("Worked for 1s");
    expect(workingSentence(12)).toBe("Working for 12s");
    expect(workingSentence(null)).toBe("Working");
    expect(stoppedSentence(12)).toBe("You stopped after 12s");
    expect(stoppedSentence(null)).toBe("You stopped this response");
    expect(
      turnFoldLabel({ durationSeconds: 12, ending: "interrupted", isLatest: true })
    ).toBe("You stopped after 12s");
    expect(
      turnFoldLabel({ durationSeconds: 12, ending: "interrupted", isLatest: false })
    ).toBe("Worked for 12s");
    expect(
      turnFoldLabel({ durationSeconds: 12, ending: "completed", isLatest: true })
    ).toBe("Worked for 12s");
  });

  it("measures elapsed boundaries relative to the turn start", () => {
    const begun = 1_000_400;

    expect(elapsedSecondsSince(begun, begun)).toBe(0);
    expect(elapsedSecondsSince(begun, begun + 999)).toBe(0);
    expect(elapsedSecondsSince(begun, begun + 1_000)).toBe(1);
    expect(elapsedSecondsSince(begun, 1_001_000)).toBe(0);
    expect(elapsedSecondsSince(begun, begun - 5_000)).toBe(0);
  });

  it("schedules ticks on the turn's seconds and recovers after delay", () => {
    const begun = 1_000_400;

    expect(millisecondsUntilNextSecond(begun, begun)).toBe(1_020);
    expect(millisecondsUntilNextSecond(begun, begun + 300)).toBe(720);
    expect(millisecondsUntilNextSecond(begun, begun + 12_300)).toBe(720);
    expect(millisecondsUntilNextSecond(begun, begun - 4_500)).toBe(520);

    let now = begun;
    const said: number[] = [];
    for (const lateness of [3, -4, 11, 0, -9, 7, 2, -1, 5]) {
      said.push(elapsedSecondsSince(begun, now));
      now += millisecondsUntilNextSecond(begun, now) + lateness;
    }
    expect(said).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8]);

    expect(elapsedSecondsSince(begun, begun + 65_000)).toBe(65);
    expect(millisecondsUntilNextSecond(begun, begun + 65_000)).toBe(1_020);
    expect(workingSentence(elapsedSecondsSince(begun, begun + 80_000))).toBe(
      "Working for 1m 20s"
    );
    expect(workedSentence(80)).toBe("Worked for 1m 20s");
  });

  it("uses a believable sender instant as the turn start", () => {
    expect(anchorFrom(1_700_000_000_400).startedAtUnixMilliseconds).toBe(
      1_700_000_000_400
    );
  });

  it("rejects seconds presented as milliseconds and implausible sender clocks", () => {
    const written = 1_700_000_000_000;

    expect(anchorFrom(1_700_000_000).startedAtUnixMilliseconds).toBe(written);
    expect(anchorFrom(written + 90_000).startedAtUnixMilliseconds).toBe(written);
    expect(anchorFrom(written - 600_000).startedAtUnixMilliseconds).toBe(written);
    expect(anchorFrom(null).startedAtUnixMilliseconds).toBe(written);
  });

  it("keeps ordinary skew and slow delivery as believable sender times", () => {
    const written = 1_700_000_000_000;

    expect(anchorFrom(written + 1_500).startedAtUnixMilliseconds).toBe(
      written + 1_500
    );
    expect(anchorFrom(written - 20_000).startedAtUnixMilliseconds).toBe(
      written - 20_000
    );
  });

  it("does not reset the turn start when a steer joins it", () => {
    const turns = turnsFrom([
      promptEvent(1, "go", "run_when_free", {
        createdAt: 1_700_000_000,
        payload: { sent_at_unix_milliseconds: 1_700_000_000_400 }
      }),
      promptEvent(2, "also this", "steer", {
        createdAt: 1_700_000_009,
        payload: { sent_at_unix_milliseconds: 1_700_000_009_100 }
      }),
      turnEndedEvent(3, {
        ending: "completed",
        createdAt: 1_700_000_012
      })
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]).toMatchObject({
      startedAtUnixMilliseconds: 1_700_000_000_400,
      durationSeconds: 12
    });
  });
});
