/* Phase 11: zero-dependency ambient soundscapes.
 *
 * Everything is synthesized at runtime with the Web Audio API.
 * No audio files, no network, no external libraries -- 100% offline
 * and private. Sound starts only on user gesture (autoplay policy).
 */
(function () {
    "use strict";

    var ctx = null;          // AudioContext (created on first user gesture)
    var master = null;       // master GainNode
    var current = null;      // {kind, nodes:[...]} of the playing soundscape
    var volume = parseFloat(localStorage.getItem("fc_soundscape_vol") || "0.5");

    function ensureCtx() {
        if (!ctx) {
            var AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return null;
            // Strictly ONE singleton AudioContext for the browser lifetime
            // (Chrome caps at 6; never create another on toggles).
            ctx = new AC();
            master = ctx.createGain();
            master.gain.value = volume;
            // Brickwall limiter: no clipping or popping on the master bus.
            var comp = ctx.createDynamicsCompressor();
            comp.threshold.value = -12;
            comp.knee.value = 0;
            comp.ratio.value = 20;
            comp.attack.value = 0.002;
            comp.release.value = 0.1;
            master.connect(comp);
            comp.connect(ctx.destination);
        }
        return ctx;
    }

    function setVolume(v) {
        volume = Math.max(0, Math.min(1, v));
        localStorage.setItem("fc_soundscape_vol", String(volume));
        if (master) master.gain.value = volume;
        var sliders = document.querySelectorAll("[data-fc-vol]");
        for (var i = 0; i < sliders.length; i++) sliders[i].value = volume;
    }

    function stopCurrent() {
        if (!current) return;
        try {
            current.nodes.forEach(function (n) {
                try { n.stop ? n.stop() : n.disconnect(); }
                catch (e) { /* already stopped */ }
            });
        } catch (e) { /* ignore */ }
        current = null;
        // Clean lifecycle: suspend the singleton instead of closing it.
        if (ctx && ctx.state === "running") {
            try { ctx.suspend(); } catch (e) { /* ignore */ }
        }
        document.querySelectorAll("[data-fc-sound]").forEach(function (b) {
            b.classList.remove("playing");
            b.textContent = b.getAttribute("data-label") || b.textContent;
        });
        var st = document.getElementById("fc-sound-status");
        if (st) st.textContent = "Sound off";
    }

    /* --- Brownian rain: integrated white noise -> lowpass + slow LFO --- */
    function startRain() {
        var c = ensureCtx();
        if (!c) return;
        if (c.state === "suspended") c.resume();
        var len = 4 * c.sampleRate;
        var buf = c.createBuffer(1, len, c.sampleRate);
        var d = buf.getChannelData(0);
        var last = 0;
        for (var i = 0; i < len; i++) {
            var white = Math.random() * 2 - 1;
            last = (last + 0.02 * white) / 1.02;   // brown-ish integration
            d[i] = last * 3.2;
        }
        var src = c.createBufferSource();
        src.buffer = buf; src.loop = true;
        var lp = c.createBiquadFilter();
        lp.type = "lowpass"; lp.frequency.value = 900; lp.Q.value = 0.4;
        // Slow LFO on the filter -> rain-like movement.
        var lfo = c.createOscillator();
        lfo.frequency.value = 0.07;
        var lfoGain = c.createGain();
        lfoGain.gain.value = 320;
        lfo.connect(lfoGain); lfoGain.connect(lp.frequency); lfo.start();
        var g = c.createGain(); g.gain.value = 0.5;
        src.connect(lp); lp.connect(g); g.connect(master);
        src.start();
        return { kind: "rain", nodes: [src, lfo] };
    }

    /* --- Calibrated pink noise: Paul Kellet's filter cascade --- */
    function startPink() {
        var c = ensureCtx();
        if (!c) return;
        if (c.state === "suspended") c.resume();
        var len = 4 * c.sampleRate;
        var buf = c.createBuffer(1, len, c.sampleRate);
        var d = buf.getChannelData(0);
        var b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
        for (var i = 0; i < len; i++) {
            var white = Math.random() * 2 - 1;
            b0 = 0.99886 * b0 + white * 0.0555179;
            b1 = 0.99332 * b1 + white * 0.0750759;
            b2 = 0.96900 * b2 + white * 0.1538520;
            b3 = 0.86650 * b3 + white * 0.3104856;
            b4 = 0.55000 * b4 + white * 0.5329522;
            b5 = -0.7616 * b5 - white * 0.0168980;
            var pink = b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362;
            b6 = white * 0.115926;
            d[i] = pink * 0.11;
        }
        var src = c.createBufferSource();
        src.buffer = buf; src.loop = true;
        var g = c.createGain(); g.gain.value = 0.6;
        src.connect(g); g.connect(master);
        src.start();
        return { kind: "pink", nodes: [src] };
    }

    /* --- 40 Hz gamma focus beat: 200 Hz + 240 Hz binaural pair ---
     * Strict physical stereo isolation via StereoPannerNode:
     * 200 Hz -> left ear only, 240 Hz -> right ear only. */
    function startGamma() {
        var c = ensureCtx();
        if (!c) return;
        // Resume inside the user gesture (autoplay policy).
        if (c.state === "suspended") c.resume();
        var oL = c.createOscillator();
        oL.type = "sine"; oL.frequency.value = 200;
        var oR = c.createOscillator();
        oR.type = "sine"; oR.frequency.value = 240;   // 40 Hz difference
        var panL = c.createStereoPanner();
        panL.pan.value = -1.0;   // hard left
        var panR = c.createStereoPanner();
        panR.pan.value = 1.0;    // hard right
        var gL = c.createGain(); gL.gain.value = 0.16;
        var gR = c.createGain(); gR.gain.value = 0.16;
        oL.connect(gL); gL.connect(panL); panL.connect(master);
        oR.connect(gR); gR.connect(panR); panR.connect(master);
        oL.start(); oR.start();
        return { kind: "gamma", nodes: [oL, oR] };
    }

    var STARTERS = { rain: startRain, pink: startPink, gamma: startGamma };
    var LABELS = {
        rain: "Brownian Rain",
        pink: "Pink Noise",
        gamma: "40Hz Gamma Beat"
    };

    function toggle(kind, btn) {
        if (current && current.kind === kind) {
            stopCurrent();
            return;
        }
        stopCurrent();
        var starter = STARTERS[kind];
        if (!starter) return;
        current = starter();
        if (current && btn) {
            btn.classList.add("playing");
            btn.textContent = "Stop " + (btn.getAttribute("data-label") ||
                                         LABELS[kind]);
        }
        var st = document.getElementById("fc-sound-status");
        if (st && current) st.textContent = "Playing: " + LABELS[kind];
    }

    // Wire up buttons/sliders wherever they appear (session card + zen).
    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-fc-sound]").forEach(function (b) {
            if (!b.getAttribute("data-label"))
                b.setAttribute("data-label", b.textContent);
            b.addEventListener("click", function () {
                toggle(b.getAttribute("data-fc-sound"), b);
            });
        });
        document.querySelectorAll("[data-fc-vol]").forEach(function (s) {
            s.value = volume;
            s.addEventListener("input", function () {
                setVolume(parseFloat(s.value));
            });
        });
        document.querySelectorAll("[data-fc-sound-stop]").forEach(
            function (b) {
                b.addEventListener("click", stopCurrent);
            });
    });

    // Public API for focus.js / zen mode.
    window.FCSound = {
        toggle: toggle,
        stop: stopCurrent,
        setVolume: setVolume,
        getVolume: function () { return volume; },
        playing: function () { return current ? current.kind : null; }
    };
})();
