/**
 * Focus Core — Navigation, Micro-Interactions & Pulse Inversion Engine
 *
 * Implements consensus specifications from:
 * - GLM-5.3 Adversarial Stress Audit (UI-8, UI-9, UI-10, UI-14)
 * - Muse AI Merlin Design Directives (Pulse ~300ms fade, Break scope, Cold-start)
 * - Qwen 3.8 Max Lead Architectural Audit (M-A1..M-A4, M-B1..M-B5, M-C1..M-C3)
 *
 * Zero external dependencies. Fully offline.
 */
(function () {
  'use strict';

  var POLL_WINDOW_MS = 90000; // Qwen M-A4: constant for classifying reloads vs new visits
  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ─────────────────────────────────────────────────────────────────────
  // QWEN M-A: NAVIGATION TIMING & TRIGGER CLASSIFIER
  // Uses Navigation Timing API as primary signal, sessionStorage as fallback.
  // Handles bfcache, wall-clock jumps, storage exceptions, pathname-only.
  // ─────────────────────────────────────────────────────────────────────
  var currentPath = window.location.pathname; // pathname only, stripped of query/hash
  var isEntrance = true; // default

  try {
    var navEntry = (window.performance && performance.getEntriesByType)
      ? performance.getEntriesByType('navigation')[0]
      : null;
    var navType = navEntry ? navEntry.type : 'navigate'; // 'reload' | 'navigate' | 'back_forward' | 'prerender'

    var stored = null;
    try {
      stored = sessionStorage.getItem('fc_nav_fingerprint');
    } catch (e) {
      // Storage exception (hardened webviews) -> default to T-entrance gracefully
    }

    var lastRec = stored ? JSON.parse(stored) : null;
    var now = Date.now();
    var elapsed = lastRec ? (now - lastRec.ts) : Infinity;

    // Clock-jump clamp: negative elapsed delta treated as new navigation
    if (elapsed < 0) {
      elapsed = Infinity;
    }

    var isSamePath = lastRec && (lastRec.path === currentPath);

    if (navType === 'back_forward') {
      // bfcache restoration: instant restore, T-data
      isEntrance = false;
    } else if (navType === 'reload') {
      // reload: T-data if within poll window on same path, else T-entrance
      isEntrance = !(isSamePath && elapsed <= POLL_WINDOW_MS);
    } else if (navType === 'navigate') {
      // navigate: T-data if meta-refresh or same-path redirect within poll window
      isEntrance = !(isSamePath && elapsed <= POLL_WINDOW_MS);
    } else {
      isEntrance = true;
    }

    // Update fingerprint
    try {
      sessionStorage.setItem('fc_nav_fingerprint', JSON.stringify({ path: currentPath, ts: now }));
    } catch (e) {}

  } catch (e) {
    isEntrance = true; // safe fallback on any exception
  }

  // Handle pageshow for bfcache restores without fresh DOMContentLoaded
  window.addEventListener('pageshow', function (event) {
    if (event.persisted) {
      isEntrance = false;
      document.querySelectorAll('.card').forEach(function (c) {
        c.style.animation = 'none';
      });
    }
  });

  // ─────────────────────────────────────────────────────────────────────
  // T-ENTRANCE: Card entrance animations (capped at 8 items)
  // ─────────────────────────────────────────────────────────────────────
  function initEntranceAnimations() {
    if (!isEntrance || reducedMotion) {
      document.querySelectorAll('.card').forEach(function (c) {
        c.style.animation = 'none';
      });
      return;
    }
    var STAGGER_CAP = 8;
    document.querySelectorAll('.card').forEach(function (c, i) {
      if (i < STAGGER_CAP) {
        c.style.setProperty('--card-i', i);
      } else {
        c.style.animation = 'none';
      }
    });
  }

  // ─────────────────────────────────────────────────────────────────────
  // T-DATA: Live poll refresh updates (150ms opacity tick, no count-up)
  // ─────────────────────────────────────────────────────────────────────
  function initDataTick() {
    if (!isEntrance && !reducedMotion) {
      document.querySelectorAll('[data-live]').forEach(function (el) {
        if (el.hasAttribute('data-financial')) return; // M-A3: financial numbers never animate
        el.style.transition = 'opacity 150ms ease';
        el.style.opacity = '0.6';
        setTimeout(function () { el.style.opacity = '1'; }, 20);
      });
    }
  }

  // ─────────────────────────────────────────────────────────────────────
  // T-ENTRANCE ONLY: Count-up for stat numbers
  // M-A1: Finalized immediately before printing.
  // M-A3: [data-financial] strictly excluded.
  // ─────────────────────────────────────────────────────────────────────
  var activeCounters = [];

  function initCounters() {
    if (!isEntrance || reducedMotion) {
      // Immediate finalization if not entrance or reduced-motion
      document.querySelectorAll('[data-count-to]').forEach(function (el) {
        el.textContent = el.dataset.countTo;
      });
      return;
    }

    document.querySelectorAll('[data-count-to]').forEach(function (el) {
      if (el.hasAttribute('data-financial')) {
        el.textContent = el.dataset.countTo;
        return;
      }
      var target = parseInt(el.dataset.countTo, 10);
      if (isNaN(target)) return;

      var dur = 480;
      var start = performance.now();
      var cancelled = false;

      function step(now) {
        if (cancelled) return;
        var p = Math.min((now - start) / dur, 1);
        var eased = 1 - (1 - p) * (1 - p);
        el.textContent = Math.round(eased * target);
        if (p < 1) requestAnimationFrame(step);
      }

      requestAnimationFrame(step);
      activeCounters.push(function finalize() {
        cancelled = true;
        el.textContent = target;
      });
    });
  }

  // Qwen M-A1: beforeprint event finalizes all counters immediately
  window.addEventListener('beforeprint', function () {
    activeCounters.forEach(function (finalize) { finalize(); });
  });

  // ─────────────────────────────────────────────────────────────────────
  // NAVIGATION: Sliding active indicator pill
  // ─────────────────────────────────────────────────────────────────────
  function initNav() {
    var nav = document.getElementById('topnav');
    if (!nav) return;
    var active = nav.querySelector('a.active');
    if (!active) return;

    var indicator = document.createElement('span');
    indicator.className = 'nav-indicator';
    nav.appendChild(indicator);

    function positionIndicator(el, animate) {
      var nr = nav.getBoundingClientRect();
      var er = el.getBoundingClientRect();
      if (!animate || reducedMotion) {
        indicator.style.transition = 'none';
        void indicator.offsetWidth;
      } else {
        indicator.style.transition = '';
      }
      indicator.style.left  = (er.left - nr.left - 4) + 'px';
      indicator.style.width = (er.width + 8) + 'px';
      indicator.style.opacity = '1';
    }

    positionIndicator(active, false);

    nav.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('mouseenter', function () { positionIndicator(a, true); });
      a.addEventListener('mouseleave', function () { positionIndicator(active, true); });
    });

    var resizeTimer;
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () { positionIndicator(active, false); }, 100);
    });
  }

  // ─────────────────────────────────────────────────────────────────────
  // UI-8: Window blur deactivates backdrop-filter on nav
  // ─────────────────────────────────────────────────────────────────────
  function initFrostGuard() {
    var nav = document.getElementById('topnav');
    if (!nav) return;
    window.addEventListener('blur', function () {
      nav.style.backdropFilter = 'none';
      nav.style.webkitBackdropFilter = 'none';
    });
    window.addEventListener('focus', function () {
      nav.style.backdropFilter = '';
      nav.style.webkitBackdropFilter = '';
    });
  }

  // ─────────────────────────────────────────────────────────────────────
  // QWEN M-B: PULSE INVERSION (SINGLE WRITER setDepth)
  //
  // Formula:
  //   --pulse-amp: calc(1 - var(--depth, 0))  (computed in CSS)
  //   Surface (0.0): pulse-amp = 1.0 -> 50% opacity swings to 0.4 (full pulse)
  //   Deep    (0.5): pulse-amp = 0.5 -> 50% opacity swings to 0.7 (calmed)
  //   Flow   (>=0.95): .is-flow class added -> animation: none (TRUE ZERO)
  //   Unmeasured: .is-unmeasured class added -> animation: none
  //
  // M-B1: Duration quantized to buckets [2s, 3.5s, 5s] to eliminate phase jitter.
  // M-B3: SVG role="img" aria-label updated statically without aria-live.
  // ─────────────────────────────────────────────────────────────────────
  window.setFocusDepth = function (score, remainingSeconds, totalSeconds) {
    var ring = document.querySelector('[data-fc-ring]');
    if (!ring) return;

    if (score == null || isNaN(score)) {
      ring.classList.add('is-unmeasured');
      ring.classList.remove('is-flow');
      ring.style.setProperty('--depth', '0');
      return;
    }

    var d = Math.min(1.0, Math.max(0.0, parseFloat(score)));
    ring.classList.remove('is-unmeasured');
    ring.classList.toggle('is-flow', d >= 0.95);
    ring.style.setProperty('--depth', d.toFixed(3));

    // M-B1: Quantize duration into 3 stable buckets
    var durBucket = '2s';
    if (d >= 0.8) {
      durBucket = '5s';
    } else if (d >= 0.4) {
      durBucket = '3.5s';
    }
    ring.style.setProperty('--pulse-dur', durBucket);

    // M-B3: Static ARIA label update
    var stateName = (d >= 0.8) ? 'Flow state' : (d >= 0.4 ? 'Deep work' : 'Surface focus');
    if (remainingSeconds != null) {
      var mins = Math.floor(remainingSeconds / 60);
      var secs = remainingSeconds % 60;
      var timeStr = mins + ':' + (secs < 10 ? '0' : '') + secs;
      ring.setAttribute('aria-label', stateName + ' \u00b7 ' + timeStr + ' remaining');
    }
  };

  function initRingPulse() {
    var ring = document.querySelector('[data-fc-ring]');
    if (!ring) return;
    var rawScore = ring.dataset.depthScore;
    var parsed = (rawScore !== undefined && rawScore !== '') ? parseFloat(rawScore) : null;
    var rem = parseInt(ring.dataset.remaining, 10);
    var tot = parseInt(ring.dataset.total, 10);
    window.setFocusDepth(parsed, isNaN(rem) ? null : rem, isNaN(tot) ? null : tot);
  }

  // ─────────────────────────────────────────────────────────────────────
  // BUTTON TACTILE PRESS
  // ─────────────────────────────────────────────────────────────────────
  function initButtons() {
    if (reducedMotion) return;
    document.addEventListener('pointerdown', function (e) {
      var btn = e.target.closest('button, .btn');
      if (!btn || btn.disabled) return;
      btn.style.transform = 'scale(0.96)';
    });
    ['pointerup', 'pointercancel'].forEach(function (ev) {
      document.addEventListener(ev, function (e) {
        var btn = e.target.closest('button, .btn');
        if (btn) btn.style.transform = '';
      });
    });
  }

  // ─────────────────────────────────────────────────────────────────────
  // BOOTSTRAP
  // ─────────────────────────────────────────────────────────────────────
  function boot() {
    initNav();
    initEntranceAnimations();
    initCounters();
    initDataTick();
    initRingPulse();
    initButtons();
    initFrostGuard();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
}());
