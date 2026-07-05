/* Minimal safe markdown renderer. The ONLY innerHTML assignment in the whole
 * codebase lives here — and it receives a string whose every character of user
 * origin was HTML-escaped BEFORE any parsing, so no un-entified "<", ">", or quote
 * can exist. Every tag is renderer-authored from a closed set; the only attribute
 * emitted is a scheme-whitelisted href. Classic script: attaches Planner.markdown. */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});

  // Code-span placeholder sentinel (NUL). Stripped from raw input first, so a
  // user string can never forge a placeholder.
  var NUL = String.fromCharCode(0);
  var RESTORE_RE = new RegExp(NUL + "(\\d+)" + NUL, "g");

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  var SCHEME_RE = /^[a-z][a-z0-9+.-]*:/;

  function safeHref(url) {
    // Validate a normalized copy: browsers strip leading C0 controls/spaces
    // (and interior tab/newline) when parsing URLs, so "\u0001javascript:..."
    // would read as scheme-less here yet execute after normalization. Our strip
    // is a superset of the browser's, so anything passing this check cannot
    // normalize to a non-whitelisted scheme.
    var norm = url.replace(/[\u0000-\u0020]+/g, "").toLowerCase();
    if (SCHEME_RE.test(norm)) {
      return (
        norm.indexOf("http:") === 0 ||
        norm.indexOf("https:") === 0 ||
        norm.indexOf("mailto:") === 0
      );
    }
    return true; // scheme-less (relative, "#/ticket/...", "?", "/") is allowed
  }

  function renderInline(line) {
    var codes = [];
    // 1. Code spans first — protected from all further processing.
    line = line.replace(/`([^`]+)`/g, function (match, inner) {
      var index = codes.length;
      codes.push(inner);
      return NUL + index + NUL;
    });
    // 2. Links — href is scheme-whitelisted; the value is already escaped.
    line = line.replace(/\[([^\]]+)\]\(([^()\s]+)\)/g, function (match, text, url) {
      if (safeHref(url)) {
        return '<a href="' + url + '">' + text + "</a>";
      }
      return match;
    });
    // 3. Bold before italic so "**" is not eaten by "*".
    line = line.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // 4. Italic.
    line = line.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    line = line.replace(/_([^_]+)_/g, "<em>$1</em>");
    // 5. Restore code spans.
    line = line.replace(RESTORE_RE, function (match, index) {
      return "<code>" + codes[Number(index)] + "</code>";
    });
    return line;
  }

  function render(text) {
    // 1. Strip the placeholder sentinel so collisions are impossible.
    text = String(text).split(NUL).join("");
    // 2. Escape everything once, before any parsing.
    var escaped = escapeHtml(text);
    var lines = escaped.split("\n");
    var out = [];
    var para = null;
    var listType = null;
    var listItems = null;
    var i = 0;

    function closePara() {
      if (para) {
        out.push("<p>" + para.join("<br>") + "</p>");
        para = null;
      }
    }
    function closeList() {
      if (listItems) {
        out.push("<" + listType + ">" + listItems.join("") + "</" + listType + ">");
        listItems = null;
        listType = null;
      }
    }
    function closeBlocks() {
      closePara();
      closeList();
    }

    while (i < lines.length) {
      var line = lines[i];
      // Fenced code block — verbatim, no inline processing (already escaped).
      if (/^```/.test(line)) {
        closeBlocks();
        i += 1;
        var codeLines = [];
        while (i < lines.length && !/^```/.test(lines[i])) {
          codeLines.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) {
          i += 1; // consume the closing fence
        }
        out.push("<pre><code>" + codeLines.join("\n") + "</code></pre>");
        continue;
      }
      // Blank line closes the open block.
      if (/^\s*$/.test(line)) {
        closeBlocks();
        i += 1;
        continue;
      }
      // Heading — caps at h3 (four or more "#" render as h3).
      var heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        closeBlocks();
        var level = heading[1].length;
        if (level > 3) {
          level = 3;
        }
        out.push("<h" + level + ">" + renderInline(heading[2]) + "</h" + level + ">");
        i += 1;
        continue;
      }
      // Unordered list item.
      var unordered = /^[-*+]\s+(.*)$/.exec(line);
      if (unordered) {
        closePara();
        if (listType && listType !== "ul") {
          closeList();
        }
        if (!listItems) {
          listType = "ul";
          listItems = [];
        }
        listItems.push("<li>" + renderInline(unordered[1]) + "</li>");
        i += 1;
        continue;
      }
      // Ordered list item.
      var ordered = /^\d{1,9}[.)]\s+(.*)$/.exec(line);
      if (ordered) {
        closePara();
        if (listType && listType !== "ol") {
          closeList();
        }
        if (!listItems) {
          listType = "ol";
          listItems = [];
        }
        listItems.push("<li>" + renderInline(ordered[1]) + "</li>");
        i += 1;
        continue;
      }
      // Paragraph line — consecutive lines join with <br>.
      closeList();
      if (!para) {
        para = [];
      }
      para.push(renderInline(line));
      i += 1;
    }
    closeBlocks();

    var div = document.createElement("div");
    div.className = "markdown";
    div.innerHTML = out.join("");
    return div;
  }

  Planner.markdown = { render: render };
})();
