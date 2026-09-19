// Confirmation-before-submit for destructive forms. Vanilla, no framework,
// no build step - matching music.js's precedent as this dashboard's only
// other client-side script.
//
// A `data-confirm="..."` attribute is used (not an inline
// onsubmit="confirm('...')") because the message routinely embeds
// arbitrary user text (a role/channel/template name), which can contain
// quotes or apostrophes. Jinja's normal HTML-attribute autoescaping makes
// that safe here; splicing the same text into an inline JS string would
// not be escaped the same way and could break the script outright.
(function () {
    "use strict";
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (form instanceof HTMLFormElement && form.dataset.confirm) {
            if (!window.confirm(form.dataset.confirm)) {
                event.preventDefault();
            }
        }
    });
})();
