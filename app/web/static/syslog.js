// SYSLOG column on guild pages: the bot's real activity feed (polled from
// GET /guilds/{id}/events, written bot-side by EventLogService) merged with
// the console's own output (C3P0.print). Nothing here is fabricated - an
// idle server shows an idle log.
(function () {
    "use strict";

    var list = document.getElementById("syslog");
    if (!list) {
        return;
    }
    var C3P0 = (window.C3P0 = window.C3P0 || {});
    var url = list.dataset.eventsUrl;
    var MAX_LINES = 40;
    var POLL_MS = 4000;
    var TYPE_MS_PER_CHAR = 22;
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var lastId = 0;

    function pad(n) { return (n < 10 ? "0" : "") + n; }

    function render(line, animate) {
        var li = document.createElement("li");
        li.className = "log-line" + (line.user ? " is-user" : "");

        var time = document.createElement("span");
        time.className = "log-time";
        time.textContent = pad(line.time.getHours()) + ":" + pad(line.time.getMinutes()) + " ";
        li.appendChild(time);

        var tag = document.createElement("span");
        if (line.tag === "ERR") {
            tag.className = "log-err";
            tag.textContent = "ERR";
        } else {
            tag.className = "log-tag";
            tag.textContent = line.user ? ">" : "[" + line.tag + "]";
        }
        li.appendChild(tag);
        li.appendChild(document.createTextNode(" " + line.text));

        if (animate && !reduceMotion) {
            var len = line.text.length + line.tag.length + 8;
            li.classList.add("is-new");
            li.style.setProperty("--type-ms", len * TYPE_MS_PER_CHAR + "ms");
            li.style.setProperty("--type-steps", len);
        }
        return li;
    }

    // Oldest lines fade toward .45, newest at full brightness.
    function ramp() {
        var items = list.children;
        for (var i = 0; i < items.length; i++) {
            items[i].style.opacity = 0.45 + 0.55 * ((i + 1) / items.length);
        }
    }

    function push(line, animate) {
        var previous = list.querySelector(".is-new");
        if (previous) {
            previous.classList.remove("is-new");
        }
        list.appendChild(render(line, animate !== false));
        while (list.children.length > MAX_LINES) {
            list.removeChild(list.firstChild);
        }
        ramp();
    }

    C3P0.syslog = {
        push: function (line) { push(line, true); },
        clear: function () { list.innerHTML = ""; },
    };

    // Lines printed before this script loaded (queued by console.js).
    if (C3P0._drainQueued) {
        C3P0._drainQueued().forEach(function (line) { push(line, false); });
    }

    function fromEvent(event) {
        return { tag: event.tag, text: event.text, user: false, time: new Date(event.ts) };
    }

    function poll(initial) {
        if (!initial && document.hidden) {
            return;
        }
        fetch(url + "?after=" + lastId, { credentials: "same-origin", headers: { Accept: "application/json" } })
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (events) {
                events.forEach(function (event, i) {
                    lastId = Math.max(lastId, event.id);
                    // The backlog on page load appears at once; only the
                    // newest of it (and every live line after) types in.
                    push(fromEvent(event), !initial || i === events.length - 1);
                });
                if (initial && !events.length) {
                    push({ tag: "SYS", text: "attached · no recent activity", user: false, time: new Date() }, true);
                }
            })
            .catch(function () { /* bot or network blip - next tick retries */ });
    }

    poll(true);
    setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
            poll(false);
        }
    });
})();
