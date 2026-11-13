/** Fixed preload for the real CLI entry; no environment map or executable input. */
import http from "node:http";
import https from "node:https";
import { gunzipSync, inflateSync, brotliDecompressSync } from "node:zlib";
import { syncBuiltinESMExports } from "node:module";
import { appendFileSync, lstatSync } from "node:fs";
const destination = process.argv.splice(2, 1)[0];
if (!lstatSync(destination).isFile() || lstatSync(destination).isSymbolicLink())
  throw new Error("wire destination must be a private driver file");
const origin = new URL(process.env.PLAN_SERVER_URL).origin;
for (const module of [http, https]) {
  const original = module.request;
  module.request = function (...args) {
    if (!(args[0] instanceof URL) || args[0].origin !== origin)
      throw new Error(
        "wire request escaped its owned host or used an unknown overload",
      );
    const request = Reflect.apply(original, this, args);
    request.prependListener("response", (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
      const record = (complete) => {
        const transport = Buffer.concat(chunks);
        let document = complete ? transport : Buffer.alloc(0);
        for (const encoding of String(
          response.headers["content-encoding"] ?? "",
        )
          .split(",")
          .reverse()) {
          if (complete && encoding.trim() === "gzip")
            document = gunzipSync(document);
          else if (complete && encoding.trim() === "deflate")
            document = inflateSync(document);
          else if (complete && encoding.trim() === "br")
            document = brotliDecompressSync(document);
        }
        appendFileSync(
          destination,
          JSON.stringify({
            method: request.method,
            path: request.path,
            status: response.statusCode,
            encoding: response.headers["content-encoding"] ?? "",
            body_base64: document.toString("base64"),
            transport_body_base64: transport.toString("base64"),
            complete,
          }) + "\n",
        );
      };
      response.once("end", () => record(response.complete));
      response.once("aborted", () => record(false));
    });
    return request;
  };
}
syncBuiltinESMExports();
