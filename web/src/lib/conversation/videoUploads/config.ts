export const MAX_VIDEO_BYTES = 250 * 1024 * 1024;
export const VIDEO_ACCEPT = ".mp4,.mov,.webm";
export const isVideoFile = (name: string): boolean => /\.(mp4|mov|webm)$/i.test(name);
