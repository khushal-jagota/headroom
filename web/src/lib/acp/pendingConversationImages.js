/**
 * @typedef {{ id: number, file: File, url: string }} PendingConversationImage
 */

/**
 * @param {File} file
 * @returns {boolean}
 */
export function isImageFile(file) {
  return file.type.toLowerCase().startsWith("image/");
}

/**
 * @param {Iterable<File> | ArrayLike<File> | null | undefined} files
 * @param {number} nextId
 * @param {(file: File) => string} createObjectURL
 * @returns {{ accepted: PendingConversationImage[], rejected: File[], nextId: number }}
 */
export function createPendingConversationImages(files, nextId, createObjectURL) {
  const accepted = [];
  const rejected = [];
  for (const file of Array.from(files || [])) {
    if (!isImageFile(file)) {
      rejected.push(file);
      continue;
    }
    accepted.push({ id: nextId, file, url: createObjectURL(file) });
    nextId += 1;
  }
  return { accepted, rejected, nextId };
}

/**
 * @param {PendingConversationImage[]} images
 * @param {number} id
 * @param {(url: string) => void} revokeObjectURL
 * @returns {PendingConversationImage[]}
 */
export function removePendingConversationImage(images, id, revokeObjectURL) {
  return images.filter((image) => {
    if (image.id !== id) return true;
    revokeObjectURL(image.url);
    return false;
  });
}

/**
 * @param {PendingConversationImage[]} images
 * @returns {File[]}
 */
export function pendingConversationImageFiles(images) {
  return images.map((image) => image.file);
}

/**
 * @param {PendingConversationImage[]} images
 * @param {Iterable<number>} sentIds
 * @param {(url: string) => void} revokeObjectURL
 * @returns {PendingConversationImage[]}
 */
export function clearSentPendingConversationImages(images, sentIds, revokeObjectURL) {
  const sent = new Set(sentIds);
  return images.filter((image) => {
    if (!sent.has(image.id)) return true;
    revokeObjectURL(image.url);
    return false;
  });
}

/**
 * @param {PendingConversationImage[]} images
 * @param {(url: string) => void} revokeObjectURL
 */
export function revokePendingConversationImages(images, revokeObjectURL) {
  for (const image of images) revokeObjectURL(image.url);
}
