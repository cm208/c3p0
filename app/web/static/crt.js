// CRT tube effects: power-on (first load of a session, and after logout),
// the warm-up flash on every page, and the operator's phosphor / CRT
// intensity preferences. The overlays themselves are static CSS; this only
// toggles them. base.html's inline head script applies stored preferences
// before first paint - this file owns changing them.
(function () {
    "use strict";

    var C3P0 = (window.C3P0 = window.C3P0 || {});
    var root = document.documentElement;
    var PHOSPHORS = { amber: "#ffa31f", sodium: "#ff7a1f", green: "#52ff8a" };
    var LEVELS = ["off", "normal", "max"];
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function store(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (e) { /* storage blocked: applies for this page only */ }
    }

    function session(key, value) {
        try {
            if (value === undefined) {
                return sessionStorage.getItem(key);
            }
            sessionStorage.setItem(key, value);
        } catch (e) {
            return null;
        }
        return null;
    }

    C3P0.crt = {
        setPhosphor: function (name) {
            if (!PHOSPHORS[name]) {
                return false;
            }
            root.style.setProperty("--p", PHOSPHORS[name]);
            store("c3p0.phosphor", name);
            // Canvases read --p per frame, so the music visualisers follow.
            return true;
        },
        setLevel: function (level) {
            if (LEVELS.indexOf(level) === -1) {
                return false;
            }
            root.dataset.crt = level;
            store("c3p0.crt", level);
            return true;
        },
        phosphorColor: function () {
            return getComputedStyle(root).getPropertyValue("--p").trim() || PHOSPHORS.amber;
        },
    };

    var power = document.querySelector(".crt-power");
    var warm = document.querySelector(".crt-warm");

    if (!reduceMotion && !session("c3p0.powered")) {
        power.classList.add("is-on");
    } else if (!reduceMotion && warm) {
        warm.classList.add("is-on");
    }
    session("c3p0.powered", "1");
})();
