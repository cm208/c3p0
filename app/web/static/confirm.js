// Confirm-before-submit for destructive forms, routed through the console's
// (y/N) prompt: `<form data-confirm="rm #general · THIS CANNOT BE UNDONE.
// PROCEED?">`. Only y/yes submits.
//
// A data-confirm attribute (not an inline onsubmit="confirm('...')")
// because the text routinely embeds user content (a role/channel/template
// name) with quotes in it: Jinja's attribute autoescaping keeps that safe,
// splicing it into an inline JS string would not.
//
// Without JS the form simply submits, as it always did; the server-side
// routes stay the source of truth for what's allowed.
(function () {
    "use strict";

    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement) || !form.dataset.confirm || form.dataset.confirmed === "yes") {
            return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();

        var ask = window.C3P0 && window.C3P0.confirm
            ? window.C3P0.confirm(form.dataset.confirm)
            : Promise.resolve(window.confirm(form.dataset.confirm));

        ask.then(function (yes) {
            if (!yes) {
                return;
            }
            // requestSubmit (not submit) so the capture-phase listener that
            // records the [OK] line for the next page still sees it.
            form.dataset.confirmed = "yes";
            if (form.requestSubmit) {
                form.requestSubmit();
            } else {
                form.submit();
            }
        });
    }, true);
})();
