/* Figure 1: the repair sequence.
 *
 * A model writes score.geno, `geno test` finds the example it breaks, the
 * model repairs the threshold, the examples pass and the file compiles.
 * Every line Geno prints here was captured from the 0.5.0 CLI.
 *
 * The page ships the finished capture as static HTML, so readers without
 * JavaScript, or with reduced motion, see the complete last frame. With
 * motion allowed, the capture rewinds and plays on a virtual clock that
 * stops while it is paused, scrolled away or in a background tab. The
 * step strip under the figure jumps to any frame.
 */
(function (global) {
  "use strict";

  var BUGGY = [
    "func score_label(score: Int) -> String",
    "    requires score >= 0",
    '    example 90 -> "excellent"',
    '    example 89 -> "passing"',
    '    example 60 -> "passing"',
    '    example 59 -> "needs work"',
    "",
    "    if score >= 90 then",
    '        return "excellent"',
    "    else if score >= 75 then",
    '        return "passing"',
    "    else",
    '        return "needs work"',
    "    end if",
    "end func",
  ].join("\n");
  var FIXED = BUGGY.replace(">= 75", ">= 60");
  var EX_LINES = [3, 4, 5, 6];
  var FIX_LINE = 10;

  var TEST = "$ geno test -v score.geno";
  var FAIL_OUT = '  FAIL  score.geno (3/4) 14ms\n        x score_label: expected "passing", got "needs work"\n\n3 passed, 1 failed, in 0.04s';
  var PASS_OUT = "  PASS  score.geno (4/4) 11ms\n\n4 passed, 0 failed, in 0.03s";
  var PY = "$ geno compile score.geno -o score.py";
  var JS = "$ geno compile score.geno --target js -o score.js";

  var SAY = [
    "Here’s a function that labels exam scores.",
    null,
    "60 passes and 59 doesn’t, so the bar is 60, not 75.",
    null,
    "All four examples hold. Shipping it.",
  ];
  var SHIP_TERM = PY + "\nCompiled to score.py\n" + JS + "\nCompiled to score.js";

  /* Each frame is the complete state at the end of a step. */
  var FRAMES = [
    { code: BUGGY, vec: "    ", term: "$ ", termDim: false, marks: {} },
    { code: BUGGY, vec: "PPFP", term: TEST + "\n" + FAIL_OUT, marks: { 5: "fail" } },
    { code: FIXED, vec: "????", term: TEST + "\n" + FAIL_OUT, termDim: true, marks: { 10: "fix" } },
    { code: FIXED, vec: "PPPP", term: TEST + "\n" + PASS_OUT, marks: { 3: "pass", 4: "pass", 5: "pass", 6: "pass" } },
    { code: FIXED, vec: "PPPP", term: SHIP_TERM, marks: {} },
  ];
  var STEP_NAMES = ["Write", "Fail", "Repair", "Pass", "Ship"];

  function H() {
    return global.GenoHighlight;
  }

  /* Open blocks for a prefix, innermost first, as
     geno.constraints.get_unclosed_blocks reports them for this program. */
  function openBlocks(text) {
    var stack = [];
    text.split("\n").forEach(function (raw) {
      var l = raw.replace(/"[^"]*"?/g, '""');
      if (/^\s*end\s+\w+/.test(l)) {
        stack.pop();
        return;
      }
      if (/^\s*else\b/.test(l)) return;
      var m = l.match(/^\s*(func|if|for|while|match)\b/);
      if (m) stack.push(m[1]);
    });
    return stack.reverse();
  }

  var BADGE = { P: ["pass", "PASS"], F: ["fail", "FAIL"], "?": ["stale", "····"], " ": ["wait", "····"], R: ["run", "RUN"] };

  /* code panel: numbered lines, test-vector badges on the example clauses */
  function codeHTML(text, opts) {
    opts = opts || {};
    var marks = opts.marks || {};
    var vec = opts.vec || "    ";
    var lines = text.split("\n");
    return lines
      .map(function (l, i) {
        var n = i + 1;
        var html = H().highlight(l, "geno");
        if (opts.sel && opts.sel.line === n) {
          html = html.replace('<span class="tok-num">' + opts.sel.text + "</span>", '<span class="tok-num rp-sel">' + opts.sel.text + "</span>");
        }
        if (opts.caret && i === lines.length - 1) html += '<span class="rp-caret"></span>';
        var ex = EX_LINES.indexOf(n);
        var badge = "";
        if (ex !== -1 && /^\s*example\b.*"[^"]*"\s*$/.test(l)) {
          var b = BADGE[vec[ex]] || BADGE[" "];
          badge = '<span class="rp-b rp-b-' + b[0] + '"><i>TV' + (ex + 1) + "</i>" + b[1] + "</span>";
        }
        var cls = "rl" + (marks[n] ? " is-" + marks[n] : "");
        return '<span class="' + cls + '"><span class="rl-n">' + n + '</span><span class="rl-c">' + (html || " ") + "</span>" + badge + "</span>";
      })
      .join("");
  }

  function termHTML(text, caret) {
    return H().highlight(text, "sh") + (caret ? '<span class="rp-caret"></span>' : "");
  }

  function sigState(step) {
    // EXAMPLES row of the timing strip: unknown, 3/4, unknown again after
    // the edit, then 4/4.
    return ["x", "lo", "x", "hi", "hi"][step];
  }

  global.GenoReplay = { codeHTML: codeHTML, termHTML: termHTML, FRAMES: FRAMES, SAY: SAY, openBlocks: openBlocks, sigState: sigState };
  if (typeof document === "undefined") return;

  var root = document.querySelector("[data-repair]");
  if (!root || !H()) return;

  var reduce = global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (s) {
    return root.querySelector(s);
  };
  var codeEl = $("[data-rp-code]");
  var termEl = $("[data-rp-term]");
  var sayEl = $("[data-rp-say]");
  var openEl = $("[data-rp-open]");
  var stateEl = $("[data-rp-state]");
  var toggle = $("[data-rp-toggle]");
  var restart = $("[data-rp-restart]");
  var strip = $("[data-rp-strip]");
  var steps = Array.prototype.slice.call(root.querySelectorAll(".rp-steps [data-step]"));
  var lastSaid = SAY[4];

  /* ------------------------------------------------------- virtual clock */
  var vnow = 0;
  var playing = false;
  var visible = true;
  var waiters = [];
  var gen = 0;
  var last = 0;
  var rafOn = false;
  var stepIdx = 4;
  var stepStart = 0;
  var stepLen = 1;
  var cycles = 0;
  var ABORT = {};
  // Sleeps are scheduled from the previous sleep's due time, not from the
  // frame that happened to resolve it, so a step lasts exactly as long as
  // its sleeps add up to and the cursor keeps pace.
  var due = null;
  // Dry runs add up a step's sleeps without waiting, to size the cursor.
  var dry = false;
  var dryTotal = 0;

  function sleep(ms, g) {
    if (dry) {
      dryTotal += ms;
      return Promise.resolve();
    }
    var start = due != null ? due : vnow;
    return new Promise(function (res, rej) {
      waiters.push({ t: start + ms, res: res, rej: rej, g: g });
    });
  }
  function flush() {
    waiters = waiters.filter(function (w) {
      if (w.g === gen) return true;
      w.rej(ABORT);
      return false;
    });
  }
  function tick(ts) {
    var dt = last ? Math.min(64, ts - last) : 16;
    last = ts;
    if (playing && visible && !document.hidden) vnow += dt;
    flush();
    var ready = waiters.filter(function (w) {
      return w.t <= vnow;
    });
    waiters = waiters.filter(function (w) {
      return w.t > vnow;
    });
    ready.forEach(function (w) {
      due = w.t;
      w.res();
    });
    drawCursor();
    // Frames are requested only while something is moving.
    if (playing && visible) global.requestAnimationFrame(tick);
    else rafOn = false;
  }
  function wake() {
    if (rafOn || !playing || !visible) return;
    rafOn = true;
    last = 0;
    global.requestAnimationFrame(tick);
  }

  /* ------------------------------------------------------------ drawing */
  var rw = null;
  function drawCursor() {
    var pos;
    if (rw) {
      var k = Math.min(1, (vnow - rw.t0) / rw.len);
      pos = rw.from * (1 - k * k * (3 - 2 * k));
    } else pos = (stepIdx + Math.max(0, Math.min(1, (vnow - stepStart) / stepLen))) / 5;
    strip.style.setProperty("--rp-pos", pos.toFixed(4));
  }
  function setStep(i, len) {
    stepIdx = i;
    stepLen = len || 1;
    // A jump (no length) lands on the end of the step.
    var now = due != null ? due : vnow;
    stepStart = len ? now : now - 1;
    steps.forEach(function (b, k) {
      b.classList.toggle("is-done", k < i);
      b.classList.toggle("is-now", k === i);
      if (k === i) b.setAttribute("aria-current", "step");
      else b.removeAttribute("aria-current");
    });
    strip.dataset.at = String(i);
    stateEl.textContent = "t" + i + " · " + STEP_NAMES[i].toUpperCase();
    drawCursor();
  }
  function say(text, past) {
    if (text != null) lastSaid = text;
    sayEl.textContent = lastSaid || "";
    sayEl.classList.toggle("is-past", !!past);
  }
  function hud(stack) {
    openEl.textContent = stack.length ? stack.join(" › ") : "none";
    openEl.classList.toggle("is-open", stack.length > 0);
  }
  function paint(i) {
    var f = FRAMES[i];
    codeEl.innerHTML = codeHTML(f.code, { marks: f.marks, vec: f.vec });
    termEl.innerHTML = termHTML(f.term, i === 0);
    termEl.classList.toggle("is-dim", !!f.termDim);
    hud([]);
    say(SAY[i] != null ? SAY[i] : SAY.slice(0, i + 1).filter(Boolean).pop(), SAY[i] == null);
    setStep(i);
  }
  function setPlaying(on) {
    playing = on;
    root.classList.toggle("is-playing", on);
    toggle.querySelector("span").textContent = on ? "Pause" : "Play";
    toggle.setAttribute("aria-label", on ? "Pause the replay" : "Play the replay");
    if (on) wake();
  }

  /* -------------------------------------------------------------- steps */
  var LEN = [5600, 4600, 4000, 3800, 8500];
  // How long the finished capture stays up before the first replay. The clock
  // only runs while the figure is on screen.
  var POSTER_MS = 4500;

  async function typeSay(text, g) {
    say("", false);
    for (var i = 1; i <= text.length; i++) {
      sayEl.textContent = text.slice(0, i);
      await sleep(text[i - 1] === " " ? 34 : 22 + ((i * 37) % 19), g);
    }
    lastSaid = text;
  }
  async function typeCmd(prefix, cmd, g) {
    var body = cmd.slice(2);
    for (var i = 0; i <= body.length; i++) {
      termEl.innerHTML = termHTML(prefix + "$ " + body.slice(0, i), true);
      await sleep(16 + ((i * 29) % 13), g);
    }
  }
  async function printLines(prefix, out, g) {
    var lines = out.split("\n");
    for (var i = 1; i <= lines.length; i++) {
      termEl.innerHTML = termHTML(prefix + lines.slice(0, i).join("\n"));
      await sleep(lines[i - 1] ? 90 : 40, g);
    }
  }
  async function scan(code, results, marks, g) {
    var vec = "    ".split("");
    for (var k = 0; k < 4; k++) {
      var m = {};
      m[EX_LINES[k]] = "scan";
      vec[k] = "R";
      codeEl.innerHTML = codeHTML(code, { vec: vec.join(""), marks: m });
      await sleep(200, g);
      vec[k] = results[k];
    }
    codeEl.innerHTML = codeHTML(code, { vec: vec.join(""), marks: marks });
  }

  var RUN = [
    async function write(g) {
      termEl.classList.remove("is-dim");
      termEl.innerHTML = termHTML("$ ", true);
      codeEl.innerHTML = "";
      hud([]);
      await typeSay(SAY[0], g);
      await sleep(320, g);
      var tokens = BUGGY.match(/\s+|[A-Za-z_]\w*|"[^"]*"|\d+|->|>=|./g);
      var acc = "";
      for (var i = 0; i < tokens.length; i++) {
        acc += tokens[i];
        codeEl.innerHTML = codeHTML(acc, { caret: true });
        hud(openBlocks(acc));
        var t = tokens[i];
        await sleep(/\n/.test(t) ? 70 : /^\s+$/.test(t) ? 6 : 18 + ((i * 41) % 26), g);
      }
      codeEl.innerHTML = codeHTML(BUGGY, {});
      hud([]);
      await sleep(700, g);
    },
    async function fail(g) {
      say(null, true);
      await typeCmd("", TEST, g);
      await sleep(260, g);
      await scan(BUGGY, "PPFP", { 5: "fail" }, g);
      await printLines(TEST + "\n", FAIL_OUT, g);
      await sleep(2600, g);
    },
    async function repair(g) {
      termEl.classList.add("is-dim");
      codeEl.innerHTML = codeHTML(BUGGY, { vec: "PPFP", marks: { 5: "fail" } });
      await typeSay(SAY[2], g);
      await sleep(300, g);
      codeEl.innerHTML = codeHTML(BUGGY, { vec: "????", marks: { 10: "fix" }, sel: { line: FIX_LINE, text: "75" } });
      await sleep(650, g);
      var mid = BUGGY.replace(">= 75", ">= 6");
      codeEl.innerHTML = codeHTML(mid, { vec: "????", marks: { 10: "fix" } });
      await sleep(170, g);
      codeEl.innerHTML = codeHTML(FIXED, { vec: "????", marks: { 10: "fix" }, sel: { line: FIX_LINE, text: "60" } });
      await sleep(900, g);
    },
    async function pass(g) {
      termEl.classList.remove("is-dim");
      say(null, true);
      await typeCmd("", TEST, g);
      await sleep(260, g);
      await scan(FIXED, "PPPP", { 3: "pass", 4: "pass", 5: "pass", 6: "pass" }, g);
      await printLines(TEST + "\n", PASS_OUT, g);
      await sleep(2000, g);
    },
    async function ship(g) {
      codeEl.innerHTML = codeHTML(FIXED, { vec: "PPPP" });
      await typeSay(SAY[4], g);
      await sleep(300, g);
      await typeCmd("", PY, g);
      await sleep(240, g);
      termEl.innerHTML = termHTML(PY + "\nCompiled to score.py");
      await sleep(320, g);
      await typeCmd(PY + "\nCompiled to score.py\n", JS, g);
      await sleep(240, g);
      termEl.innerHTML = termHTML(SHIP_TERM);
      await sleep(5200, g);
    },
  ];

  async function run(from) {
    var g = ++gen;
    due = null;
    flush();
    try {
      for (;;) {
        for (var i = from; i < RUN.length; i++) {
          setStep(i, LEN[i]);
          if (reduce) {
            paint(i);
            await sleep(3800, g);
          } else await RUN[i](g);
        }
        from = 0;
        cycles++;
        if (cycles >= 3 || reduce) break;
        await rewind(g);
      }
      paint(4);
      setPlaying(false);
    } catch (e) {
      if (e !== ABORT) throw e;
    }
  }
  async function rewind(g) {
    var from = (stepIdx + Math.max(0, Math.min(1, (vnow - stepStart) / stepLen))) / 5;
    rw = { t0: due != null ? due : vnow, from: from, len: 520 };
    root.classList.add("is-rewinding");
    try {
      await sleep(520, g);
    } finally {
      rw = null;
      root.classList.remove("is-rewinding");
    }
    codeEl.innerHTML = "";
    stepIdx = 0;
    stepStart = due != null ? due : vnow;
    stepLen = 1e9;
  }

  /* ----------------------------------------------------------- controls */
  function jump(i) {
    gen++;
    due = null;
    flush();
    rw = null;
    root.classList.remove("is-rewinding");
    setPlaying(false);
    paint(i);
  }
  steps.forEach(function (b, i) {
    b.disabled = false;
    b.addEventListener("click", function () {
      jump(i);
    });
  });
  toggle.hidden = false;
  restart.hidden = false;
  toggle.addEventListener("click", function () {
    if (playing) {
      setPlaying(false);
      return;
    }
    // Resume a paused run; after a jump or the end, play on from the next step.
    var pending = waiters.some(function (w) {
      return w.g === gen;
    });
    setPlaying(true);
    if (!pending) {
      cycles = 2;
      run(stepIdx >= 4 ? 0 : stepIdx + 1);
    }
  });
  restart.addEventListener("click", function () {
    cycles = 2;
    rw = null;
    root.classList.remove("is-rewinding");
    setPlaying(true);
    run(0);
  });

  if ("IntersectionObserver" in global) {
    new IntersectionObserver(
      function (es) {
        visible = es[0].isIntersecting;
        wake();
      },
      { threshold: 0.15 }
    ).observe(root);
  }
  root.classList.add("is-live");

  if (reduce) {
    paint(4);
    setPlaying(false);
    return;
  }
  // Open on the finished capture and hold it long enough to read, then rewind
  // and play it. First size each step from its own sleeps so the cursor
  // crosses each boundary on time.
  (async function () {
    dry = true;
    for (var i = 0; i < RUN.length; i++) {
      dryTotal = 0;
      await RUN[i](gen);
      LEN[i] = dryTotal;
    }
    dry = false;
    paint(4);
    setPlaying(true);
    var g = gen;
    try {
      await sleep(POSTER_MS, g);
      await rewind(g);
      run(0);
    } catch (e) {
      if (e !== ABORT) throw e;
    }
  })();
})(typeof window !== "undefined" ? window : globalThis);
