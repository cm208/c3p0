// Live now-playing/queue polling for the Music dashboard page - the first
// client-side JS in this app. Deliberately minimal: no framework, no build
// step. The server-rendered page is already a correct, fully-functional
// snapshot - this only upgrades it to auto-refresh; nothing here is load
// bearing for the page to work with JS disabled.
(function () {
    "use strict";

    var panel = document.querySelector("[data-music-panel]");
    if (!panel) return;
    var stateUrl = panel.dataset.stateUrl;
    var POLL_INTERVAL_MS = 4000;

    function setText(field, text) {
        var el = panel.querySelector('[data-field="' + field + '"]');
        if (el) el.textContent = text;
    }

    function formatDuration(seconds) {
        if (seconds === null || seconds === undefined) return "--:--";
        var total = Math.max(0, Math.floor(seconds));
        var hours = Math.floor(total / 3600);
        var minutes = Math.floor((total % 3600) / 60);
        var secs = total % 60;
        var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
        return hours ? hours + ":" + pad(minutes) + ":" + pad(secs) : minutes + ":" + pad(secs);
    }

    function renderNowPlaying(state) {
        var nowPlaying = panel.querySelector("[data-now-playing]");
        if (!nowPlaying) return;

        var empty = nowPlaying.querySelector('[data-field="empty"]');
        var hasCurrent = !!state.current;

        if (hasCurrent) {
            setText("title", state.current.title);
            setText("requested-by", "Requested by " + state.current.requested_by_name);
            setText("elapsed", formatDuration(state.elapsed_seconds));
            setText("duration", formatDuration(state.current.duration_seconds));
            setText("status", state.paused ? "PAUSED" : (state.playing ? "PLAYING" : "STOPPED"));

            var statusBadge = nowPlaying.querySelector('[data-field="status"]');
            if (statusBadge) statusBadge.className = "badge " + (state.playing ? "on" : "off");

            var fill = nowPlaying.querySelector('[data-field="progress"]');
            if (fill && state.current.duration_seconds) {
                var pct = Math.max(0, Math.min(100, (100 * (state.elapsed_seconds || 0)) / state.current.duration_seconds));
                fill.style.width = pct + "%";
            }
        } else if (empty) {
            empty.textContent = state.connected ? "Queue is empty." : "Not playing anything - add a track below to start.";
        }

        var pauseBtn = nowPlaying.querySelector('[data-action="pause"]');
        var resumeBtn = nowPlaying.querySelector('[data-action="resume"]');
        var skipBtn = nowPlaying.querySelector('[data-action="skip"]');
        if (pauseBtn) pauseBtn.disabled = !state.playing;
        if (resumeBtn) resumeBtn.disabled = !state.paused;
        if (skipBtn) skipBtn.disabled = !(state.playing || state.paused);
    }

    function render(state) {
        setText("volume", state.volume_percent + "%");
        setText("queue-length", String(state.queue.length));
        renderNowPlaying(state);
        // The queue list and the voice-channel picker are left to a full
        // page load - rewriting a list of per-row delete forms (and their
        // CSRF tokens) safely from a poll tick is more machinery than a
        // 4-second-stale queue list justifies for a first pass.
    }

    async function poll() {
        try {
            var response = await fetch(stateUrl, { headers: { Accept: "application/json" } });
            if (response.ok) render(await response.json());
        } catch (err) {
            // Network hiccup or the bot container mid-restart - just retry
            // on the next tick rather than surfacing anything.
        }
    }

    setInterval(poll, POLL_INTERVAL_MS);
})();
