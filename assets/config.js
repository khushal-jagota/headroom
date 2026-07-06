/* Frontend constants. Anything tunable on the client lives here, not inline.
 * Classic script: attaches Planner.config. */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});
  Planner.config = {
    // Used only until GET /api/meta answers (SPEC §9: debounce 250ms from /api/meta).
    DEBOUNCE_MS_DEFAULT: 250,
    // WS reconnect backoff: 500 → 1000 → 2000 → 4000 → 8000 → 10000 (capped).
    WS_RETRY_MIN_MS: 500,
    WS_RETRY_MAX_MS: 10000,
    WS_RETRY_FACTOR: 2,
    // API paths the foundation itself calls.
    API: {
      META: "/api/meta",
      QUEUES: "/api/queues",
      EVENTS_WS: "/api/events"
    },
    // Hash routes (screen registry keys are the values' first segment).
    ROUTES: {
      day: "#/day",
      review: "#/review",
      board: "#/board",
      sprint: "#/sprint",
      backlog: "#/backlog",
      ideas: "#/ideas",
      ticketPrefix: "#/ticket/"
    }
  };
})();
