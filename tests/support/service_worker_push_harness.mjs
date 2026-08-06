import { readFile } from "node:fs/promises";
import process from "node:process";
import vm from "node:vm";

const serviceWorkerPath = process.argv[2];
if (!serviceWorkerPath) {
  throw new Error("service worker path is required");
}

const input = [];
for await (const chunk of process.stdin) input.push(chunk);
const payloads = JSON.parse(input.join(""));
const handlers = new Map();
const notifications = [];
const self = {
  addEventListener(name, handler) {
    handlers.set(name, handler);
  },
  registration: {
    showNotification(title, options) {
      notifications.push({ title, options });
      return Promise.resolve();
    }
  },
  clients: {},
  location: { origin: "https://panels.example" }
};

vm.runInNewContext(await readFile(serviceWorkerPath, "utf8"), { self, URL });
const push = handlers.get("push");
if (!push) {
  throw new Error("service worker did not register a push handler");
}

for (const payload of payloads) {
  let pending;
  push({
    data: { json: () => payload },
    waitUntil(value) {
      pending = value;
    }
  });
  await pending;
}

process.stdout.write(JSON.stringify(notifications));
