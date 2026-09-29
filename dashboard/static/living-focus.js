/* Focus Core — The Living Instrument: Focus page behavior.
   Slice 6: ready-state instrument — stepper, mode chips + gliding pill,
   orb time sync, Begin morph into the session POST.
   Slice 7: in-session orb — live tick, depth glow, time-up state. */
(function () {
  'use strict';

  var form = document.getElementById('lv-begin-form');
  if (!form) return; // not the ready state (e.g. in-session page)

  var reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- duration stepper: 15–120 min in 5s ---- */
  var minutesInput = document.getElementById('lv-minutes');
  var stepNum = document.getElementById('lv-step-num');
  var orbTime = document.getElementById('lv-orb-time');
  var minutes = parseInt(minutesInput.value, 10) || 50;

  function fmtTime(m) { return m + ':00'; }

  function renderMinutes() {
    minutesInput.value = String(minutes);
    orbTime.textContent = fmtTime(minutes);
    // digits blur-swap
    stepNum.classList.add('lv-swap');
    setTimeout(function () {
      stepNum.textContent = String(minutes);
      stepNum.classList.remove('lv-swap');
    }, reduceMotion ? 0 : 160);
  }

  document.getElementById('lv-minus').addEventListener('click', function () {
    minutes = Math.max(15, minutes - 5);
    renderMinutes();
  });
  document.getElementById('lv-plus').addEventListener('click', function () {
    minutes = Math.min(120, minutes + 5);
    renderMinutes();
  });

  /* ---- mode chips + gliding ember pill ---- */
  var modeInput = document.getElementById('lv-mode');
  var stepNote = document.getElementById('lv-step-note');
  var cyclesWrap = document.getElementById('lv-cycles');
  var glide = document.querySelector('.lv-glide');
  var chips = Array.prototype.slice.call(
    document.querySelectorAll('.lv-chip'));

  var NOTES = {
    classic: 'Fixed timer.',
    pomodoro: 'Work-block length — breaks adapt to you.',
    flowtime: 'Soft target, not an alarm.'
  };

  function moveGlide(btn) {
    if (!glide || !btn) return;
    glide.style.width = btn.offsetWidth + 'px';
    glide.style.transform = 'translateX(' + btn.offsetLeft + 'px)';
  }

  function selectChip(btn, instant) {
    chips.forEach(function (c) {
      var on = c === btn;
      c.classList.toggle('on', on);
      c.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    var mode = btn.getAttribute('data-mode');
    modeInput.value = mode;
    stepNote.textContent = NOTES[mode] || '';
    cyclesWrap.hidden = mode !== 'pomodoro';
    if (!instant && !reduceMotion) moveGlide(btn);
    else if (glide && btn) {
      // place without animation on first paint
      var prev = glide.style.transition;
      glide.style.transition = 'none';
      moveGlide(btn);
      void glide.offsetWidth;
      glide.style.transition = prev;
    }
  }

  chips.forEach(function (c, i) {
    c.addEventListener('click', function () { selectChip(c, false); });
    /* proper radiogroup keys: arrows move, not just Tab */
    c.addEventListener('keydown', function (e) {
      var next = null;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        next = chips[(i + 1) % chips.length];
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        next = chips[(i - 1 + chips.length) % chips.length];
      }
      if (next) {
        e.preventDefault();
        selectChip(next, false);
        next.focus();
      }
    });
  });
  // initial glide placement after layout
  window.addEventListener('load', function () {
    var active = document.querySelector('.lv-chip.on');
    selectChip(active, true);
  });
  // also place now in case load already fired
  (function () {
    var active = document.querySelector('.lv-chip.on');
    if (active && glide) {
      // defer one frame so layout is ready
      requestAnimationFrame(function () { selectChip(active, true); });
    }
  })();

  /* ---- pomodoro work-block stepper: 1–24 ---- */
  var cyclesInput = document.getElementById('lv-cycles-val');
  var cNum = document.getElementById('lv-cnum');
  var cycles = parseInt(cyclesInput.value, 10) || 4;

  function renderCycles() {
    cyclesInput.value = String(cycles);
    cNum.textContent = String(cycles);
  }
  document.getElementById('lv-cminus').addEventListener('click', function () {
    cycles = Math.max(1, cycles - 1);
    renderCycles();
  });
  document.getElementById('lv-cplus').addEventListener('click', function () {
    cycles = Math.min(24, cycles + 1);
    renderCycles();
  });

  /* ---- magnetic begin button (same feel as home CTA) ---- */
  var begin = document.getElementById('lv-begin');
  if (!reduceMotion && window.matchMedia('(pointer: fine)').matches) {
    begin.addEventListener('pointermove', function (e) {
      var r = begin.getBoundingClientRect();
      var dx = (e.clientX - (r.left + r.width / 2)) / r.width;
      var dy = (e.clientY - (r.top + r.height / 2)) / r.height;
      begin.style.transform =
        'translate(' + (dx * 7).toFixed(1) + 'px,' + (dy * 7).toFixed(1) + 'px)';
    });
    begin.addEventListener('pointerleave', function () {
      begin.style.transform = '';
    });
  }

  /* ---- the signature morph: button becomes the timer ---- */
  var orbState = document.getElementById('lv-orb-state');
  var submitted = false;

  begin.addEventListener('click', function (e) {
    if (submitted) return;
    if (reduceMotion) return; // submit immediately, no morph
    e.preventDefault();
    submitted = true;
    document.body.classList.add('lv-beginning');
    // state label blur-swaps to "In session"
    orbState.style.filter = 'blur(6px)';
    orbState.style.opacity = '0';
    setTimeout(function () {
      orbState.textContent = 'In session';
      orbState.style.filter = '';
      orbState.style.opacity = '';
    }, 300);
    // let the morph play, then POST
    setTimeout(function () { form.submit(); }, 720);
  });
})();

/* In-session orb: the server renders data-total / data-value /
   data-ring-mode into [data-lv-orb]; this ticks the ring and the
   countdown once per second and polls the depth gauge for the halo glow. */
(function () {
  'use strict';

  var orb = document.querySelector('[data-lv-orb]');
  if (!orb || orb.hasAttribute('data-lv-static')) return;

  var reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var CIRC = 942.48; // 2*pi*150
  var total = parseFloat(orb.getAttribute('data-total')) || 1;
  var value = parseFloat(orb.getAttribute('data-value')) || 0;
  var mode = orb.getAttribute('data-ring-mode') || 'remaining';
  var prog = document.getElementById('lv-orb-prog');
  var timeEl = document.getElementById('lv-orb-time');
  var stateEl = document.getElementById('lv-orb-state');

  var endAt = 0, startAt = 0;
  if (mode === 'remaining') {
    endAt = Date.now() + Math.max(0, value) * 1000;
  } else {
    startAt = Date.now() - Math.max(0, value) * 1000;
  }

  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec));
    var h = Math.floor(sec / 3600),
        m = Math.floor(sec % 3600 / 60),
        s = sec % 60;
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return h > 0 ? h + ':' + p(m) + ':' + p(s) : m + ':' + p(s);
  }

  /* Keep the orb's aria-label truthful as time passes — but only when
     the displayed minute changes, so screen readers don't chatter. */
  var baseCaption = orb.getAttribute('aria-label') || '';
  var sepAt = baseCaption.lastIndexOf(',');
  var labelCaption = sepAt > 0 ? baseCaption.slice(0, sepAt) : baseCaption;
  var lastMinute = -1;
  function refreshAria(text) {
    /* text is m:ss or h:mm:ss — derive whole minutes from the parts */
    var parts = String(text).split(':').map(function (p) {
      return parseInt(p, 10);
    });
    var minute = 0;
    if (parts.length === 3) minute = parts[0] * 60 + parts[1];
    else if (parts.length === 2) minute = parts[0];
    if (isNaN(minute)) minute = 0;
    if (minute !== lastMinute) {
      lastMinute = minute;
      orb.setAttribute('aria-label', labelCaption + ', ' + text);
    }
  }

  function paint(frac, text) {
    frac = Math.max(0, Math.min(1, frac));
    if (prog) {
      if (!reduceMotion) prog.style.transition = 'stroke-dashoffset 1s linear';
      prog.style.strokeDashoffset = String(CIRC * (1 - frac));
    }
    if (timeEl) timeEl.textContent = text;
    refreshAria(text);
  }

  var tick = setInterval(function () {
    if (mode === 'remaining') {
      var left = Math.max(0, (endAt - Date.now()) / 1000);
      paint(left / total, fmt(left));
      if (left <= 0) {
        clearInterval(tick);
        orb.classList.add('lv-done');
        if (stateEl) stateEl.textContent = 'Time is up';
      }
    } else {
      // elapsed (flowtime): the ring fills toward the soft target.
      var elapsed = Math.max(0, (Date.now() - startAt) / 1000);
      paint(Math.min(1, elapsed / total), fmt(elapsed));
    }
  }, 1000);

  /* Spec §8: the ticking timer is aria-live="off"; this polite region
     announces remaining/elapsed time every 5 minutes for screen readers. */
  var statusEl = document.getElementById('lv-orb-status');
  if (statusEl) {
    var announce = function () {
      var mins, msg;
      if (mode === 'remaining') {
        mins = Math.floor(Math.max(0, (endAt - Date.now()) / 1000) / 60);
        msg = mins <= 0 ? 'Less than a minute remaining'
          : mins + (mins === 1 ? ' minute remaining' : ' minutes remaining');
      } else {
        mins = Math.floor(Math.max(0, (Date.now() - startAt) / 1000) / 60);
        msg = mins + (mins === 1 ? ' minute elapsed' : ' minutes elapsed');
      }
      statusEl.textContent = msg;
    };
    setInterval(announce, 5 * 60 * 1000);
  }

  /* Depth glow: poll the gauge, tint the halo. Skipped on breaks. */
  var sid = orb.getAttribute('data-session-id');
  var phase = orb.getAttribute('data-phase');
  if (sid && phase !== 'break') {
    setInterval(function () {
      fetch('/focus/depth?session_id=' + encodeURIComponent(sid))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.state) orb.setAttribute('data-depth', d.state);
        })
        .catch(function () { /* gauge offline: keep last glow */ });
    }, 30000);
  }
})();
