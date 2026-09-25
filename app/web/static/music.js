// Music page: live now-playing / queue / volume (polled from the page's own
// /music/state JSON), a local one-second clock between polls, and the two
// phosphor visualisers.
//
// About the visualisers: the bot streams audio into Discord, not to this
// browser, so there is no real FFT data here and never will be. The
// oscilloscope and spectrum animate from `playing` and `volume` only -
// that's the intended design, not a placeholder. They flatten when paused
// and stop drawing while the tab is hidden.
//
// Everything is progressive: the server-rendered page is a correct
// snapshot with working forms; this only keeps it live.
(function () {
    "use strict";

    var panel = document.querySelector("[data-music-panel]");
    if (!panel) {
        return;
    }
    var C3P0 = (window.C3P0 = window.C3P0 || {});
    var stateUrl = panel.dataset.stateUrl;
    var guildId = document.body.dataset.guildId;
    var csrf = document.body.dataset.csrf || "";
    var POLL_INTERVAL_MS = 4000;
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var state = null;
    var elapsedBase = 0;
    var elapsedAt = 0;
    var queueSignature = null;

    function field(name) {
        return panel.querySelector('[data-field="' + name + '"]');
    }

    function setText(name, text) {
        var el = field(name);
        if (el) {
            el.textContent = text;
        }
    }

    function formatDuration(seconds) {
        if (seconds === null || seconds === undefined) {
            return "--:--";
        }
        var total = Math.max(0, Math.floor(seconds));
        var hours = Math.floor(total / 3600);
        var minutes = Math.floor((total % 3600) / 60);
        var secs = total % 60;
        var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
        return hours ? hours + ":" + pad(minutes) + ":" + pad(secs) : minutes + ":" + pad(secs);
    }

    // --- Now playing ---

    function currentElapsed() {
        if (!state || !state.current) {
            return 0;
        }
        if (!state.playing) {
            return elapsedBase;
        }
        return elapsedBase + (Date.now() - elapsedAt) / 1000;
    }

    function renderProgress() {
        if (!state || !state.current) {
            return;
        }
        var elapsed = currentElapsed();
        var duration = state.current.duration_seconds;
        if (duration) {
            elapsed = Math.min(elapsed, duration);
            field("progress").style.width = Math.max(0, Math.min(100, (100 * elapsed) / duration)) + "%";
        }
        setText("elapsed", formatDuration(elapsed));
    }

    function renderNowPlaying() {
        var active = state.playing || state.paused;
        setText("np-label", "[ " + (state.paused ? "PAUSED" : state.playing ? "NOW PLAYING" : "IDLE") + " ]");
        var vc = state.voice_channel_name ? " · VC: " + state.voice_channel_name.toUpperCase() : "";
        if (state.current) {
            setText("title", state.current.title);
            setText("meta", "REQUESTED BY @" + state.current.requested_by_name + vc);
            setText("duration", formatDuration(state.current.duration_seconds));
        } else {
            setText("title", "NO SIGNAL");
            setText("meta", state.connected ? "QUEUE EMPTY" + vc : 'NOT CONNECTED. RUN "play <song>" TO START THE QUEUE.');
            setText("duration", "--:--");
            setText("elapsed", "0:00");
            field("progress").style.width = "0%";
        }

        var pauseForm = panel.querySelector('[data-toggle="pause"]');
        var resumeForm = panel.querySelector('[data-toggle="resume"]');
        if (pauseForm && resumeForm) {
            pauseForm.hidden = !state.playing;
            resumeForm.hidden = state.playing;
            resumeForm.querySelector("button").disabled = !state.paused;
        }
        ["restart", "skip", "stop"].forEach(function (action) {
            var button = panel.querySelector('[data-action="' + action + '"]');
            if (button) {
                button.disabled = !active;
            }
        });
    }

    function renderVolume() {
        setText("volume", state.volume_percent + "%");
        field("volume-bar").style.width = state.volume_percent + "%";
        panel.querySelectorAll("[data-vol-step]").forEach(function (input) {
            var next = state.volume_percent + parseInt(input.dataset.volStep, 10);
            input.value = Math.max(0, Math.min(100, next));
        });
    }

    // --- Queue (rebuilt only when it actually changed) ---

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) {
            node.className = className;
        }
        if (text !== undefined) {
            node.textContent = text;
        }
        return node;
    }

    function renderQueue() {
        var signature = JSON.stringify(state.queue.map(function (t) { return [t.title, t.requested_by]; }));
        var count = state.queue.length;
        setText("queue-label", "[ QUEUE · " + count + " TRACK" + (count === 1 ? "" : "S") + " ]");
        if (signature === queueSignature) {
            return;
        }
        queueSignature = signature;
        var list = panel.querySelector("[data-queue-list]");
        list.textContent = "";
        if (!count) {
            list.appendChild(el("p", "empty-state", 'NO ENTRIES. RUN "play <song>" TO START THE QUEUE.'));
            return;
        }
        state.queue.forEach(function (track, i) {
            var row = el("div", "queue-row");
            row.appendChild(el("span", "n", (i < 9 ? "0" : "") + (i + 1)));
            var title = el("span", "t", track.title + " ");
            title.appendChild(el("span", "by", "— @" + track.requested_by_name));
            row.appendChild(title);
            row.appendChild(el("span", "dur", formatDuration(track.duration_seconds)));
            var form = el("form");
            form.method = "post";
            form.action = "/guilds/" + guildId + "/music/queue/" + i + "/remove";
            form.dataset.ok = "removed from queue";
            var token = el("input");
            token.type = "hidden";
            token.name = "csrf_token";
            token.value = csrf;
            form.appendChild(token);
            var button = el("button", "ghost-btn danger", "[DEL]");
            button.type = "submit";
            button.setAttribute("aria-label", "Remove " + track.title);
            form.appendChild(button);
            row.appendChild(form);
            list.appendChild(row);
        });
    }

    function apply(next) {
        state = next;
        elapsedBase = next.elapsed_seconds || 0;
        elapsedAt = Date.now();
        renderNowPlaying();
        renderProgress();
        renderVolume();
        renderQueue();
    }

    function poll(force) {
        if (document.hidden && force !== true) {
            return Promise.resolve();
        }
        return fetch(stateUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (next) {
                if (next) {
                    apply(next);
                }
            })
            .catch(function () { /* bot mid-restart - next tick retries */ });
    }

    C3P0.music = { refresh: function () { return poll(true); } };
    poll(true);
    setInterval(poll, POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
            poll();
        }
    });
    setInterval(renderProgress, 1000);

    // --- Visualisers ---

    var scope = document.getElementById("scope");
    var spectrum = document.getElementById("spectrum");
    var BARS = 28;
    var bars = new Array(BARS).fill(0);
    var peaks = new Array(BARS).fill(0);

    function phosphor() {
        return getComputedStyle(document.documentElement).getPropertyValue("--p").trim() || "#ffa31f";
    }

    // Canvases render at 2x device pixels for crisp phosphor lines.
    function fit(canvas) {
        var w = Math.round(canvas.clientWidth * 2);
        var h = Math.round(canvas.clientHeight * 2);
        if (canvas.width !== w || canvas.height !== h) {
            canvas.width = w;
            canvas.height = h;
        }
        return [w, h];
    }

    function level() {
        return state && state.playing ? state.volume_percent / 100 : 0;
    }

    function drawScope(t, color) {
        var ctx = scope.getContext("2d");
        var size = fit(scope);
        var w = size[0];
        var h = size[1];
        // Translucent fill instead of a clear: the previous frames fade
        // out slowly, like phosphor persistence.
        ctx.shadowBlur = 0;
        ctx.globalAlpha = 1;
        ctx.fillStyle = "rgba(13,8,5,0.28)";
        ctx.fillRect(0, 0, w, h);
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.07;
        ctx.lineWidth = 1;
        for (var i = 1; i < 10; i++) {
            ctx.beginPath(); ctx.moveTo((w * i) / 10, 0); ctx.lineTo((w * i) / 10, h); ctx.stroke();
        }
        for (var j = 1; j < 6; j++) {
            ctx.beginPath(); ctx.moveTo(0, (h * j) / 6); ctx.lineTo(w, (h * j) / 6); ctx.stroke();
        }
        ctx.globalAlpha = 1;
        ctx.lineWidth = 3.5;
        ctx.shadowBlur = 16;
        ctx.shadowColor = color;
        ctx.beginPath();
        var amp = h * 0.42 * (0.04 + level());
        for (var x = 0; x <= w; x += 3) {
            var y = h / 2 + amp * (
                Math.sin(x * 0.018 + t * 0.004) * 0.5 +
                Math.sin(x * 0.047 - t * 0.009) * 0.3 * Math.sin(t * 0.0013) +
                Math.sin(x * 0.13 + t * 0.021) * 0.14
            );
            if (x) {
                ctx.lineTo(x, y);
            } else {
                ctx.moveTo(x, y);
            }
        }
        ctx.stroke();
    }

    function drawSpectrum(t, color) {
        var ctx = spectrum.getContext("2d");
        var size = fit(spectrum);
        var w = size[0];
        var h = size[1];
        var on = level();
        var bw = w / BARS;
        var cell = 10;
        var gap = 4;
        ctx.clearRect(0, 0, w, h);
        ctx.shadowColor = color;
        ctx.shadowBlur = 12;
        for (var i = 0; i < BARS; i++) {
            var target = on * (0.25 + 0.75 * Math.abs(Math.sin(t * 0.003 * (1 + i * 0.07) + i)) * (1 - (i / BARS) * 0.55)) * (0.7 + Math.random() * 0.3);
            bars[i] += (target - bars[i]) * 0.22;
            peaks[i] = Math.max(peaks[i] - 0.006, bars[i]);
            var cells = Math.floor((bars[i] * h) / (cell + gap));
            ctx.fillStyle = color;
            for (var k = 0; k < cells; k++) {
                ctx.globalAlpha = 0.55 + (0.45 * k) / Math.max(1, cells);
                ctx.fillRect(i * bw + 3, h - (k + 1) * (cell + gap), bw - 6, cell);
            }
            ctx.globalAlpha = 1;
            ctx.fillStyle = "#fff1d6";
            ctx.fillRect(i * bw + 3, h - peaks[i] * h - 4, bw - 6, 3);
        }
        ctx.globalAlpha = 1;
    }

    // One rAF loop for both; 15fps under reduced motion; paused while
    // the tab is hidden (rAF already stops, the timer path checks).
    var lastFrame = 0;
    var frameGap = reduceMotion ? 1000 / 15 : 0;
    function frame(t) {
        if (!document.hidden && t - lastFrame >= frameGap) {
            lastFrame = t;
            var color = phosphor();
            if (scope) {
                drawScope(t, color);
            }
            if (spectrum) {
                drawSpectrum(t, color);
            }
        }
        window.requestAnimationFrame(frame);
    }
    if (scope || spectrum) {
        window.requestAnimationFrame(frame);
    }
})();
