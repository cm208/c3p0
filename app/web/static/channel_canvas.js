// Drag-and-drop channel/category canvas - stage-then-apply. Every drag,
// rename, permission change, create, and delete below only mutates an
// in-memory draft; nothing reaches Discord until "Apply" serializes the
// draft into a batch (see parse_channel_canvas_batch/
// apply_channel_canvas_batch in app/web/server_management.py) and submits
// it as a real form POST (so the server's rendered result page loads via
// a normal navigation, matching every other page on this dashboard,
// rather than needing a fetch()+replace dance).
//
// Vanilla, no framework, no build step - matching confirm.js/music.js's
// precedent as this dashboard's only other client-side scripts. Written
// in the same ES5-ish style (var, no template literals/arrow functions)
// as those two for consistency, not because a build step couldn't handle
// newer syntax.
//
// Model: nodesByKey (refKey -> node), rootOrder (top-level item keys -
// categories and unparented channels interleaved, matching how Discord's
// own client actually renders them), childrenByParent (category key ->
// ordered channel keys). A node's ref is either {kind:"existing", id:
// <real Discord id>} or {kind:"temp", id:<client-generated string>} for
// something created in this draft and not yet sent to Discord.
(function () {
    "use strict";

    var rootEl = document.getElementById("canvas-root");
    if (!rootEl) {
        return; // this script is loaded on every server-management page; no-op off the canvas page
    }

    var CATEGORY_TYPE = 4;
    var VOICE_TYPE = 2;
    var TEXT_TYPE = 0;

    function readJSON(id) {
        var el = document.getElementById(id);
        if (!el) {
            return null;
        }
        try {
            return JSON.parse(el.textContent);
        } catch (err) {
            return null;
        }
    }

    var initialChannels = readJSON("canvas-initial-channels") || [];
    var rolesData = readJSON("canvas-initial-roles") || [];
    var overwritePermissions = readJSON("canvas-overwrite-permissions") || [];
    var permissionBitValues = readJSON("canvas-permission-bit-values") || {};
    var operatorPermissions = readJSON("canvas-operator-permissions") || [];
    // Every Discord id in this file is kept as a string, never parsed with
    // parseInt/Number - snowflakes regularly exceed 2**53, past float64's
    // safe integer range, and two ids differing only in their low bits
    // (channels created close together in time, which is common) can
    // otherwise round to the same value once parsed as a JS Number. The
    // server's batch parser (parse_channel_canvas_batch) already accepts a
    // numeric string exactly as happily as a number, so nothing downstream
    // needs these as numbers.
    var everyoneRoleId = rootEl.dataset.everyoneRoleId;

    rootEl.dataset.dropzone = "true";
    rootEl.dataset.parentKey = "";

    var state = null;
    var tempCounter = 0;
    var dragState = null;
    var contextMenuEl = null;
    var modalBackdropEl = null;
    var hasChanges = false;

    function refKey(ref) {
        return ref.kind + ":" + ref.id;
    }

    function refForBatch(ref) {
        if (!ref) {
            return null;
        }
        return { kind: ref.kind, id: ref.id };
    }

    function makeNodeFromChannel(channel) {
        return {
            ref: { kind: "existing", id: channel.id },
            type: channel.type,
            name: channel.name,
            topic: channel.topic,
            nsfw: channel.nsfw,
            rate_limit_per_user: channel.rate_limit_per_user,
            bitrate: channel.bitrate,
            user_limit: channel.user_limit,
            overwrites: (channel.overwrites || []).map(function (o) {
                return { role_id: o.role_id, allow: o.allow, deny: o.deny };
            }),
            parentRef: channel.parent_id ? { kind: "existing", id: channel.parent_id } : null,
            deleted: false,
            dirty: false,
        };
    }

    function buildState() {
        var nodesByKey = {};
        var rootOrder = [];
        var childrenByParent = {};

        var categories = initialChannels
            .filter(function (c) { return c.type === CATEGORY_TYPE; })
            .sort(function (a, b) { return a.position - b.position; });
        var others = initialChannels
            .filter(function (c) { return c.type !== CATEGORY_TYPE; })
            .sort(function (a, b) { return a.position - b.position; });

        others
            .filter(function (c) { return !c.parent_id; })
            .forEach(function (c) {
                var node = makeNodeFromChannel(c);
                var key = refKey(node.ref);
                nodesByKey[key] = node;
                rootOrder.push(key);
            });

        categories.forEach(function (c) {
            var node = makeNodeFromChannel(c);
            var key = refKey(node.ref);
            nodesByKey[key] = node;
            rootOrder.push(key);
            childrenByParent[key] = [];
        });

        others
            .filter(function (c) { return c.parent_id; })
            .forEach(function (c) {
                var node = makeNodeFromChannel(c);
                var key = refKey(node.ref);
                nodesByKey[key] = node;
                var parentKey = refKey(node.parentRef);
                if (!childrenByParent[parentKey]) {
                    // Its category wasn't in this fetch - fall back to root
                    // rather than silently dropping the channel from view.
                    node.parentRef = null;
                    rootOrder.push(key);
                } else {
                    childrenByParent[parentKey].push(key);
                }
            });

        return { nodesByKey: nodesByKey, rootOrder: rootOrder, childrenByParent: childrenByParent };
    }

    function removeFromCurrentParent(key) {
        var node = state.nodesByKey[key];
        if (node.type === CATEGORY_TYPE) {
            var idx = state.rootOrder.indexOf(key);
            if (idx !== -1) {
                state.rootOrder.splice(idx, 1);
            }
            return;
        }
        if (node.parentRef) {
            var list = state.childrenByParent[refKey(node.parentRef)] || [];
            var i = list.indexOf(key);
            if (i !== -1) {
                list.splice(i, 1);
            }
        } else {
            var i2 = state.rootOrder.indexOf(key);
            if (i2 !== -1) {
                state.rootOrder.splice(i2, 1);
            }
        }
    }

    function insertIntoParent(key, parentKey, index) {
        var node = state.nodesByKey[key];
        if (!parentKey) {
            node.parentRef = null;
            state.rootOrder.splice(index, 0, key);
        } else {
            node.parentRef = state.nodesByKey[parentKey].ref;
            if (!state.childrenByParent[parentKey]) {
                state.childrenByParent[parentKey] = [];
            }
            state.childrenByParent[parentKey].splice(index, 0, key);
        }
    }

    function markDirty() {
        hasChanges = true;
        updateStatus();
    }

    function updateStatus() {
        var statusEl = document.getElementById("canvas-status");
        if (!statusEl) {
            return;
        }
        if (hasChanges) {
            statusEl.textContent = "Unsaved changes - click Apply to send them to Discord.";
            statusEl.classList.add("is-dirty");
        } else {
            statusEl.textContent = "No unsaved changes.";
            statusEl.classList.remove("is-dirty");
        }
    }

    // --- Rendering ---

    function typeIcon(type) {
        return type === VOICE_TYPE ? "🔊" : "#";
    }

    function renderChannelRow(key, node) {
        var li = document.createElement("li");
        li.className = "canvas-channel-row";
        li.draggable = true;
        li.dataset.key = key;

        var icon = document.createElement("span");
        icon.className = "canvas-type-icon";
        icon.textContent = typeIcon(node.type);
        li.appendChild(icon);

        var name = document.createElement("span");
        name.className = "canvas-name";
        name.textContent = node.name;
        name.tabIndex = 0;
        name.addEventListener("click", function () { startRename(key, name); });
        name.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                startRename(key, name);
            }
        });
        li.appendChild(name);

        if (node.overwrites.length) {
            var badge = document.createElement("span");
            badge.className = "badge partial";
            badge.textContent = node.overwrites.length + " overwrite" + (node.overwrites.length !== 1 ? "s" : "");
            li.appendChild(badge);
        }

        addMenuTrigger(li, key);

        li.addEventListener("contextmenu", function (event) {
            event.preventDefault();
            showContextMenuAt(event.clientX, event.clientY, key);
        });

        return li;
    }

    function renderCategory(key, node) {
        var wrapper = document.createElement("div");
        wrapper.className = "canvas-category hud-frame";
        wrapper.draggable = true;
        wrapper.dataset.key = key;

        var header = document.createElement("div");
        header.className = "canvas-category-header";

        var name = document.createElement("span");
        name.className = "canvas-name";
        name.textContent = node.name;
        name.tabIndex = 0;
        name.addEventListener("click", function (event) {
            event.stopPropagation();
            startRename(key, name);
        });
        name.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                startRename(key, name);
            }
        });
        header.appendChild(name);

        addMenuTrigger(header, key);

        header.addEventListener("contextmenu", function (event) {
            event.preventDefault();
            showContextMenuAt(event.clientX, event.clientY, key);
        });

        wrapper.appendChild(header);

        var list = document.createElement("ul");
        list.className = "canvas-channel-list";
        list.dataset.dropzone = "true";
        list.dataset.parentKey = key;
        (state.childrenByParent[key] || []).forEach(function (childKey) {
            list.appendChild(renderChannelRow(childKey, state.nodesByKey[childKey]));
        });
        wrapper.appendChild(list);

        wrapper.appendChild(buildAddControl(key));

        return wrapper;
    }

    function render() {
        rootEl.innerHTML = "";
        state.rootOrder.forEach(function (key) {
            var node = state.nodesByKey[key];
            if (node.type === CATEGORY_TYPE) {
                rootEl.appendChild(renderCategory(key, node));
            } else {
                rootEl.appendChild(renderChannelRow(key, node));
            }
        });
        updateStatus();
    }

    // --- Menu trigger (keyboard/touch-friendly alternative to right-click) ---

    function addMenuTrigger(container, key) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "secondary";
        btn.textContent = "⋯";
        btn.title = "Rename, adjust permissions, or delete";
        btn.addEventListener("click", function (event) {
            event.stopPropagation();
            var rect = btn.getBoundingClientRect();
            showContextMenuAt(rect.left, rect.bottom + 4, key);
        });
        container.appendChild(btn);
    }

    // --- Context menu ---

    function dismissContextMenu() {
        if (contextMenuEl) {
            contextMenuEl.remove();
            contextMenuEl = null;
        }
    }

    function addMenuItem(menu, label, handler, isDanger) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = label;
        if (isDanger) {
            btn.className = "danger";
        }
        btn.addEventListener("click", handler);
        menu.appendChild(btn);
    }

    function showContextMenuAt(x, y, key) {
        dismissContextMenu();
        var menu = document.createElement("div");
        menu.className = "canvas-context-menu";
        menu.style.left = x + "px";
        menu.style.top = y + "px";

        addMenuItem(menu, "Rename", function () {
            dismissContextMenu();
            var el = rootEl.querySelector('[data-key="' + key + '"] .canvas-name');
            if (el) {
                startRename(key, el);
            }
        });
        addMenuItem(menu, "Adjust Permissions", function () {
            dismissContextMenu();
            openPermissionsModal(key);
        });
        addMenuItem(menu, "Delete", function () {
            dismissContextMenu();
            deleteNode(key);
        }, true);

        document.body.appendChild(menu);
        contextMenuEl = menu;

        // Keep the menu on-screen if it would overflow the viewport edge.
        var rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth) {
            menu.style.left = Math.max(4, window.innerWidth - rect.width - 4) + "px";
        }
        if (rect.bottom > window.innerHeight) {
            menu.style.top = Math.max(4, window.innerHeight - rect.height - 4) + "px";
        }
    }

    document.addEventListener("click", function (event) {
        if (contextMenuEl && !contextMenuEl.contains(event.target)) {
            dismissContextMenu();
        }
        if (!event.target.closest(".canvas-add")) {
            var openAdds = document.querySelectorAll(".canvas-add.is-open");
            for (var i = 0; i < openAdds.length; i++) {
                openAdds[i].classList.remove("is-open");
            }
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            dismissContextMenu();
            dismissModal();
        }
    });

    // --- Rename ---

    function startRename(key, nameEl) {
        var node = state.nodesByKey[key];
        var input = document.createElement("input");
        input.type = "text";
        input.className = "canvas-rename-input";
        input.value = node.name;
        input.maxLength = 100;
        nameEl.replaceWith(input);
        input.focus();
        input.select();

        var committed = false;
        function commit() {
            if (committed) {
                return;
            }
            committed = true;
            var value = input.value.trim();
            if (value && value !== node.name) {
                node.name = value;
                node.dirty = true;
                markDirty();
            }
            render();
        }

        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                commit();
            } else if (event.key === "Escape") {
                event.preventDefault();
                committed = true;
                render();
            }
        });
        input.addEventListener("blur", commit);
    }

    // --- Delete ---

    function deleteNode(key) {
        var node = state.nodesByKey[key];
        var confirmMessage = 'Remove "' + node.name + '" from the draft? This only changes the draft until you Apply'
            + (node.type === CATEGORY_TYPE ? " - its channels move up to the top level, they aren't deleted too." : ".");
        if (!window.confirm(confirmMessage)) {
            return;
        }

        if (node.type === CATEGORY_TYPE) {
            var children = (state.childrenByParent[key] || []).slice();
            delete state.childrenByParent[key];
            var idx = state.rootOrder.indexOf(key);
            state.rootOrder.splice(idx, 1);
            children.forEach(function (childKey) { state.nodesByKey[childKey].parentRef = null; });
            var spliceArgs = [idx, 0].concat(children);
            Array.prototype.splice.apply(state.rootOrder, spliceArgs);
        } else {
            removeFromCurrentParent(key);
        }

        if (node.ref.kind === "existing") {
            node.deleted = true;
        } else {
            delete state.nodesByKey[key];
        }
        markDirty();
        render();
    }

    // --- Drag and drop ---

    function findDropIndex(container, clientY, excludeKey) {
        var children = container.children;
        var visible = [];
        for (var i = 0; i < children.length; i++) {
            var el = children[i];
            if (el.dataset && el.dataset.key && el.dataset.key !== excludeKey) {
                visible.push(el);
            }
        }
        for (var j = 0; j < visible.length; j++) {
            var rect = visible[j].getBoundingClientRect();
            if (clientY < rect.top + rect.height / 2) {
                return j;
            }
        }
        return visible.length;
    }

    document.addEventListener("dragstart", function (event) {
        var el = event.target.closest("[data-key]");
        if (!el) {
            return;
        }
        dragState = { key: el.dataset.key };
        el.classList.add("is-dragging");
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = "move";
            try {
                event.dataTransfer.setData("text/plain", el.dataset.key);
            } catch (err) {
                // Some browsers restrict setData outside a real drag gesture in tests; harmless to skip.
            }
        }
    });

    document.addEventListener("dragend", function () {
        var dragging = document.querySelectorAll(".is-dragging");
        for (var i = 0; i < dragging.length; i++) {
            dragging[i].classList.remove("is-dragging");
        }
        var targets = document.querySelectorAll(".is-drop-target");
        for (var j = 0; j < targets.length; j++) {
            targets[j].classList.remove("is-drop-target");
        }
        dragState = null;
    });

    document.addEventListener("dragover", function (event) {
        if (!dragState) {
            return;
        }
        var zone = event.target.closest("[data-dropzone]");
        if (!zone) {
            return;
        }
        var draggedNode = state.nodesByKey[dragState.key];
        if (!draggedNode) {
            return;
        }
        // A category can only be reordered among other top-level items,
        // never nested inside another category's channel list.
        if (draggedNode.type === CATEGORY_TYPE && zone !== rootEl) {
            return;
        }
        event.preventDefault();
        zone.classList.add("is-drop-target");
    });

    document.addEventListener("dragleave", function (event) {
        var zone = event.target.closest("[data-dropzone]");
        if (zone) {
            zone.classList.remove("is-drop-target");
        }
    });

    document.addEventListener("drop", function (event) {
        if (!dragState) {
            return;
        }
        var zone = event.target.closest("[data-dropzone]");
        if (!zone) {
            return;
        }
        var key = dragState.key;
        var draggedNode = state.nodesByKey[key];
        if (!draggedNode || (draggedNode.type === CATEGORY_TYPE && zone !== rootEl)) {
            dragState = null;
            return;
        }
        event.preventDefault();
        zone.classList.remove("is-drop-target");

        var parentKey = zone.dataset.parentKey || "";
        var index = findDropIndex(zone, event.clientY, key);
        removeFromCurrentParent(key);
        insertIntoParent(key, parentKey, index);
        dragState = null;
        markDirty();
        render();
    });

    // --- Add (create) ---

    function buildAddControl(parentKey) {
        var wrap = document.createElement("div");
        wrap.className = "canvas-add";

        var trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "canvas-add-trigger";
        trigger.textContent = "+";
        trigger.title = "Add a channel here";
        wrap.appendChild(trigger);

        var options = document.createElement("div");
        options.className = "canvas-add-options";
        [[TEXT_TYPE, "# Text"], [VOICE_TYPE, "🔊 Voice"]].forEach(function (pair) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = pair[1];
            btn.addEventListener("click", function (event) {
                event.stopPropagation();
                wrap.classList.remove("is-open");
                openCreateModal(pair[0], parentKey);
            });
            options.appendChild(btn);
        });
        wrap.appendChild(options);

        trigger.addEventListener("click", function (event) {
            event.stopPropagation();
            var wasOpen = wrap.classList.contains("is-open");
            var openAdds = document.querySelectorAll(".canvas-add.is-open");
            for (var i = 0; i < openAdds.length; i++) {
                openAdds[i].classList.remove("is-open");
            }
            if (!wasOpen) {
                wrap.classList.add("is-open");
            }
        });

        return wrap;
    }

    function openCreateModal(type, parentKey) {
        var typeLabel = type === VOICE_TYPE ? "Voice Channel" : "Text Channel";
        var nameInput, topicInput;
        var roleChecks = [];

        buildModal("New " + typeLabel, function (body) {
            var nameField = document.createElement("div");
            nameField.className = "field";
            var nameLabel = document.createElement("label");
            nameLabel.textContent = "Name";
            nameInput = document.createElement("input");
            nameInput.type = "text";
            nameInput.maxLength = 100;
            nameField.appendChild(nameLabel);
            nameField.appendChild(nameInput);
            body.appendChild(nameField);

            if (type === TEXT_TYPE) {
                var topicField = document.createElement("div");
                topicField.className = "field";
                var topicLabel = document.createElement("label");
                topicLabel.textContent = "Topic (optional)";
                topicInput = document.createElement("input");
                topicInput.type = "text";
                topicInput.maxLength = 1024;
                topicField.appendChild(topicLabel);
                topicField.appendChild(topicInput);
                body.appendChild(topicField);
            }

            var eyebrow = document.createElement("span");
            eyebrow.className = "eyebrow";
            eyebrow.textContent = "Restrict Visibility";
            body.appendChild(eyebrow);
            var hint = document.createElement("p");
            hint.className = "field-hint";
            hint.textContent = "Optional - denies @everyone View Channel and allows only the roles you pick.";
            body.appendChild(hint);

            if (!rolesData.length) {
                var empty = document.createElement("p");
                empty.className = "empty-state";
                empty.textContent = "No assignable roles to restrict to.";
                body.appendChild(empty);
            }
            rolesData.forEach(function (role) {
                var toggle = document.createElement("div");
                toggle.className = "field-toggle";
                var checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.id = "canvas-restrict-" + role.id;
                var label = document.createElement("label");
                label.setAttribute("for", checkbox.id);
                label.textContent = role.name;
                toggle.appendChild(checkbox);
                toggle.appendChild(label);
                body.appendChild(toggle);
                roleChecks.push({ role: role, checkbox: checkbox });
            });
        }, function onSave() {
            var name = nameInput.value.trim();
            if (!name) {
                window.alert("Name cannot be empty.");
                return false;
            }

            var overwrites = [];
            var restrictedRoleIds = roleChecks
                .filter(function (rc) { return rc.checkbox.checked; })
                .map(function (rc) { return rc.role.id; });
            if (restrictedRoleIds.length) {
                var viewBit = permissionBitValues.view_channel || 0;
                overwrites.push({ role_id: everyoneRoleId, allow: 0, deny: viewBit });
                restrictedRoleIds.forEach(function (rid) {
                    overwrites.push({ role_id: rid, allow: viewBit, deny: 0 });
                });
            }

            tempCounter += 1;
            var ref = { kind: "temp", id: "new-" + tempCounter };
            var key = refKey(ref);
            var node = {
                ref: ref,
                type: type,
                name: name,
                topic: topicInput ? (topicInput.value.trim() || null) : null,
                nsfw: false,
                rate_limit_per_user: 0,
                bitrate: null,
                user_limit: null,
                overwrites: overwrites,
                parentRef: parentKey ? state.nodesByKey[parentKey].ref : null,
                deleted: false,
                dirty: true,
            };
            state.nodesByKey[key] = node;
            if (parentKey) {
                state.childrenByParent[parentKey].push(key);
            } else {
                state.rootOrder.push(key);
            }
            markDirty();
            render();
            return true;
        });

        setTimeout(function () { nameInput.focus(); }, 0);
    }

    document.getElementById("canvas-root-add").addEventListener("click", function (event) {
        var wrap = event.currentTarget;
        var trigger = event.target.closest(".canvas-add-trigger");
        if (trigger) {
            event.stopPropagation();
            wrap.classList.toggle("is-open");
            return;
        }
        var addBtn = event.target.closest("[data-add-type]");
        if (addBtn) {
            wrap.classList.remove("is-open");
            var type = parseInt(addBtn.dataset.addType, 10);
            if (type === CATEGORY_TYPE) {
                openCreateCategoryModal();
            } else {
                openCreateModal(type, "");
            }
        }
    });

    function openCreateCategoryModal() {
        var nameInput;
        buildModal("New Category", function (body) {
            var field = document.createElement("div");
            field.className = "field";
            var label = document.createElement("label");
            label.textContent = "Name";
            nameInput = document.createElement("input");
            nameInput.type = "text";
            nameInput.maxLength = 100;
            field.appendChild(label);
            field.appendChild(nameInput);
            body.appendChild(field);
        }, function onSave() {
            var name = nameInput.value.trim();
            if (!name) {
                window.alert("Name cannot be empty.");
                return false;
            }
            tempCounter += 1;
            var ref = { kind: "temp", id: "new-" + tempCounter };
            var key = refKey(ref);
            state.nodesByKey[key] = {
                ref: ref, type: CATEGORY_TYPE, name: name, topic: null, nsfw: false,
                rate_limit_per_user: 0, bitrate: null, user_limit: null, overwrites: [],
                parentRef: null, deleted: false, dirty: true,
            };
            state.childrenByParent[key] = [];
            state.rootOrder.push(key);
            markDirty();
            render();
            return true;
        });
        setTimeout(function () { nameInput.focus(); }, 0);
    }

    // --- Adjust permissions ---

    function openPermissionsModal(key) {
        var node = state.nodesByKey[key];
        var selectEl, rowsContainer;
        var triStateByFlag = {};
        var pendingOverwrites = node.overwrites.map(function (o) {
            return { role_id: o.role_id, allow: o.allow, deny: o.deny };
        });

        function roleName(roleId) {
            if (roleId === everyoneRoleId) {
                return "@everyone";
            }
            for (var i = 0; i < rolesData.length; i++) {
                if (rolesData[i].id === roleId) {
                    return rolesData[i].name;
                }
            }
            return "role " + roleId;
        }

        function renderExistingRows() {
            rowsContainer.innerHTML = "";
            if (!pendingOverwrites.length) {
                var empty = document.createElement("p");
                empty.className = "empty-state";
                empty.textContent = "No permission overwrites yet.";
                rowsContainer.appendChild(empty);
                return;
            }
            pendingOverwrites.forEach(function (ow) {
                var row = document.createElement("div");
                row.className = "item-row hud-frame";
                var main = document.createElement("div");
                main.className = "item-main";
                main.textContent = roleName(ow.role_id);
                row.appendChild(main);
                var removeBtn = document.createElement("button");
                removeBtn.type = "button";
                removeBtn.className = "danger";
                removeBtn.textContent = "Remove";
                removeBtn.addEventListener("click", function () {
                    pendingOverwrites = pendingOverwrites.filter(function (o) { return o.role_id !== ow.role_id; });
                    renderExistingRows();
                });
                row.appendChild(removeBtn);
                rowsContainer.appendChild(row);
            });
        }

        function seedGridForRole(roleId) {
            var existing = null;
            for (var i = 0; i < pendingOverwrites.length; i++) {
                if (pendingOverwrites[i].role_id === roleId) {
                    existing = pendingOverwrites[i];
                    break;
                }
            }
            overwritePermissions.forEach(function (flag) {
                var bit = permissionBitValues[flag] || 0;
                var stateName = "neutral";
                if (existing && (existing.allow & bit)) {
                    stateName = "allow";
                } else if (existing && (existing.deny & bit)) {
                    stateName = "deny";
                }
                var group = triStateByFlag[flag];
                var buttons = group.querySelectorAll("button");
                for (var j = 0; j < buttons.length; j++) {
                    buttons[j].classList.toggle("is-active", buttons[j].dataset.state === stateName);
                }
            });
        }

        buildModal("Permissions: " + node.name, function (body) {
            var eyebrow1 = document.createElement("span");
            eyebrow1.className = "eyebrow";
            eyebrow1.textContent = "Current Overwrites";
            body.appendChild(eyebrow1);
            rowsContainer = document.createElement("div");
            body.appendChild(rowsContainer);
            renderExistingRows();

            var eyebrow2 = document.createElement("span");
            eyebrow2.className = "eyebrow";
            eyebrow2.textContent = "Add / Update a Role";
            body.appendChild(eyebrow2);

            var field = document.createElement("div");
            field.className = "field";
            var label = document.createElement("label");
            label.textContent = "Role";
            selectEl = document.createElement("select");
            var everyoneOpt = document.createElement("option");
            everyoneOpt.value = String(everyoneRoleId);
            everyoneOpt.textContent = "@everyone";
            selectEl.appendChild(everyoneOpt);
            rolesData.forEach(function (role) {
                var opt = document.createElement("option");
                opt.value = String(role.id);
                opt.textContent = role.name;
                selectEl.appendChild(opt);
            });
            field.appendChild(label);
            field.appendChild(selectEl);
            body.appendChild(field);

            var grid = document.createElement("div");
            grid.className = "canvas-overwrite-grid";
            overwritePermissions.forEach(function (flag) {
                var row = document.createElement("div");
                row.className = "canvas-overwrite-row";
                var flagLabel = document.createElement("span");
                flagLabel.textContent = flag.replace(/_/g, " ");
                row.appendChild(flagLabel);

                var triGroup = document.createElement("div");
                triGroup.className = "canvas-tri-state";
                var locked = operatorPermissions.indexOf(flag) === -1;
                ["neutral", "allow", "deny"].forEach(function (stateName) {
                    var btn = document.createElement("button");
                    btn.type = "button";
                    btn.textContent = stateName === "neutral" ? "Inherit" : (stateName === "allow" ? "Allow" : "Deny");
                    btn.dataset.state = stateName;
                    if (stateName === "allow" && locked) {
                        btn.disabled = true;
                        btn.title = "You don't hold this permission yourself.";
                    }
                    btn.addEventListener("click", function () {
                        var buttons = triGroup.querySelectorAll("button");
                        for (var k = 0; k < buttons.length; k++) {
                            buttons[k].classList.remove("is-active");
                        }
                        btn.classList.add("is-active");
                    });
                    triGroup.appendChild(btn);
                });
                triStateByFlag[flag] = triGroup;
                row.appendChild(triGroup);
                grid.appendChild(row);
            });
            body.appendChild(grid);

            selectEl.addEventListener("change", function () { seedGridForRole(selectEl.value); });
            seedGridForRole(selectEl.value);

            var addBtn = document.createElement("button");
            addBtn.type = "button";
            addBtn.className = "secondary";
            addBtn.textContent = "Set This Role's Overwrite";
            addBtn.addEventListener("click", function () {
                var roleId = selectEl.value;
                var allow = 0;
                var deny = 0;
                overwritePermissions.forEach(function (flag) {
                    var bit = permissionBitValues[flag] || 0;
                    var active = triStateByFlag[flag].querySelector(".is-active");
                    var stateName = active ? active.dataset.state : "neutral";
                    if (stateName === "allow") {
                        allow |= bit;
                    } else if (stateName === "deny") {
                        deny |= bit;
                    }
                });
                pendingOverwrites = pendingOverwrites.filter(function (o) { return o.role_id !== roleId; });
                if (allow || deny) {
                    pendingOverwrites.push({ role_id: roleId, allow: allow, deny: deny });
                }
                renderExistingRows();
            });
            body.appendChild(addBtn);
        }, function onSave() {
            node.overwrites = pendingOverwrites;
            node.dirty = true;
            markDirty();
            render();
            return true;
        });
    }

    // --- Modal shell ---

    function dismissModal() {
        if (modalBackdropEl) {
            modalBackdropEl.remove();
            modalBackdropEl = null;
        }
    }

    function buildModal(titleText, bodyBuilder, onSave) {
        dismissModal();
        var backdrop = document.createElement("div");
        backdrop.className = "canvas-modal-backdrop";
        backdrop.addEventListener("click", function (event) {
            if (event.target === backdrop) {
                dismissModal();
            }
        });

        var modal = document.createElement("div");
        modal.className = "canvas-modal hud-frame";
        modal.addEventListener("click", function (event) { event.stopPropagation(); });

        var h2 = document.createElement("h2");
        h2.textContent = titleText;
        modal.appendChild(h2);

        var body = document.createElement("div");
        modal.appendChild(body);
        bodyBuilder(body);

        var actions = document.createElement("div");
        actions.className = "canvas-modal-actions";
        var cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.className = "secondary";
        cancelBtn.textContent = "Cancel";
        cancelBtn.addEventListener("click", dismissModal);
        actions.appendChild(cancelBtn);

        var saveBtn = document.createElement("button");
        saveBtn.type = "button";
        saveBtn.textContent = "Save";
        saveBtn.addEventListener("click", function () {
            if (onSave()) {
                dismissModal();
            }
        });
        actions.appendChild(saveBtn);
        modal.appendChild(actions);

        backdrop.appendChild(modal);
        document.body.appendChild(backdrop);
        modalBackdropEl = backdrop;
    }

    // --- Apply / discard ---

    function computeBatch() {
        var channels = [];
        var positions = [];

        Object.keys(state.nodesByKey).forEach(function (key) {
            var node = state.nodesByKey[key];
            if (node.ref.kind === "existing" && node.deleted) {
                channels.push({ op: "delete", id: node.ref.id });
                return;
            }
            if (node.ref.kind === "temp") {
                channels.push({
                    op: "create", temp_id: node.ref.id, type: node.type, name: node.name,
                    parent: refForBatch(node.parentRef), topic: node.topic, nsfw: node.nsfw,
                    rate_limit_per_user: node.rate_limit_per_user, bitrate: node.bitrate,
                    user_limit: node.user_limit, overwrites: node.overwrites,
                });
            } else if (node.dirty) {
                channels.push({
                    op: "edit", id: node.ref.id, name: node.name, parent: refForBatch(node.parentRef),
                    topic: node.topic, nsfw: node.nsfw, rate_limit_per_user: node.rate_limit_per_user,
                    bitrate: node.bitrate, user_limit: node.user_limit, overwrites: node.overwrites,
                });
            }
        });

        // Every surviving node's position, in its current draft order -
        // categories and root channels share one sequence at the root,
        // each category's children share their own. This is a first-pass
        // approximation of Discord's own (fiddlier, per-type) position
        // model, not a byte-for-byte reproduction of it.
        state.rootOrder.forEach(function (key, index) {
            positions.push({ ref: refForBatch(state.nodesByKey[key].ref), position: index, parent: null });
        });
        Object.keys(state.childrenByParent).forEach(function (parentKey) {
            state.childrenByParent[parentKey].forEach(function (key, index) {
                positions.push({
                    ref: refForBatch(state.nodesByKey[key].ref), position: index,
                    parent: refForBatch(state.nodesByKey[parentKey].ref),
                });
            });
        });

        return { channels: channels, positions: positions };
    }

    window.addEventListener("beforeunload", function (event) {
        if (hasChanges) {
            event.preventDefault();
            event.returnValue = "";
        }
    });

    var discardBtn = document.getElementById("canvas-discard");
    if (discardBtn) {
        discardBtn.addEventListener("click", function () {
            if (hasChanges && !window.confirm("Discard all unsaved changes in this draft?")) {
                return;
            }
            state = buildState();
            hasChanges = false;
            render();
        });
    }

    var applyBtn = document.getElementById("canvas-apply-button");
    if (applyBtn) {
        applyBtn.addEventListener("click", function () {
            var batch = computeBatch();
            if (!batch.channels.length && !batch.positions.length) {
                window.alert("No changes to apply.");
                return;
            }
            document.getElementById("canvas-batch-field").value = JSON.stringify(batch);
            hasChanges = false; // navigating away regardless of outcome
            document.getElementById("canvas-apply-form").submit();
        });
    }

    state = buildState();
    render();
})();
