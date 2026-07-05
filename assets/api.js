/* Data layer: JSON fetch wrapper (structured-error passthrough), the WS client,
 * and the invalidation bus (SPEC §9). No client-side state store — the bus holds
 * only a cursor, a debounce timer, a backoff delay, and one flush callback; every
 * flush makes the active screen refetch. WS batches are an invalidation signal:
 * onmessage reads only msg.cursor, never the events array.
 * Classic script: attaches Planner.api and Planner.bus; defines window.__plannerDebug. */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});
  var config = Planner.config;

  // The sanctioned console hook: counters only, one console.debug per flush / open.
  window.__plannerDebug = { flushes: 0, wsOpens: 0, cursor: 0 };

  function makeError(code, message, extra) {
    var err = new Error(message);
    err.code = code;
    if (extra) {
      Object.keys(extra).forEach(function (key) {
        err[key] = extra[key];
      });
    }
    return err;
  }

  function makePlannerError(envelope, status) {
    var err = new Error(envelope.message);
    err.code = envelope.code;
    err.detail = envelope.detail;
    err.status = status;
    err.isPlannerError = true;
    return err;
  }

  // --- fetchJson ---------------------------------------------------------------

  function fetchJson(path, options) {
    options = options || {};
    var init = { method: options.method || "GET", headers: {} };
    if (options.body !== undefined && options.body !== null) {
      init.body = JSON.stringify(options.body);
      init.headers["Content-Type"] = "application/json";
    }
    return fetch(path, init).then(
      function (response) {
        return response.text().then(function (raw) {
          return { response: response, raw: raw };
        });
      },
      function () {
        return Promise.reject(makeError("network", "network error"));
      }
    ).then(function (pair) {
      var response = pair.response;
      var raw = pair.raw;
      if (response.ok) {
        try {
          return raw === "" ? {} : JSON.parse(raw);
        } catch (parseErr) {
          return Promise.reject(
            makeError("bad_json", "invalid JSON in response", { status: response.status })
          );
        }
      }
      var parsed = null;
      try {
        parsed = JSON.parse(raw);
      } catch (parseErr) {
        parsed = null;
      }
      if (parsed && parsed.error && parsed.error.code) {
        return Promise.reject(makePlannerError(parsed.error, response.status));
      }
      return Promise.reject(
        makeError("http_error", "HTTP " + response.status, { status: response.status })
      );
    });
  }

  Planner.api = { fetchJson: fetchJson };

  // --- the invalidation bus ----------------------------------------------------

  var cursor = 0;
  var socket = null;
  var debounceMs = config.DEBOUNCE_MS_DEFAULT;
  var flushTimer = null;
  var retryMs = config.WS_RETRY_MIN_MS;
  var onFlush = null;
  var started = false;

  function flush() {
    flushTimer = null;
    window.__plannerDebug.flushes += 1;
    console.debug("[planner] flush " + window.__plannerDebug.flushes);
    if (onFlush) {
      onFlush();
    }
  }

  function schedule() {
    clearTimeout(flushTimer);
    flushTimer = setTimeout(flush, debounceMs);
  }

  function connect() {
    var scheme = location.protocol === "https:" ? "wss://" : "ws://";
    var url = scheme + location.host + config.API.EVENTS_WS + "?since=" + cursor;
    socket = new WebSocket(url);
    socket.onopen = function () {
      retryMs = config.WS_RETRY_MIN_MS;
      window.__plannerDebug.wsOpens += 1;
      console.debug("[planner] ws open since=" + cursor);
    };
    socket.onmessage = function (event) {
      var msg;
      try {
        msg = JSON.parse(event.data);
      } catch (parseErr) {
        console.debug("[planner] ws bad json");
        return;
      }
      if (typeof msg.cursor === "number") {
        cursor = msg.cursor;
        window.__plannerDebug.cursor = cursor;
      }
      schedule();
    };
    socket.onclose = function () {
      socket = null;
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * config.WS_RETRY_FACTOR, config.WS_RETRY_MAX_MS);
    };
  }

  Planner.bus = {
    onInvalidate: function (fn) {
      onFlush = fn;
    },
    start: function (ms) {
      if (started) {
        return;
      }
      started = true;
      debounceMs = ms;
      connect();
    }
  };
})();
