/* Phase 11: focus session UI -- progress ring, depth polling, zen mode.
 * Vanilla JS, no libraries. The server renders initial values into
 * data-* attributes; this file animates and refreshes them.
 */
(function () {
    "use strict";

    /* --- Circular progress ring -------------------------------------- */
    function initRings() {
        document.querySelectorAll("[data-fc-ring]").forEach(function (el) {
            var total = parseFloat(el.getAttribute("data-total") || "1");
            var remaining = parseFloat(
                el.getAttribute("data-remaining") || "0");
            var circle = el.querySelector(".fc-ring-fg");
            if (!circle) return;
            var r = parseFloat(circle.getAttribute("r") || "54");
            var circ = 2 * Math.PI * r;
            circle.style.strokeDasharray = String(circ);
            var target = circ * (1 - Math.max(0, Math.min(1,
                remaining / total)));
            // Animate smoothly from full to target on load.
            circle.style.strokeDashoffset = "0";
            requestAnimationFrame(function () {
                circle.style.transition =
                    "stroke-dashoffset 1.2s ease-out";
                circle.style.strokeDashoffset = String(target);
            });
            // Countdown ticker: tick once per second, update ring + label.
            var label = el.querySelector(".fc-ring-label");
            var endAt = Date.now() + remaining * 1000;
            var mode = el.getAttribute("data-mode") || "remaining";
            var tick = setInterval(function () {
                var left = Math.max(0, (endAt - Date.now()) / 1000);
                if (mode === "remaining") {
                    circle.style.transition = "stroke-dashoffset 1s linear";
                    circle.style.strokeDashoffset = String(
                        circ * (1 - left / total));
                    if (label) label.textContent = fmt(left);
                    if (left <= 0) clearInterval(tick);
                } else {
                    // elapsed mode (flowtime): ring fills up instead.
                    var elapsed = total - left;
                    circle.style.transition = "stroke-dashoffset 1s linear";
                    circle.style.strokeDashoffset = String(
                        circ * Math.max(0, 1 - elapsed / total));
                    if (label) label.textContent = fmt(elapsed) + " elapsed";
                }
            }, 1000);
        });
    }

    function fmt(sec) {
        sec = Math.max(0, Math.floor(sec));
        var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60),
            s = sec % 60;
        function p(n) { return (n < 10 ? "0" : "") + n; }
        return h > 0 ? h + ":" + p(m) + ":" + p(s) : m + ":" + p(s);
    }

    /* --- Depth gauge live polling ------------------------------------ */
    function initDepth() {
        var pill = document.getElementById("fc-depth-pill");
        if (!pill) return;
        var sessionId = pill.getAttribute("data-session-id");
        if (!sessionId) return;
        var COLORS = { flow: "#4caf50", deep: "#ffb300", surface: "#9e9e9e" };
        var LABELS = { flow: "Flow State", deep: "Deep Work",
                       surface: "Surface Focus" };
        function refresh() {
            fetch("/focus/depth?session_id=" +
                  encodeURIComponent(sessionId))
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    var st = d.state || "surface";
                    pill.textContent = (st === "flow" ? "🟢 " :
                                        st === "deep" ? "🟡 " : "⚪ ") +
                                       (LABELS[st] || st);
                    pill.style.borderColor = COLORS[st] || "#9e9e9e";
                    var meter = document.getElementById("fc-depth-meter");
                    if (meter) {
                        meter.style.background = COLORS[st] || "#9e9e9e";
                        meter.style.width =
                            (st === "flow" ? "100%" :
                             st === "deep" ? "62%" : "30%");
                    }
                    // Ring color follows depth.
                    document.querySelectorAll(".fc-ring-fg").forEach(
                        function (c) {
                            c.style.stroke = COLORS[st] || "#9e9e9e";
                        });
                })
                .catch(function () { /* keep last known state */ });
        }
        refresh();
        setInterval(refresh, 30000);
    }

    /* --- Zen mode ----------------------------------------------------- */
    function initZen() {
        var body = document.body;
        function toggle() { body.classList.toggle("zen-mode"); }
        document.querySelectorAll("[data-fc-zen]").forEach(function (b) {
            b.addEventListener("click", toggle);
        });
        document.addEventListener("keydown", function (e) {
            // 'z' toggles zen (not while typing in an input).
            if ((e.key === "z" || e.key === "Z") &&
                !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
                toggle();
            }
            if (e.key === "Escape" && body.classList.contains("zen-mode")) {
                body.classList.remove("zen-mode");
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        initRings();
        initDepth();
        initZen();
    });
})();
