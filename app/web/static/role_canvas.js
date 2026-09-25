// Drag-and-drop reorder for the editable portion of the roles list -
// stage-then-apply, same UX as channel_canvas.js, but much smaller: this
// only ever reorders. Create/edit/delete/permissions stay on their own
// existing pages, untouched by this script. Positions themselves are
// never computed here - the draft only ever tracks an *order* of role
// ids, and the server (resolve_role_reorder in app/web/
// server_management.py) turns that into real Discord position numbers by
// permuting the positions this guild's editable roles already hold. That
// split is deliberate: it's what makes it structurally impossible for a
// drag here to push a role above C3P0's own role or a managed role,
// without this script needing to know anything about where that boundary
// is.
//
// Vanilla, ES5-ish style, matching channel_canvas.js/music.js/confirm.js.
(function () {
    "use strict";

    var listEl = document.getElementById("role-canvas-list");
    if (!listEl) {
        return; // loaded on every server-management page; no-op off the roles list
    }

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

    var initialRoles = readJSON("role-canvas-initial-roles") || [];
    var rolesById = {};
    initialRoles.forEach(function (role) { rolesById[role.id] = role; });

    var order = initialRoles.map(function (role) { return role.id; });
    var initialOrder = order.slice();
    var hasChanges = false;
    var dragState = null;

    function updateStatus() {
        var statusEl = document.getElementById("role-canvas-status");
        if (!statusEl) {
            return;
        }
        if (hasChanges) {
            statusEl.textContent = "Unsaved order - click Apply to send it to Discord.";
            statusEl.classList.add("is-dirty");
        } else {
            statusEl.textContent = "No unsaved changes.";
            statusEl.classList.remove("is-dirty");
        }
    }

    function markDirty() {
        hasChanges = true;
        updateStatus();
    }

    function render() {
        listEl.innerHTML = "";
        order.forEach(function (roleId) {
            var role = rolesById[roleId];
            var li = document.createElement("li");
            li.className = "item-row hud-frame role-canvas-row";
            li.draggable = true;
            li.dataset.roleId = roleId;

            var handle = document.createElement("span");
            handle.className = "drag-handle";
            handle.textContent = "⋮⋮";
            handle.title = "Drag to reorder";
            li.appendChild(handle);

            var main = document.createElement("div");
            main.className = "item-main";
            var title = document.createElement("span");
            title.className = "item-title";
            title.textContent = role.name;
            main.appendChild(title);
            li.appendChild(main);

            var actions = document.createElement("div");
            actions.className = "item-actions";

            var editLink = document.createElement("a");
            editLink.className = "button secondary";
            editLink.href = "/guilds/" + listEl.dataset.guildId + "/server-management/roles/" + roleId + "/edit";
            editLink.textContent = "[E]DIT";
            actions.appendChild(editLink);

            var deleteForm = document.createElement("form");
            deleteForm.method = "post";
            deleteForm.action = "/guilds/" + listEl.dataset.guildId + "/server-management/roles/" + roleId + "/delete";
            deleteForm.dataset.confirm = "rm @" + role.name + " \u00B7 THIS CANNOT BE UNDONE. PROCEED?";
            deleteForm.dataset.ok = "role @" + role.name + " deleted";
            var csrfInput = document.createElement("input");
            csrfInput.type = "hidden";
            csrfInput.name = "csrf_token";
            csrfInput.value = listEl.dataset.csrfToken;
            deleteForm.appendChild(csrfInput);
            var deleteBtn = document.createElement("button");
            deleteBtn.type = "submit";
            deleteBtn.className = "danger";
            deleteBtn.textContent = "[DEL]";
            deleteForm.appendChild(deleteBtn);
            actions.appendChild(deleteForm);

            li.appendChild(actions);
            listEl.appendChild(li);
        });
        updateStatus();
    }

    function findDropIndex(clientY, excludeRoleId) {
        var rows = [];
        for (var i = 0; i < listEl.children.length; i++) {
            var el = listEl.children[i];
            if (el.dataset.roleId !== excludeRoleId) {
                rows.push(el);
            }
        }
        for (var j = 0; j < rows.length; j++) {
            var rect = rows[j].getBoundingClientRect();
            if (clientY < rect.top + rect.height / 2) {
                return j;
            }
        }
        return rows.length;
    }

    listEl.addEventListener("dragstart", function (event) {
        var el = event.target.closest("[data-role-id]");
        if (!el) {
            return;
        }
        dragState = { roleId: el.dataset.roleId };
        el.classList.add("is-dragging");
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = "move";
            try {
                event.dataTransfer.setData("text/plain", el.dataset.roleId);
            } catch (err) {
                // Some browsers restrict setData outside a real drag gesture in tests; harmless to skip.
            }
        }
    });

    listEl.addEventListener("dragend", function () {
        var dragging = listEl.querySelectorAll(".is-dragging");
        for (var i = 0; i < dragging.length; i++) {
            dragging[i].classList.remove("is-dragging");
        }
        listEl.classList.remove("is-drop-target");
        dragState = null;
    });

    listEl.addEventListener("dragover", function (event) {
        if (!dragState) {
            return;
        }
        event.preventDefault();
        listEl.classList.add("is-drop-target");
    });

    listEl.addEventListener("dragleave", function (event) {
        if (event.target === listEl) {
            listEl.classList.remove("is-drop-target");
        }
    });

    listEl.addEventListener("drop", function (event) {
        if (!dragState) {
            return;
        }
        event.preventDefault();
        listEl.classList.remove("is-drop-target");

        var roleId = dragState.roleId;
        var fromIndex = order.indexOf(roleId);
        if (fromIndex === -1) {
            dragState = null;
            return;
        }
        var toIndex = findDropIndex(event.clientY, roleId);
        order.splice(fromIndex, 1);
        order.splice(toIndex, 0, roleId);
        dragState = null;
        markDirty();
        render();
    });

    var discardBtn = document.getElementById("role-canvas-discard");
    if (discardBtn) {
        discardBtn.addEventListener("click", function () {
            if (!hasChanges) {
                return;
            }
            var ask = window.C3P0 && window.C3P0.confirm
                ? window.C3P0.confirm("discard role order \u00B7 UNSAVED DRAG CHANGES ARE LOST. PROCEED?")
                : Promise.resolve(window.confirm("Discard the unsaved role order?"));
            ask.then(function (yes) {
                if (!yes) {
                    return;
                }
                order = initialOrder.slice();
                hasChanges = false;
                render();
            });
        });
    }

    var applyBtn = document.getElementById("role-canvas-apply-button");
    if (applyBtn) {
        applyBtn.addEventListener("click", function () {
            if (!hasChanges) {
                if (window.C3P0 && window.C3P0.print) {
                    window.C3P0.print("SYS", "no changes to apply");
                } else {
                    window.alert("No changes to apply.");
                }
                return;
            }
            document.getElementById("role-canvas-order-field").value = JSON.stringify(order);
            hasChanges = false; // navigating away regardless of outcome
            document.getElementById("role-canvas-apply-form").submit();
        });
    }

    window.addEventListener("beforeunload", function (event) {
        if (hasChanges) {
            event.preventDefault();
            event.returnValue = "";
        }
    });

    render();
})();
