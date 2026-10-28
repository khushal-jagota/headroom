import type { VideoUploadAdapter, VideoUploadStatus } from "./contracts";
const ROOT = "/api/conversation/video-uploads";
async function responseBody(response: Response): Promise<VideoUploadStatus> {
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Video upload failed.");
  return body as VideoUploadStatus;
}
export const videoUploadAdapter: VideoUploadAdapter = {
  reserve: (destination, file, signal) => fetch(ROOT, {
    method: "POST", headers: {"Content-Type": "application/json"}, signal,
    body: JSON.stringify({destination, filename: file.name, byte_count: file.size})
  }).then(responseBody),
  transfer: (uploadId, file, signal, progress) => new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const abort = () => request.abort();
    request.open("PUT", `${ROOT}/${encodeURIComponent(uploadId)}/bytes`);
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.upload.onprogress = event => progress(event.loaded);
    request.upload.onload = () => progress(file.size);
    request.onload = () => {
      signal.removeEventListener("abort", abort);
      try {
        const body = JSON.parse(request.responseText);
        if (request.status >= 200 && request.status < 300) resolve(body as VideoUploadStatus);
        else reject(new Error(typeof body.detail === "string" ? body.detail : "Video upload failed."));
      } catch { reject(new Error("The video service did not return a valid response.")); }
    };
    request.onerror = () => { signal.removeEventListener("abort", abort); reject(new Error("The video upload was interrupted. Retry to upload it again.")); };
    request.onabort = () => { signal.removeEventListener("abort", abort); reject(new Error("The video upload was interrupted.")); };
    signal.addEventListener("abort", abort, {once: true});
    if (signal.aborted) abort();
    else request.send(file);
  }),
  status: uploadId => fetch(`${ROOT}/${encodeURIComponent(uploadId)}`).then(responseBody),
  cancel: async uploadId => { await fetch(`${ROOT}/${encodeURIComponent(uploadId)}`, {method: "DELETE"}); }
};
