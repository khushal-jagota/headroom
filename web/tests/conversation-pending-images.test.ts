import { describe, expect, it, vi } from "vitest";

import {
  createPendingConversationImages,
  pendingImagesAsPieces,
  releasePendingImages,
  restoredPendingImages
} from "../src/lib/conversation/pendingImages";

const THREE_MIB_IN_BYTES = 3 * 1024 * 1024;

describe("pending Conversation images", () => {
  it("keeps selection order, rejects non-images, and encodes exact message pieces", async () => {
    const created: string[] = [];
    const imageA = new File([new Uint8Array([1, 2, 3])], "a.png", { type: "image/png" });
    const note = new File(["not a picture"], "note.txt", { type: "text/plain" });
    const imageB = new File([new Uint8Array([4, 5])], "b.webp", { type: "image/webp" });

    const intake = await createPendingConversationImages(
      [imageA, note, imageB],
      7,
      0,
      (file) => {
        const url = `blob:${file.name}`;
        created.push(url);
        return url;
      }
    );

    expect(intake.accepted.map((image) => [image.id, image.fileName, image.previewUrl])).toEqual([
      [7, "a.png", "blob:a.png"],
      [8, "b.webp", "blob:b.webp"]
    ]);
    expect(intake.rejected.map((file) => file.name)).toEqual(["note.txt"]);
    expect(intake.nextId).toBe(9);
    expect(created).toEqual(["blob:a.png", "blob:b.webp"]);
    expect(pendingImagesAsPieces(intake.accepted)).toEqual([
      { piece: "image", data: "AQID", media_type: "image/png", file_name: "a.png" },
      { piece: "image", data: "BAU=", media_type: "image/webp", file_name: "b.webp" }
    ]);
  });

  it("revokes owned blob previews and cleans up a partially-created batch", async () => {
    const imageA = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
    const imageB = new File([new Uint8Array([2])], "b.png", { type: "image/png" });
    const revoked: string[] = [];

    const intake = await createPendingConversationImages(
      [imageA],
      1,
      0,
      () => "blob:a"
    );
    releasePendingImages(intake.accepted, (url) => revoked.push(url));

    await expect(
      createPendingConversationImages(
        [imageA, imageB],
        10,
        0,
        (file) => {
          if (file === imageB) throw new Error("no more preview resources");
          return "blob:partial-a";
        },
        (url) => revoked.push(url)
      )
    ).rejects.toThrow("no more preview resources");

    expect(revoked).toEqual(["blob:a", "blob:partial-a"]);
  });

  it("rejects empty, unsupported, and per-image oversize files before reading or previewing", async () => {
    const rejectedFiles = [
      new File([], "empty.png", { type: "image/png" }),
      new File(["text"], "note.txt", { type: "text/plain" }),
      new File(["<svg/>"], "vector.svg", { type: "image/svg+xml" }),
      new File(["heic"], "photo.heic", { type: "image/heic" }),
      new File([new Uint8Array(THREE_MIB_IN_BYTES + 1)], "huge.png", { type: "image/png" })
    ];
    const read = vi.fn(async () => {
      throw new Error("a rejected file must not be read");
    });
    for (const file of rejectedFiles) file.arrayBuffer = read;
    const createPreview = vi.fn(() => {
      throw new Error("a rejected file must not get a preview");
    });

    const intake = await createPendingConversationImages(rejectedFiles, 40, 0, createPreview);

    expect(intake.rejected.map((file) => file.name)).toEqual([
      "empty.png",
      "note.txt",
      "vector.svg",
      "photo.heic",
      "huge.png"
    ]);
    expect(intake.accepted).toEqual([]);
    expect(intake.nextId).toBe(40);
    expect(read).not.toHaveBeenCalled();
    expect(createPreview).not.toHaveBeenCalled();
  });

  it("applies aggregate and already-pending byte bounds before reading rejected bytes", async () => {
    const firstHalf = new File(
      [new Uint8Array(THREE_MIB_IN_BYTES / 2)],
      "first-half.png",
      { type: "image/png" }
    );
    const aggregateOverflow = new File(
      [new Uint8Array(THREE_MIB_IN_BYTES / 2 + 1)],
      "aggregate-overflow.png",
      { type: "image/png" }
    );
    const aggregateRead = vi.fn(async () => {
      throw new Error("aggregate overflow must not be read");
    });
    aggregateOverflow.arrayBuffer = aggregateRead;

    const aggregate = await createPendingConversationImages(
      [firstHalf, aggregateOverflow],
      60,
      0,
      () => "blob:aggregate"
    );

    expect(aggregate.accepted.map((image) => image.fileName)).toEqual(["first-half.png"]);
    expect(aggregate.rejected).toEqual([aggregateOverflow]);
    expect(aggregateRead).not.toHaveBeenCalled();

    const pendingOverflow = new File([new Uint8Array([1, 2])], "later.png", {
      type: "image/png"
    });
    const pendingRead = vi.fn(async () => new ArrayBuffer(0));
    pendingOverflow.arrayBuffer = pendingRead;

    const alreadyPending = await createPendingConversationImages(
      [pendingOverflow],
      70,
      THREE_MIB_IN_BYTES - 1
    );

    expect(alreadyPending.accepted).toEqual([]);
    expect(alreadyPending.rejected).toEqual([pendingOverflow]);
    expect(alreadyPending.nextId).toBe(70);
    expect(pendingRead).not.toHaveBeenCalled();
  });
});
