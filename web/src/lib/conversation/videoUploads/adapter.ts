import { clientFetch, withUpdateWritePause } from "../../clientIdentity";
import { mutationHeaders } from "../../releaseMonitor";
import type { VideoUploadAdapter, VideoUploadStatus } from "./contracts";
const ROOT = "/api/conversation/video-uploads";
async function responseBody(response: Response): Promise<VideoUploadStatus> {
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "File upload failed.");
  return body as VideoUploadStatus;
}
export const videoUploadAdapter: VideoUploadAdapter = {
  reserve: async (destination, file, signal) => clientFetch(ROOT, {
    method: "POST", headers: await mutationHeaders({"Content-Type": "application/json"}), signal,
    body: JSON.stringify({destination, filename: file.name, byte_count: file.size})
  }).then(responseBody),
  transfer: async (uploadId, file, signal, progress) => {
    const headers = await mutationHeaders({ "Content-Type": "application/octet-stream" });
    const path = `${ROOT}/${encodeURIComponent(uploadId)}/bytes`;
    return withUpdateWritePause(path, { method: "PUT", headers, body: file, signal }, () => new Promise<Response>((resolve, reject) => {
      const request = new XMLHttpRequest();
      const abort = () => request.abort();
      request.open("PUT", path);
      for (const [name, value] of Object.entries(headers)) request.setRequestHeader(name, value);
      request.upload.onprogress = event => progress(event.loaded);
      request.upload.onload = () => progress(file.size);
      request.onload = () => {
        signal.removeEventListener("abort", abort);
        const proof = request.getResponseHeader("X-Panels-Write-Admitted");
        resolve(new Response(request.responseText, { status: request.status,
          headers: proof === null ? {} : { "X-Panels-Write-Admitted": proof } }));
      };
      request.onerror = () => { signal.removeEventListener("abort", abort); reject(new Error("The file upload was interrupted. Retry to upload it again.")); };
      request.onabort = () => { signal.removeEventListener("abort", abort); reject(new Error("The file upload was interrupted.")); };
      signal.addEventListener("abort", abort, {once: true});
      if (signal.aborted) abort();
      else request.send(file);
    })).then(responseBody);
  },
  status: uploadId => clientFetch(`${ROOT}/${encodeURIComponent(uploadId)}`).then(responseBody),
  cancel: async uploadId => { await clientFetch(`${ROOT}/${encodeURIComponent(uploadId)}`, {method: "DELETE", headers: await mutationHeaders()}); }
};
