"use strict";

const FALLBACK_ROUTE = "/#/workspace";
const ICON = "/static/icon-192.png";

function notificationPayload(event) {
  try {
    const value = event.data?.json();
    if (
      value &&
      typeof value.title === "string" &&
      typeof value.body === "string" &&
      typeof value.route === "string" &&
      value.route.startsWith("/#/ticket/") &&
      typeof value.tag === "string"
    ) {
      return value;
    }
  } catch {
    // A malformed remote payload receives a generic, safe notification.
  }
  return {
    title: "Panels",
    body: "Panels has an update.",
    route: FALLBACK_ROUTE,
    tag: "panels-update"
  };
}

self.addEventListener("push", (event) => {
  const payload = notificationPayload(event);
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: ICON,
      badge: ICON,
      tag: payload.tag,
      renotify: false,
      data: { route: payload.route }
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const route =
    typeof event.notification.data?.route === "string"
      ? event.notification.data.route
      : FALLBACK_ROUTE;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const existing = windows.find((client) => new URL(client.url).origin === self.location.origin);
      if (existing) {
        return existing.navigate(route).then(() => existing.focus());
      }
      return self.clients.openWindow(route);
    })
  );
});
