/* Focus Core — The Living Instrument: Home page behavior.
   Slice 1: count-up, cursor spotlight, magnetic CTA.
   Entrance choreography is pure CSS (.st / .line) — no-JS safe. */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function fmtHMM(totalSeconds) {
    var h = Math.floor(totalSeconds / 3600);
    var m = Math.floor((totalSeconds % 3600) / 60);
    return h + ':' + String(m).padStart(2, '0');
  }

  document.addEventListener('DOMContentLoaded', function () {
    /* ---- pulse count-up: 0 → actual, 1.4s ease-out ----
       The final value is server-rendered, so no-JS still shows it. */
    document.querySelectorAll('[data-countup]').forEach(function (el) {
      var target = parseInt(el.getAttribute('data-seconds') || '0', 10);
      if (reduceMotion) return; /* final value already in the markup */
      el.textContent = '0:00';
      var start = null, dur = 1400, delay = 450;
      function frame(t) {
        if (!start) start = t;
        var p = Math.min(1, (t - start) / dur);
        var eased = 1 - Math.pow(1 - p, 3);
        el.textContent = fmtHMM(Math.round(target * eased));
        if (p < 1) requestAnimationFrame(frame);
        else el.textContent = fmtHMM(target);
      }
      setTimeout(function () { requestAnimationFrame(frame); }, delay);
    });

    /* ---- cursor spotlight on the pulse card ---- */
    document.querySelectorAll('.lv-pulse').forEach(function (card) {
      card.addEventListener('mousemove', function (e) {
        var r = card.getBoundingClientRect();
        card.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%');
        card.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%');
      });
    });

    /* ---- magnetic CTA: gentle pull toward the cursor, max 7px ---- */
    if (!reduceMotion) document.querySelectorAll('.lv-magnet').forEach(function (btn) {
      btn.addEventListener('mousemove', function (e) {
        var r = btn.getBoundingClientRect();
        var dx = e.clientX - (r.left + r.width / 2);
        var dy = e.clientY - (r.top + r.height / 2);
        var d = Math.hypot(dx, dy) || 1;
        var pull = Math.min(7, d * 0.12);
        btn.style.transform =
          'translate(' + (dx / d * pull).toFixed(1) + 'px,' + (dy / d * pull).toFixed(1) + 'px)';
      });
      btn.addEventListener('mouseleave', function () { btn.style.transform = ''; });
    });
  });
})();
