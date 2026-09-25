// The command line at the bottom of every page, plus the shared C3P0.*
// API the other scripts use (print, confirm, post, working). Vanilla, no
// framework, no build step, like every script in this dashboard.
//
// Extension point: C3P0.commands.register({name, aliases, help, guildOnly,
// run(args, ctx)}). ctx gives navigate(url), print(tag, text),
// confirm(text) -> Promise<boolean>, post(url, fields) -> Promise<{ok,
// error}>, guildId and page. Commands that change data must post() to the
// same routes the page's forms use - never a console-only API.
(function () {
    "use strict";

    var C3P0 = (window.C3P0 = window.C3P0 || {});
    var body = document.body;
    var guildId = body.dataset.guildId || null;
    var page = body.dataset.page || "";
    var csrf = body.dataset.csrf || "";
    var loggedIn = body.dataset.loggedIn === "true";
    var guildBase = guildId ? "/guilds/" + guildId : null;

    var form = document.getElementById("cmdline");
    var input = form.querySelector(".cmd-input");
    var mirror = form.querySelector(".cmd-text");
    var confirmEl = form.querySelector(".cmd-confirm");

    // ------------------------------------------------------------------
    // Output. Lines go to the SYSLOG column (syslog.js) on guild pages;
    // elsewhere (boot, picker) to a short scratch area under the content.

    var queued = [];

    C3P0.print = function (tag, text, opts) {
        var line = { tag: tag, text: String(text), user: !!(opts && opts.user), time: new Date() };
        if (C3P0.syslog) {
            C3P0.syslog.push(line);
        } else {
            queued.push(line);
            scratchPrint(line);
        }
    };

    C3P0._drainQueued = function () {
        var lines = queued;
        queued = [];
        return lines;
    };

    var scratch = null;
    function scratchPrint(line) {
        if (document.getElementById("syslog")) {
            return; // syslog.js will pick these up from the queue
        }
        if (!scratch) {
            scratch = document.createElement("ul");
            scratch.className = "syslog-lines";
            scratch.setAttribute("aria-live", "polite");
            scratch.style.cssText = "flex:none;margin-top:12px;justify-content:flex-start;";
            var host = document.querySelector("[data-console-out]") || document.getElementById("main");
            host.appendChild(scratch);
        }
        var li = document.createElement("li");
        li.className = "log-line" + (line.user ? " is-user" : "");
        var tag = line.tag === "ERR"
            ? '<span class="log-err">ERR</span>'
            : '<span class="log-tag">' + (line.user ? "&gt;" : "[" + escapeHtml(line.tag) + "]") + "</span>";
        li.innerHTML = tag + " " + escapeHtml(line.text);
        scratch.appendChild(li);
        while (scratch.children.length > 6) {
            scratch.removeChild(scratch.firstChild);
        }
        li.scrollIntoView({ block: "nearest" });
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    C3P0.escapeHtml = escapeHtml;

    // ------------------------------------------------------------------
    // WORKING chip in the status bar while a console request is in flight.

    var workingCount = 0;
    C3P0.working = function (on) {
        workingCount = Math.max(0, workingCount + (on ? 1 : -1));
        body.classList.toggle("is-working", workingCount > 0);
    };

    // ------------------------------------------------------------------
    // POST to a dashboard route exactly like its HTML form would. Routes
    // answer success with a 303 to the page (fetch follows it -> ok) and
    // failure with the page re-rendered at 400 carrying the message in
    // [data-flash-error], which is what gets reported.

    C3P0.post = function (url, fields) {
        var data = new FormData();
        data.append("csrf_token", csrf);
        Object.keys(fields || {}).forEach(function (key) {
            if (fields[key] !== undefined && fields[key] !== null) {
                data.append(key, fields[key]);
            }
        });
        C3P0.working(true);
        return fetch(url, { method: "POST", body: data, credentials: "same-origin" })
            .then(function (response) {
                if (response.ok) {
                    return { ok: true };
                }
                return response.text().then(function (text) {
                    var message = null;
                    try {
                        var doc = new DOMParser().parseFromString(text, "text/html");
                        var flash = doc.querySelector("[data-flash-error]");
                        message = flash ? flash.textContent.trim() : null;
                    } catch (e) { /* not HTML */ }
                    if (!message) {
                        try {
                            message = JSON.parse(text).detail;
                        } catch (e) { /* not JSON either */ }
                    }
                    return { ok: false, error: message || "request failed (" + response.status + ")" };
                });
            })
            .catch(function () {
                return { ok: false, error: "connection lost" };
            })
            .finally(function () {
                C3P0.working(false);
            });
    };

    // ------------------------------------------------------------------
    // (y/N) confirmation: the prompt is replaced by an inverse block and
    // the next line typed answers it. Only y/yes confirms.

    var pendingConfirm = null;

    C3P0.confirm = function (text) {
        if (pendingConfirm) {
            pendingConfirm.resolve(false);
        }
        return new Promise(function (resolve) {
            pendingConfirm = { text: text, resolve: resolve };
            confirmEl.textContent = text + " (y/N)";
            form.classList.add("is-confirming");
            focusInput();
        });
    };

    function answerConfirm(value) {
        var current = pendingConfirm;
        pendingConfirm = null;
        form.classList.remove("is-confirming");
        confirmEl.textContent = "";
        var yes = /^y(es)?$/i.test(value);
        C3P0.print(">", current.text + " " + (value || "n"), { user: true });
        if (!yes) {
            C3P0.print("SYS", "aborted");
        }
        current.resolve(yes);
    }

    // ------------------------------------------------------------------
    // Command registry.

    var registry = {};
    var ordered = [];

    C3P0.commands = {
        register: function (def) {
            ordered.push(def);
            [def.name].concat(def.aliases || []).forEach(function (n) {
                registry[n.toLowerCase()] = def;
            });
        },
        find: function (name) {
            return registry[name.toLowerCase()] || null;
        },
        all: function () {
            return ordered.slice();
        },
    };

    var ctx = {
        navigate: function (url) { window.location.href = url; },
        print: C3P0.print,
        confirm: function (text) { return C3P0.confirm(text); },
        post: C3P0.post,
        guildId: guildId,
        page: page,
    };

    // Hooks for Enter on an empty prompt ("continue"): the boot screen
    // authenticates, the picker connects to the selected guild. Pages may
    // set these before this deferred script runs, so never reset them.
    C3P0.onEmptyEnter = C3P0.onEmptyEnter || null;
    C3P0.onArrow = C3P0.onArrow || null;

    function run(raw) {
        var value = raw.trim();
        if (pendingConfirm) {
            answerConfirm(value);
            return;
        }
        if (!value) {
            if (C3P0.onEmptyEnter) {
                C3P0.onEmptyEnter();
            }
            return;
        }
        pushHistory(value);
        C3P0.print(">", value, { user: true });

        var parts = value.split(/\s+/);
        var name = parts[0];
        var args = parts.slice(1);
        var def = C3P0.commands.find(name);
        if (!def) {
            C3P0.print("ERR", "command not found: " + name);
            return;
        }
        if (def.guildOnly && !guildId) {
            C3P0.print("ERR", loggedIn ? "no guild attached · run connect <n>" : "not authenticated · press enter");
            return;
        }
        try {
            var result = def.run(args, ctx, value.slice(name.length).trim());
            if (result && typeof result.catch === "function") {
                result.catch(function (err) {
                    C3P0.print("ERR", err && err.message ? err.message : "command failed");
                });
            }
        } catch (err) {
            C3P0.print("ERR", err && err.message ? err.message : "command failed");
        }
    }

    // ------------------------------------------------------------------
    // History (up/down), last 50 entries per browser session.

    var HISTORY_KEY = "c3p0.history";
    var history = [];
    try {
        history = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]");
    } catch (e) {
        history = [];
    }
    var historyIndex = history.length;
    var draft = "";

    function pushHistory(value) {
        if (history[history.length - 1] !== value) {
            history.push(value);
            history = history.slice(-50);
            try {
                sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history));
            } catch (e) { /* storage blocked */ }
        }
        historyIndex = history.length;
    }

    function browseHistory(step) {
        if (!history.length) {
            return;
        }
        if (historyIndex === history.length) {
            draft = input.value;
        }
        historyIndex = Math.max(0, Math.min(history.length, historyIndex + step));
        setInput(historyIndex === history.length ? draft : history[historyIndex]);
    }

    // ------------------------------------------------------------------
    // Input wiring: the invisible <input> owns typing; the mirror shows it.

    function setInput(value) {
        input.value = value;
        sync();
    }

    function sync() {
        mirror.textContent = input.value;
    }

    function focusInput() {
        input.focus({ preventScroll: true });
    }
    C3P0.focusCommandLine = focusInput;

    input.addEventListener("input", sync);
    input.addEventListener("focus", function () {
        form.classList.add("is-focused");
    });
    input.addEventListener("blur", function () {
        form.classList.remove("is-focused", "is-focused-visible");
    });
    input.addEventListener("keyup", function (event) {
        if (event.key === "Tab") {
            form.classList.add("is-focused-visible");
        }
    });

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        var value = input.value;
        setInput("");
        run(value);
    });

    input.addEventListener("keydown", function (event) {
        if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            if (C3P0.onArrow && !input.value && C3P0.onArrow(event.key === "ArrowUp" ? -1 : 1)) {
                event.preventDefault();
                return;
            }
            event.preventDefault();
            browseHistory(event.key === "ArrowUp" ? -1 : 1);
        } else if (event.key === "Escape" && pendingConfirm) {
            event.preventDefault();
            setInput("");
            answerConfirm("");
        }
    });

    // Clicking empty space in the tube returns focus to the command line,
    // but never steals a click meant for a control, a text selection, or a
    // drag in the channel/role canvases.
    document.getElementById("tube").addEventListener("click", function (event) {
        var target = event.target;
        if (target.closest("a, button, input, textarea, select, label, summary, [draggable='true'], [contenteditable], .msg-editor, [data-no-refocus]")) {
            return;
        }
        var selection = window.getSelection();
        if (selection && String(selection).length) {
            return;
        }
        focusInput();
    });

    // Autofocus only where a physical keyboard is likely - on a phone this
    // would pop the on-screen keyboard over the page on every load.
    if (window.matchMedia("(pointer: fine)").matches && !document.querySelector("[autofocus]")) {
        focusInput();
    }

    // ------------------------------------------------------------------
    // Function keys: F1-F7 guild pages (from the nav's data-fkey), F9
    // guild picker, F10 logout.

    document.addEventListener("keydown", function (event) {
        if (!/^F([1-9]|10)$/.test(event.key) || event.ctrlKey || event.altKey || event.metaKey) {
            return;
        }
        var target = document.querySelector('[data-fkey="' + event.key + '"]');
        if (event.key === "F9" && loggedIn) {
            event.preventDefault();
            ctx.navigate("/");
        } else if (event.key === "F10" && loggedIn) {
            event.preventDefault();
            logout();
        } else if (target && target.tagName === "A") {
            event.preventDefault();
            ctx.navigate(target.getAttribute("href"));
        }
    });

    function logout() {
        try {
            sessionStorage.removeItem("c3p0.booted");
            sessionStorage.removeItem("c3p0.powered");
        } catch (e) { /* storage blocked */ }
        var logoutForm = document.getElementById("logout-form");
        if (logoutForm) {
            logoutForm.submit();
        }
    }

    document.addEventListener("submit", function (event) {
        if (event.target.id === "logout-form" || event.target.getAttribute("form") === "logout-form") {
            try {
                sessionStorage.removeItem("c3p0.booted");
                sessionStorage.removeItem("c3p0.powered");
            } catch (e) { /* storage blocked */ }
        }
    });

    // ------------------------------------------------------------------
    // Form results as SYSLOG lines. Routes redirect (303) on success and
    // re-render with an error on failure, so: remember what was submitted,
    // and on the next page load report [OK] - or ERR if the page came back
    // with an error.

    var FLASH_KEY = "c3p0.flash";

    document.addEventListener("submit", function (event) {
        var f = event.target;
        if (!(f instanceof HTMLFormElement) || f === form || (f.method || "").toLowerCase() !== "post") {
            return;
        }
        if (f.id === "logout-form") {
            return;
        }
        // confirm.js re-submits a data-confirm form only after a "y"; the
        // first (intercepted) submit must not queue an [OK] for an abort.
        if (f.dataset.confirm && f.dataset.confirmed !== "yes") {
            return;
        }
        var label = f.dataset.ok || (page ? page + ".cfg written" : "request sent");
        try {
            sessionStorage.setItem(FLASH_KEY, label);
        } catch (e) { /* storage blocked */ }
    }, true);

    function reportFlash() {
        var label = null;
        try {
            label = sessionStorage.getItem(FLASH_KEY);
            sessionStorage.removeItem(FLASH_KEY);
        } catch (e) { /* storage blocked */ }
        var error = document.querySelector("[data-flash-error]");
        if (error) {
            C3P0.print("ERR", error.textContent.trim());
        } else if (label) {
            C3P0.print("OK", label);
        }
    }

    // ------------------------------------------------------------------
    // Status bar: clock, and the bot's live shard/gateway values.

    var clock = document.querySelector("[data-clock]");
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function tick() {
        var d = new Date();
        if (clock) {
            clock.textContent = pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
        }
    }
    tick();
    setInterval(tick, 1000);

    var shardEl = document.querySelector('[data-status="shard"]');
    var gwEl = document.querySelector('[data-status="gw"]');
    function pollStatus(force) {
        if (!loggedIn || (document.hidden && force !== true)) {
            return;
        }
        fetch("/api/status", { credentials: "same-origin", headers: { Accept: "application/json" } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (s) {
                if (!s || !s.reachable) {
                    shardEl.textContent = "SHARD OFFLINE";
                    gwEl.textContent = "GW --ms";
                    return;
                }
                shardEl.textContent = "SHARD " + s.shard_id + (s.ready ? " READY" : " CONNECTING");
                gwEl.textContent = "GW " + (s.latency_ms === null ? "--" : s.latency_ms) + "ms";
            })
            .catch(function () { /* keep the last values */ });
    }
    pollStatus(true);
    setInterval(pollStatus, 15000);
    // Polls skip hidden tabs; catch up the moment the tab is shown again.
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
            pollStatus();
        }
    });

    // ------------------------------------------------------------------
    // Built-in commands.

    var PAGES = {
        general: "/general",
        server: "/server-management/channels",
        music: "/music",
        mod: "/moderation",
        cmds: "/custom-commands",
        welcome: "/welcome",
        roles: "/roles",
    };
    var PAGE_ALIASES = { modlog: "mod", moderation: "mod", commands: "cmds", "custom-commands": "cmds", "r.roles": "roles" };

    function pageTarget(name) {
        var key = (name || "").replace(/^~\//, "").replace(/\/$/, "").toLowerCase();
        key = PAGE_ALIASES[key] || key;
        return PAGES[key] ? key : null;
    }

    Object.keys(PAGES).forEach(function (key) {
        var aliases = Object.keys(PAGE_ALIASES).filter(function (a) { return PAGE_ALIASES[a] === key; });
        C3P0.commands.register({
            name: key,
            aliases: aliases,
            help: "open " + key,
            guildOnly: true,
            nav: true,
            run: function () { ctx.navigate(guildBase + PAGES[key]); },
        });
    });

    C3P0.commands.register({
        name: "cd",
        help: "cd <page>",
        guildOnly: true,
        run: function (args) {
            var key = pageTarget(args[0]);
            if (!key) {
                C3P0.print("ERR", "cd: " + (args[0] || "?") + ": no such page");
                return;
            }
            ctx.navigate(guildBase + PAGES[key]);
        },
    });

    C3P0.commands.register({
        name: "guilds",
        aliases: ["exit"],
        help: "guild picker",
        run: function () {
            if (!loggedIn) {
                C3P0.print("ERR", "not authenticated · press enter");
                return;
            }
            ctx.navigate("/");
        },
    });

    C3P0.commands.register({
        name: "connect",
        help: "connect <n>",
        run: function (args) {
            if (!loggedIn) {
                C3P0.print("ERR", "not authenticated · press enter");
                return;
            }
            var rows = document.querySelectorAll("[data-picker-row]");
            if (!rows.length) {
                ctx.navigate("/");
                return;
            }
            var n = parseInt(args[0], 10) || 1;
            var row = rows[Math.max(0, Math.min(rows.length, n) - 1)];
            ctx.navigate(row.getAttribute("href"));
        },
    });

    C3P0.commands.register({
        name: "logout",
        help: "end session",
        run: function () {
            if (!loggedIn) {
                C3P0.print("ERR", "no session");
                return;
            }
            logout();
        },
    });

    function musicAction(action, fields, doneText) {
        return C3P0.post(guildBase + "/music/player/" + action, fields).then(function (result) {
            if (!result.ok) {
                C3P0.print("ERR", result.error);
                return;
            }
            C3P0.print("MUSIC", doneText);
            if (C3P0.music) {
                C3P0.music.refresh();
            }
        });
    }

    C3P0.commands.register({
        name: "play",
        help: "play <song or url>",
        guildOnly: true,
        run: function (args, c, rest) {
            if (!rest) {
                C3P0.print("ERR", "usage: play <song>");
                return;
            }
            var channelSelect = document.getElementById("voice_channel_id");
            return C3P0.post(guildBase + "/music/queue/add", {
                query: rest,
                voice_channel_id: channelSelect && channelSelect.value ? channelSelect.value : null,
            }).then(function (result) {
                if (!result.ok) {
                    C3P0.print("ERR", result.error);
                    return;
                }
                C3P0.print("MUSIC", 'queued "' + rest + '"');
                if (page === "music") {
                    window.location.reload();
                } else {
                    ctx.navigate(guildBase + "/music");
                }
            });
        },
    });

    C3P0.commands.register({ name: "skip", help: "skip track", guildOnly: true, run: function () { return musicAction("skip", {}, "skipped"); } });
    C3P0.commands.register({ name: "pause", help: "pause", guildOnly: true, run: function () { return musicAction("pause", {}, "paused"); } });
    C3P0.commands.register({ name: "resume", help: "resume", guildOnly: true, run: function () { return musicAction("resume", {}, "resumed"); } });
    C3P0.commands.register({ name: "stop", help: "stop + clear queue", guildOnly: true, run: function () { return musicAction("stop", {}, "stopped · queue cleared"); } });
    C3P0.commands.register({ name: "prev", aliases: ["restart"], help: "restart track", guildOnly: true, run: function () { return musicAction("restart", {}, "restarted track"); } });

    C3P0.commands.register({
        name: "vol",
        aliases: ["volume"],
        help: "vol <0-100>",
        guildOnly: true,
        run: function (args) {
            var n = parseInt(args[0], 10);
            if (isNaN(n) || n < 0 || n > 100) {
                C3P0.print("ERR", "usage: vol <0-100>");
                return;
            }
            return musicAction("volume", { percent: n }, "volume " + n + "%");
        },
    });

    C3P0.commands.register({
        name: "rm",
        help: "rm <!command|#channel>",
        guildOnly: true,
        run: function (args, c, rest) {
            if (rest.charAt(0) === "#") {
                if (C3P0.canvas) {
                    C3P0.canvas.removeByName(rest.slice(1));
                } else {
                    C3P0.print("SYS", "channels are edited on SERVER · opening it");
                    ctx.navigate(guildBase + PAGES.server);
                }
                return;
            }
            if (!rest) {
                C3P0.print("ERR", "usage: rm <!command|#channel>");
                return;
            }
            return C3P0.confirm("rm " + rest + " · THIS CANNOT BE UNDONE. PROCEED?").then(function (yes) {
                if (!yes) {
                    return;
                }
                return C3P0.post(guildBase + "/custom-commands/" + encodeURIComponent(rest) + "/delete").then(function (result) {
                    if (!result.ok) {
                        C3P0.print("ERR", "rm: " + rest + ": " + result.error);
                        return;
                    }
                    C3P0.print("OK", "command " + rest + " removed");
                    if (page === "cmds") {
                        window.location.reload();
                    }
                });
            });
        },
    });

    C3P0.commands.register({
        name: "phosphor",
        help: "phosphor <amber|sodium|green>",
        run: function (args) {
            var name = (args[0] || "").toLowerCase();
            if (!C3P0.crt || !C3P0.crt.setPhosphor(name)) {
                C3P0.print("ERR", "usage: phosphor <amber|sodium|green>");
                return;
            }
            C3P0.print("SYS", "phosphor " + name);
        },
    });

    C3P0.commands.register({
        name: "crt",
        help: "crt <off|normal|max>",
        run: function (args) {
            var level = (args[0] || "").toLowerCase();
            if (!C3P0.crt || !C3P0.crt.setLevel(level)) {
                C3P0.print("ERR", "usage: crt <off|normal|max>");
                return;
            }
            C3P0.print("SYS", "crt " + level);
        },
    });

    C3P0.commands.register({
        name: "clear",
        help: "clear syslog",
        run: function () {
            if (C3P0.syslog) {
                C3P0.syslog.clear();
            } else if (scratch) {
                scratch.innerHTML = "";
            }
        },
    });

    C3P0.commands.register({
        name: "help",
        aliases: ["?"],
        help: "this list",
        run: function () {
            C3P0.print("HELP", "general server music mod cmds welcome roles · cd <page>");
            C3P0.print("HELP", "guilds · connect <n> · logout");
            C3P0.print("HELP", "play <song> · skip · pause · resume · prev · stop · vol <0-100>");
            C3P0.print("HELP", "rm <!cmd|#channel> · clear");
            C3P0.print("HELP", "phosphor <amber|sodium|green> · crt <off|normal|max>");
            C3P0.print("HELP", "F1-F7 pages · F9 guilds · F10 logout · up/down history");
        },
    });

    C3P0.commands.register({
        name: "sudo",
        help: "",
        run: function () {
            C3P0.print("ERR", "operator is not in the sudoers file. this incident will be reported.");
        },
    });

    // Reported once the page's own scripts (syslog.js) have had a chance
    // to attach, so the lines land in the SYSLOG column.
    window.addEventListener("load", reportFlash);
})();
