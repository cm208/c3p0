// Message editor: a formatting toolbar + live Discord-style preview for any
// <textarea data-message-editor>. Vanilla, no framework, no build step,
// matching the dashboard's other scripts.
//
// Progressive enhancement only: the textarea stays the real form field and
// every toolbar action just edits its plain text, so the form posts exactly
// what it always did and the server validates it exactly as before. With JS
// off, the page is the plain textarea it used to be.
//
// Page-level data (variables, live roles/channels) comes from one
// <script type="application/json" id="message-editor-context"> built by
// app/web/message_editor.py. Ids in it are strings and must stay strings -
// never parseInt/Number() a snowflake (see that module's docstring).
//
// Per-textarea options, all data-* attributes:
//   data-variables="a,b"   {placeholders} offered for this field; omit the
//                          attribute entirely for a field the bot posts
//                          literally (no substitution happens at all)
//   data-default="..."     what the bot sends when the field is left blank
//   data-preview-label     heading over the preview (e.g. "Direct message")
//   data-embed-toggle=id   checkbox: when checked, preview as an embed
//   data-embed-always      always preview as an embed
//   data-embed-title=id    input whose value is the embed's title
//   data-embed-footer=id   input whose value is the embed's footer
//   data-embed-fallback=id field used as the embed body when this one is
//                          blank (welcome's embed description -> template)
//   data-embed-override=id field that, when non-blank, replaces this one as
//                          the embed body (the inverse of the above)
(function () {
    "use strict";

    var contextEl = document.getElementById("message-editor-context");
    if (!contextEl) {
        return;
    }
    var ctx = JSON.parse(contextEl.textContent);

    var variableSamples = {};
    ctx.variables.forEach(function (v) {
        variableSamples[v.name] = v.sample;
    });
    var roleNames = {};
    ctx.roles.forEach(function (r) {
        roleNames[r.id] = r.name;
    });
    var channelNames = {};
    ctx.channels.forEach(function (c) {
        channelNames[c.id] = c.name;
    });

    // --- Small DOM helpers ---

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

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    // --- Text editing primitives ---

    // execCommand("insertText") keeps the browser's native undo stack
    // intact (Ctrl+Z undoes a toolbar action like it would typing);
    // setRangeText is the fallback where it's unsupported, at the cost of
    // undo. Either way an "input" event fires so the preview re-renders.
    function replaceSelection(ta, text, selectFrom, selectTo) {
        ta.focus();
        var start = ta.selectionStart;
        var inserted = false;
        try {
            inserted = document.execCommand("insertText", false, text);
        } catch (e) {
            inserted = false;
        }
        if (!inserted) {
            ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, "end");
            ta.dispatchEvent(new Event("input", { bubbles: true }));
        }
        if (selectFrom !== undefined) {
            ta.setSelectionRange(start + selectFrom, start + selectTo);
        }
    }

    function insertText(ta, text) {
        replaceSelection(ta, text, text.length, text.length);
    }

    // Wrap the selection in before/after markers, or unwrap it if it's
    // already wrapped (so clicking Bold twice is a toggle, like any editor).
    function toggleWrap(ta, before, after, placeholder) {
        var s = ta.selectionStart;
        var e = ta.selectionEnd;
        var value = ta.value;
        var selected = value.slice(s, e);
        if (
            s >= before.length &&
            value.slice(s - before.length, s) === before &&
            value.slice(e, e + after.length) === after
        ) {
            ta.setSelectionRange(s - before.length, e + after.length);
            replaceSelection(ta, selected, 0, selected.length);
            return;
        }
        var inner = selected || placeholder;
        replaceSelection(ta, before + inner + after, before.length, before.length + inner.length);
    }

    // Line-level syntax (headings, quotes, lists, subtext): applies to every
    // line the selection touches; removes the prefix if every line already
    // has it. Other line prefixes are swapped out, so H1 -> H2 doesn't
    // produce "## # text".
    var LINE_PREFIX_RE = /^(#{1,3} |-# |> |- )/;

    function toggleLinePrefix(ta, prefix) {
        var value = ta.value;
        var start = value.lastIndexOf("\n", ta.selectionStart - 1) + 1;
        var endBreak = value.indexOf("\n", ta.selectionEnd);
        var end = endBreak === -1 ? value.length : endBreak;
        var lines = value.slice(start, end).split("\n");
        var allHave = lines.every(function (line) {
            return line.indexOf(prefix) === 0;
        });
        var updated = lines
            .map(function (line) {
                if (allHave) {
                    return line.slice(prefix.length);
                }
                return prefix + line.replace(LINE_PREFIX_RE, "");
            })
            .join("\n");
        ta.setSelectionRange(start, end);
        replaceSelection(ta, updated, 0, updated.length);
    }

    function insertCodeBlock(ta) {
        var selected = ta.value.slice(ta.selectionStart, ta.selectionEnd);
        var inner = selected || "code";
        var lead = ta.selectionStart > 0 && ta.value[ta.selectionStart - 1] !== "\n" ? "\n" : "";
        var open = lead + "```\n";
        replaceSelection(ta, open + inner + "\n```", open.length, open.length + inner.length);
    }

    // --- Discord markdown -> HTML (preview only) ---
    //
    // Safety model: everything user-controlled is HTML-escaped before any
    // markup is added. Constructs whose content must NOT be further
    // formatted (code, mentions, links' hrefs) are rendered to finished,
    // escaped HTML immediately and parked in `stash` behind a \u0000N\u0000
    // token, then swapped back in at the very end; the remaining text is
    // escaped as a whole and only ever gains fixed tags from the inline
    // rules below. The sole attribute that carries user text is a link
    // href, which is escaped and restricted to http(s).

    var TIMESTAMP_STYLES = {
        t: { hour: "numeric", minute: "2-digit" },
        T: { hour: "numeric", minute: "2-digit", second: "2-digit" },
        d: { year: "numeric", month: "2-digit", day: "2-digit" },
        D: { year: "numeric", month: "long", day: "numeric" },
        f: { year: "numeric", month: "long", day: "numeric", hour: "numeric", minute: "2-digit" },
        F: {
            weekday: "long",
            year: "numeric",
            month: "long",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
        },
    };

    function formatRelative(date) {
        var seconds = Math.round((date.getTime() - Date.now()) / 1000);
        var units = [
            ["year", 31536000],
            ["month", 2592000],
            ["day", 86400],
            ["hour", 3600],
            ["minute", 60],
            ["second", 1],
        ];
        var rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
        for (var i = 0; i < units.length; i++) {
            if (Math.abs(seconds) >= units[i][1] || units[i][0] === "second") {
                return rtf.format(Math.round(seconds / units[i][1]), units[i][0]);
            }
        }
        return "";
    }

    function formatTimestamp(unix, style) {
        var date = new Date(unix * 1000);
        if (isNaN(date.getTime())) {
            return "Invalid Date";
        }
        if (style === "R") {
            return formatRelative(date);
        }
        var options = TIMESTAMP_STYLES[style] || TIMESTAMP_STYLES.f;
        return date.toLocaleString(undefined, options);
    }

    function applyInline(s) {
        return s
            .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
            .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
            .replace(/__(.+?)__/g, "<u>$1</u>")
            .replace(/\*([^*\s](?:[^*]*?[^*\s])?)\*/g, "<em>$1</em>")
            .replace(/(^|[^\w])_([^_\s](?:[^_]*?[^_\s])?)_(?!\w)/g, "$1<em>$2</em>")
            .replace(/~~(.+?)~~/g, "<s>$1</s>")
            .replace(/\|\|(.+?)\|\|/g, '<span class="md-spoiler" tabindex="0">$1</span>');
    }

    function renderTokens(s, keep, pill) {
        s = s.replace(/<@!?(\d+)>/g, function (_, id) {
            return pill("@" + (ctx.users[id] || "unknown-user"));
        });
        s = s.replace(/<@&(\d+)>/g, function (_, id) {
            return pill("@" + (roleNames[id] || "deleted-role"));
        });
        s = s.replace(/<#(\d+)>/g, function (_, id) {
            return pill("#" + (channelNames[id] || "unknown-channel"));
        });
        s = s.replace(/@(everyone|here)\b/g, function (_, which) {
            return pill("@" + which);
        });
        s = s.replace(/<t:(-?\d+)(?::([tTdDfFR]))?>/g, function (_, unix, style) {
            return keep('<span class="md-timestamp">' + escapeHtml(formatTimestamp(Number(unix), style)) + "</span>");
        });
        s = s.replace(/<a?:(\w+):(\d+)>/g, function (_, name) {
            return keep('<span class="md-emoji">:' + escapeHtml(name) + ":</span>");
        });
        return s;
    }

    function renderMarkdown(source, options) {
        var stash = [];
        function keep(html) {
            stash.push(html);
            return "\u0000" + (stash.length - 1) + "\u0000";
        }
        function pill(text) {
            return keep('<span class="md-mention">' + escapeHtml(text) + "</span>");
        }

        var s = String(source).replace(/\u0000/g, "");

        s = s.replace(/```(?:([\w+-]+)\n)?([\s\S]*?)```/g, function (_, lang, code) {
            return keep('<pre class="md-codeblock"><code>' + escapeHtml(code.replace(/^\n/, "")) + "</code></pre>");
        });
        s = s.replace(/``([^`]+?)``|`([^`\n]+?)`/g, function (_, a, b) {
            return keep("<code>" + escapeHtml(a || b) + "</code>");
        });
        s = s.replace(/\\([*_~|`>#\-\\[\]()<@:])/g, function (_, ch) {
            return keep(escapeHtml(ch));
        });

        // Embed titles don't resolve mentions/timestamps/emoji on Discord -
        // they show the raw syntax - so neither does the preview.
        if (!options.inlineOnly) {
            s = renderTokens(s, keep, pill);
        }

        if (!options.plainLinks) {
            s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, function (_, text, url) {
                return keep('<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">') + text + keep("</a>");
            });
        }
        s = s.replace(/<?(https?:\/\/[^\s<>\u0000]+[^\s<>\u0000.,:;"')\]])>?/g, function (_, url) {
            return keep('<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(url) + "</a>");
        });

        s = escapeHtml(s);

        var html = options.inlineOnly ? applyInline(s) : renderBlocks(s);
        return html.replace(/\u0000(\d+)\u0000/g, function (_, i) {
            return stash[Number(i)];
        });
    }

    // Line-level syntax on already-escaped text: headings, subtext, lists,
    // single-line "> " quotes and the rest-of-message ">>> " quote.
    function renderBlocks(escaped) {
        var out = [];
        var inQuote = false;
        var inList = false;
        var quoteRest = false;

        escaped.split("\n").forEach(function (line) {
            var quoted = quoteRest;
            if (!quoted && line.indexOf("&gt;&gt;&gt; ") === 0) {
                quoteRest = quoted = true;
                line = line.slice(13);
            } else if (!quoted && line.indexOf("&gt; ") === 0) {
                quoted = true;
                line = line.slice(5);
            }

            var heading = /^(#{1,3}) (.+)$/.exec(line);
            var subtext = /^-# (.+)$/.exec(line);
            var item = /^\s*[-*] (.+)$/.exec(line);

            if (inList && !item) {
                out.push("</ul>");
                inList = false;
            }
            if (quoted !== inQuote) {
                if (inList) {
                    out.push("</ul>");
                    inList = false;
                }
                out.push(quoted ? '<blockquote class="md-quote">' : "</blockquote>");
                inQuote = quoted;
            }

            if (heading) {
                var level = heading[1].length;
                out.push("<h" + level + ' class="md-h">' + applyInline(heading[2]) + "</h" + level + ">");
            } else if (subtext) {
                out.push('<div class="md-subtext">' + applyInline(subtext[1]) + "</div>");
            } else if (item) {
                if (!inList) {
                    out.push("<ul>");
                    inList = true;
                }
                out.push("<li>" + applyInline(item[1]) + "</li>");
            } else {
                out.push('<div class="md-line">' + (applyInline(line) || "<br>") + "</div>");
            }
        });
        if (inList) {
            out.push("</ul>");
        }
        if (inQuote) {
            out.push("</blockquote>");
        }
        return out.join("");
    }

    // --- Variable substitution (preview only) ---

    // Mirrors app/utils/templates.py: a variable this field offers gets its
    // sample value; one the bot knows but this field doesn't render with
    // (e.g. {channel} in a DM) comes out empty, as it would on Discord; an
    // unknown one is left literal and reported, since saving will fail.
    function substitute(text, allowed, unknown) {
        return text.replace(/\{(\w+)\}/g, function (match, name) {
            if (allowed.indexOf(name) !== -1) {
                return variableSamples[name];
            }
            if (Object.prototype.hasOwnProperty.call(variableSamples, name)) {
                return "";
            }
            if (unknown.indexOf(match) === -1) {
                unknown.push(match);
            }
            return match;
        });
    }

    // --- Popover menus ---

    var openMenu = null;

    function closeMenu() {
        if (openMenu) {
            openMenu.menu.hidden = true;
            openMenu.button.setAttribute("aria-expanded", "false");
            openMenu = null;
        }
    }

    document.addEventListener("click", function (event) {
        if (openMenu && !openMenu.wrap.contains(event.target)) {
            closeMenu();
        }
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && openMenu) {
            var button = openMenu.button;
            closeMenu();
            button.focus();
        }
    });

    function toolButton(label, title, onClick) {
        var button = el("button", "msg-tool", label);
        button.type = "button";
        button.title = title;
        button.setAttribute("aria-label", title);
        button.addEventListener("click", onClick);
        return button;
    }

    // A toolbar button that opens a popover; `build(menu, close)` fills it.
    // Rebuilt on every open, so role/channel lists and form fields always
    // start fresh.
    function menuButton(label, title, build) {
        var wrap = el("div", "msg-tool-wrap");
        var menu = el("div", "msg-menu");
        menu.hidden = true;
        var button = toolButton(label, title, function () {
            if (openMenu && openMenu.menu === menu) {
                closeMenu();
                return;
            }
            closeMenu();
            menu.textContent = "";
            build(menu, closeMenu);
            menu.hidden = false;
            button.setAttribute("aria-expanded", "true");
            openMenu = { wrap: wrap, menu: menu, button: button };
            var first = menu.querySelector("input, select, button");
            if (first) {
                first.focus();
            }
        });
        button.setAttribute("aria-haspopup", "true");
        button.setAttribute("aria-expanded", "false");
        wrap.appendChild(button);
        wrap.appendChild(menu);
        return wrap;
    }

    // A filterable list of insertable items, grouped under headings.
    function buildPickList(menu, groups, onPick) {
        var filter = el("input", "msg-menu-filter");
        filter.type = "search";
        filter.placeholder = "Filter…";
        filter.setAttribute("aria-label", "Filter");
        var list = el("div", "msg-menu-list");
        menu.appendChild(filter);
        menu.appendChild(list);

        function draw() {
            var q = filter.value.trim().toLowerCase();
            list.textContent = "";
            var any = false;
            groups.forEach(function (group) {
                var items = group.items.filter(function (item) {
                    return !q || item.label.toLowerCase().indexOf(q) !== -1 || item.hint.toLowerCase().indexOf(q) !== -1;
                });
                if (!items.length) {
                    return;
                }
                any = true;
                list.appendChild(el("div", "msg-menu-heading", group.title));
                items.forEach(function (item) {
                    var option = el("button", "msg-menu-item");
                    option.type = "button";
                    option.appendChild(el("span", "msg-menu-label", item.label));
                    option.appendChild(el("span", "msg-menu-hint", item.hint));
                    option.addEventListener("click", function () {
                        onPick(item.insert);
                    });
                    list.appendChild(option);
                });
            });
            if (!any) {
                list.appendChild(el("div", "msg-menu-empty", "Nothing matches."));
            }
        }

        filter.addEventListener("input", draw);
        // Enter in a field nested inside the dashboard's <form> would
        // otherwise submit the whole page.
        filter.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                var firstItem = list.querySelector(".msg-menu-item");
                if (firstItem) {
                    firstItem.click();
                }
            }
        });
        draw();
    }

    function menuField(menu, labelText, input) {
        var field = el("label", "msg-menu-field");
        field.appendChild(el("span", "msg-menu-heading", labelText));
        field.appendChild(input);
        menu.appendChild(field);
        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                var submit = menu.querySelector(".msg-menu-submit");
                if (submit) {
                    submit.click();
                }
            }
        });
        return input;
    }

    function menuSubmit(menu, label, onClick) {
        var button = el("button", "msg-menu-submit", label);
        button.type = "button";
        button.addEventListener("click", onClick);
        menu.appendChild(button);
    }

    function pad(n) {
        return (n < 10 ? "0" : "") + n;
    }

    function localDateTimeValue(date) {
        return (
            date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
            "T" + pad(date.getHours()) + ":" + pad(date.getMinutes())
        );
    }

    // --- Editor construction ---

    function byId(id) {
        return id ? document.getElementById(id) : null;
    }

    function enhance(ta) {
        var allowed = ta.hasAttribute("data-variables")
            ? ta.getAttribute("data-variables").split(",").filter(Boolean)
            : null;
        var embedToggle = byId(ta.dataset.embedToggle);
        var embedTitle = byId(ta.dataset.embedTitle);
        var embedFooter = byId(ta.dataset.embedFooter);
        var embedFallback = byId(ta.dataset.embedFallback);
        var embedOverride = byId(ta.dataset.embedOverride);

        var root = el("div", "msg-editor");
        var toolbar = el("div", "msg-toolbar");
        toolbar.setAttribute("role", "toolbar");
        toolbar.setAttribute("aria-label", "Message formatting");
        var panes = el("div", "msg-panes");
        var inputPane = el("div", "msg-input");
        var previewPane = el("div", "msg-preview");
        previewPane.setAttribute("aria-live", "polite");

        ta.parentNode.insertBefore(root, ta);
        root.appendChild(toolbar);
        root.appendChild(panes);
        panes.appendChild(inputPane);
        panes.appendChild(previewPane);
        inputPane.appendChild(ta);
        var counter = el("div", "msg-counter");
        inputPane.appendChild(counter);

        // Formatting
        var formatGroup = el("div", "msg-tool-group");
        [
            ["B", "Bold (Ctrl+B) - **text**", "**", "**", "bold text"],
            ["I", "Italic (Ctrl+I) - *text*", "*", "*", "italic text"],
            ["U", "Underline (Ctrl+U) - __text__", "__", "__", "underlined text"],
            ["S", "Strikethrough - ~~text~~", "~~", "~~", "struck text"],
            ["||", "Spoiler - ||text||", "||", "||", "spoiler"],
            ["</>", "Inline code - `text`", "`", "`", "code"],
        ].forEach(function (spec) {
            var button = toolButton(spec[0], spec[1], function () {
                toggleWrap(ta, spec[2], spec[3], spec[4]);
            });
            button.classList.add("msg-tool-" + spec[4].split(" ")[0]);
            formatGroup.appendChild(button);
        });
        formatGroup.appendChild(
            toolButton("{ }", "Code block - ```text```", function () {
                insertCodeBlock(ta);
            })
        );
        toolbar.appendChild(formatGroup);

        // Line-level
        var blockGroup = el("div", "msg-tool-group");
        blockGroup.appendChild(
            menuButton("H", "Heading", function (menu, close) {
                [
                    ["Heading 1", "# ", "Large"],
                    ["Heading 2", "## ", "Medium"],
                    ["Heading 3", "### ", "Small"],
                    ["Subtext", "-# ", "Small, muted"],
                ].forEach(function (spec) {
                    var option = el("button", "msg-menu-item");
                    option.type = "button";
                    option.appendChild(el("span", "msg-menu-label", spec[0]));
                    option.appendChild(el("span", "msg-menu-hint", spec[1].trim() + " " + spec[2]));
                    option.addEventListener("click", function () {
                        close();
                        toggleLinePrefix(ta, spec[1]);
                    });
                    menu.appendChild(option);
                });
            })
        );
        blockGroup.appendChild(
            toolButton(">", "Quote - > text", function () {
                toggleLinePrefix(ta, "> ");
            })
        );
        blockGroup.appendChild(
            toolButton("•", "Bulleted list - - item", function () {
                toggleLinePrefix(ta, "- ");
            })
        );
        blockGroup.appendChild(
            menuButton("Link", "Masked link - [text](https://...)", function (menu, close) {
                var selected = ta.value.slice(ta.selectionStart, ta.selectionEnd);
                var textInput = el("input");
                textInput.type = "text";
                textInput.value = selected;
                textInput.placeholder = "Link text";
                var urlInput = el("input");
                urlInput.type = "url";
                urlInput.placeholder = "https://";
                menuField(menu, "Text", textInput);
                menuField(menu, "URL", urlInput);
                var hint = el("div", "msg-menu-empty", "");
                menu.appendChild(hint);
                menuSubmit(menu, "Insert link", function () {
                    var url = urlInput.value.trim();
                    if (!/^https?:\/\/\S+$/.test(url)) {
                        hint.textContent = "Enter a full http:// or https:// URL.";
                        urlInput.focus();
                        return;
                    }
                    var text = textInput.value.trim() || url;
                    close();
                    insertText(ta, "[" + text.replace(/[[\]]/g, "") + "](" + url + ")");
                });
            })
        );
        toolbar.appendChild(blockGroup);

        // Inserts
        var insertGroup = el("div", "msg-tool-group");
        if (allowed && allowed.length) {
            insertGroup.appendChild(
                menuButton("{x} Variable", "Insert a variable, filled in when the message is sent", function (menu, close) {
                    var items = ctx.variables
                        .filter(function (v) {
                            return allowed.indexOf(v.name) !== -1;
                        })
                        .map(function (v) {
                            return { label: v.label, hint: "{" + v.name + "}", insert: "{" + v.name + "}" };
                        });
                    buildPickList(menu, [{ title: "Variables", items: items }], function (text) {
                        close();
                        insertText(ta, text);
                    });
                })
            );
        }
        insertGroup.appendChild(
            menuButton("@ Mention", "Mention a role or channel", function (menu, close) {
                buildPickList(
                    menu,
                    [
                        {
                            title: "Everyone",
                            items: [
                                { label: "@everyone", hint: "Pings all members", insert: "@everyone" },
                                { label: "@here", hint: "Pings online members", insert: "@here" },
                            ],
                        },
                        {
                            title: "Roles",
                            items: ctx.roles.map(function (r) {
                                return { label: "@" + r.name, hint: "<@&" + r.id + ">", insert: "<@&" + r.id + ">" };
                            }),
                        },
                        {
                            title: "Channels",
                            items: ctx.channels.map(function (c) {
                                return { label: "#" + c.name, hint: "<#" + c.id + ">", insert: "<#" + c.id + ">" };
                            }),
                        },
                    ],
                    function (text) {
                        close();
                        insertText(ta, text);
                    }
                );
            })
        );
        insertGroup.appendChild(
            menuButton("Time", "Timestamp - shown in each reader's own timezone", function (menu, close) {
                var when = el("input");
                when.type = "datetime-local";
                when.value = localDateTimeValue(new Date());
                var style = el("select");
                var sample = el("div", "msg-menu-empty", "");
                [
                    ["f", "Date and time"],
                    ["F", "Day, date and time"],
                    ["d", "Short date"],
                    ["D", "Long date"],
                    ["t", "Short time"],
                    ["T", "Long time"],
                    ["R", "Relative"],
                ].forEach(function (spec) {
                    var option = el("option", "", spec[1]);
                    option.value = spec[0];
                    style.appendChild(option);
                });
                function unix() {
                    var date = new Date(when.value);
                    return isNaN(date.getTime()) ? null : Math.floor(date.getTime() / 1000);
                }
                function update() {
                    var ts = unix();
                    sample.textContent = ts === null ? "Pick a date." : "Shows as: " + formatTimestamp(ts, style.value);
                }
                menuField(menu, "When", when);
                menuField(menu, "Format", style);
                menu.appendChild(sample);
                when.addEventListener("input", update);
                style.addEventListener("change", update);
                update();
                menuSubmit(menu, "Insert timestamp", function () {
                    var ts = unix();
                    if (ts === null) {
                        when.focus();
                        return;
                    }
                    close();
                    insertText(ta, "<t:" + ts + ":" + style.value + ">");
                });
            })
        );
        toolbar.appendChild(insertGroup);

        ta.addEventListener("keydown", function (event) {
            if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) {
                return;
            }
            var key = event.key.toLowerCase();
            var marks = { b: "**", i: "*", u: "__" };
            if (marks[key]) {
                event.preventDefault();
                toggleWrap(ta, marks[key], marks[key], "text");
            }
        });

        // --- Preview ---

        function render() {
            var max = Number(ta.getAttribute("maxlength")) || 0;
            counter.textContent = ta.value.length + (max ? " / " + max : "");
            counter.classList.toggle("near-limit", max > 0 && ta.value.length > max * 0.9);

            var unknown = [];
            function prep(text) {
                return allowed ? substitute(text, allowed, unknown) : text;
            }

            var own = ta.value.trim();
            var usingDefault = !own && !!ta.dataset.default;
            var body = own || ta.dataset.default || "";
            var asEmbed = ta.hasAttribute("data-embed-always") || (embedToggle && embedToggle.checked);
            var note = "";

            if (asEmbed && embedOverride && embedOverride.value.trim()) {
                note = "Not sent while the embed description is set - that's used as the embed's body instead.";
            } else if (asEmbed && !own && embedFallback) {
                body = embedFallback.value.trim() || embedFallback.dataset.default || "";
                note = "Blank, so the message template above is used as the embed's body.";
            } else if (usingDefault) {
                note = "Blank, so the built-in default is sent.";
            }

            previewPane.textContent = "";
            previewPane.appendChild(el("div", "msg-preview-label", ta.dataset.previewLabel || "Preview"));

            var message = el("div", "msg-message");
            var author = el("div", "msg-author");
            author.appendChild(el("span", "msg-author-name", "C3P0"));
            author.appendChild(el("span", "msg-bot-tag", "BOT"));
            author.appendChild(
                el("span", "msg-time", "Today at " + new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }))
            );
            message.appendChild(author);

            if (asEmbed) {
                var embed = el("div", "msg-embed");
                var title = embedTitle ? embedTitle.value.trim() : "";
                if (title) {
                    var titleEl = el("div", "msg-embed-title");
                    titleEl.innerHTML = renderMarkdown(prep(title), { inlineOnly: true, plainLinks: true });
                    embed.appendChild(titleEl);
                }
                if (body) {
                    var desc = el("div", "msg-embed-body md");
                    desc.innerHTML = renderMarkdown(prep(body), {});
                    embed.appendChild(desc);
                }
                var footer = embedFooter ? embedFooter.value.trim() : "";
                if (footer) {
                    // Discord doesn't format markdown in an embed footer.
                    embed.appendChild(el("div", "msg-embed-footer", prep(footer)));
                }
                if (!embed.childNodes.length) {
                    embed.appendChild(el("div", "msg-empty", "Empty embed."));
                }
                message.appendChild(embed);
            } else if (body) {
                var content = el("div", "msg-content md");
                content.innerHTML = renderMarkdown(prep(body), {});
                message.appendChild(content);
            } else {
                message.appendChild(el("div", "msg-empty", "Nothing to preview yet."));
            }
            previewPane.appendChild(message);

            if (note) {
                previewPane.appendChild(el("p", "msg-note", note));
            }
            if (unknown.length) {
                previewPane.appendChild(
                    el("p", "msg-note msg-warning", "Unknown variable" + (unknown.length > 1 ? "s" : "") + ": " + unknown.join(", ") + " - saving will be rejected.")
                );
            }
        }

        previewPane.addEventListener("click", function (event) {
            var spoiler = event.target.closest(".md-spoiler");
            if (spoiler) {
                spoiler.classList.toggle("revealed");
            }
        });
        previewPane.addEventListener("keydown", function (event) {
            if ((event.key === "Enter" || event.key === " ") && event.target.classList.contains("md-spoiler")) {
                event.preventDefault();
                event.target.classList.toggle("revealed");
            }
        });

        ta.addEventListener("input", render);
        if (ta.form) {
            // [ DISCARD ] is a reset button; values change after the event.
            ta.form.addEventListener("reset", function () { setTimeout(render, 0); });
        }
        [embedToggle, embedTitle, embedFooter, embedFallback, embedOverride].forEach(function (source) {
            if (source) {
                source.addEventListener("input", render);
                source.addEventListener("change", render);
            }
        });
        render();
    }

    document.querySelectorAll("textarea[data-message-editor]").forEach(enhance);
})();
