/* Interactive explainers shared by both site versions.
 *
 * Each demo owns its logic and fills "slots" inside a container the page
 * provides, so the two designs can lay the same behaviour out differently:
 *
 *   <div data-demo="score">  <pre data-slot="code"></pre>  <pre data-slot="out"></pre> </div>
 *
 * Every message a demo prints was captured from the real Geno 0.5.0-rc.1
 * CLI (geno check / geno test -v / geno run) for the same program; paths are
 * shortened to the bare file name.
 */
(function (global) {
  "use strict";

  var H = global.GenoHighlight;
  var reduceMotion = global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function $(root, sel) {
    return root.querySelector(sel);
  }
  function slot(root, name) {
    return root.querySelector('[data-slot="' + name + '"]');
  }
  function esc(s) {
    return H.escape(String(s));
  }
  function lineHTML(code) {
    return code
      .split("\n")
      .map(function (l, i) {
        return '<span class="ln" data-ln="' + (i + 1) + '">' + (H.highlight(l, "geno") || " ") + "</span>";
      })
      .join("\n");
  }
  function onVisible(el, fn, opts) {
    if (!("IntersectionObserver" in global)) {
      fn();
      return;
    }
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) {
            fn(e);
            if (!(opts && opts.repeat)) io.disconnect();
          }
        });
      },
      { threshold: (opts && opts.threshold) || 0.25 }
    );
    io.observe(el);
    return io;
  }

  /* ------------------------------------------------------------------ */
  /* Scrubbable numbers                                                  */
  /* ------------------------------------------------------------------ */
  function scrubber(name, value, opts) {
    return (
      '<span class="scrub" role="slider" tabindex="0" data-scrub="' + name + '"' +
      ' aria-label="' + esc(opts.label) + '" aria-valuemin="' + opts.min + '" aria-valuemax="' + opts.max + '"' +
      ' aria-valuenow="' + value + '" data-min="' + opts.min + '" data-max="' + opts.max + '">' +
      '<span class="tok-num">' + value + "</span></span>"
    );
  }

  function bindScrub(el, onChange) {
    var min = +el.dataset.min;
    var max = +el.dataset.max;
    function set(v) {
      v = Math.max(min, Math.min(max, Math.round(v)));
      if (String(v) === el.textContent) return;
      el.querySelector(".tok-num").textContent = v;
      el.setAttribute("aria-valuenow", v);
      onChange(v);
    }
    var startX = 0;
    var startV = 0;
    var dragging = false;
    el.addEventListener("pointerdown", function (e) {
      dragging = true;
      startX = e.clientX;
      startV = +el.textContent;
      el.setPointerCapture(e.pointerId);
      el.classList.add("is-dragging");
      document.documentElement.classList.add("is-scrubbing");
      e.preventDefault();
    });
    el.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      set(startV + (e.clientX - startX) / 3);
    });
    function end() {
      dragging = false;
      el.classList.remove("is-dragging");
      document.documentElement.classList.remove("is-scrubbing");
    }
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("keydown", function (e) {
      var step = e.shiftKey ? 10 : 1;
      var v = +el.textContent;
      if (e.key === "ArrowRight" || e.key === "ArrowUp") set(v + step);
      else if (e.key === "ArrowLeft" || e.key === "ArrowDown") set(v - step);
      else if (e.key === "Home") set(min);
      else if (e.key === "End") set(max);
      else return;
      e.preventDefault();
    });
    // Wheel nudges for trackpads, only while focused so the page still scrolls.
    el.addEventListener(
      "wheel",
      function (e) {
        if (document.activeElement !== el) return;
        e.preventDefault();
        set(+el.textContent + (e.deltaY < 0 ? 1 : -1));
      },
      { passive: false }
    );
  }

  /* ------------------------------------------------------------------ */
  /* 1. Examples are the specification                                   */
  /* ------------------------------------------------------------------ */
  var SCORE_SRC = [
    "func score_label(score: Int) -> String",
    "    requires score >= 0",
    '    example {ex0} -> "excellent"',
    '    example {ex1} -> "passing"',
    '    example {ex2} -> "needs work"',
    "",
    "    if score >= {hi} then",
    '        return "excellent"',
    "    else if score >= {mid} then",
    '        return "passing"',
    "    else",
    '        return "needs work"',
    "    end if",
    "end func",
  ];
  var SCORE_EXPECT = ["excellent", "passing", "needs work"];

  function scoreDemo(root) {
    var state = {
      ex0: 95,
      ex1: 70,
      ex2: 40,
      hi: +(root.dataset.hi || 90),
      mid: +(root.dataset.mid || 60),
    };
    var meta = {
      ex0: { min: -20, max: 120, label: "First example input" },
      ex1: { min: -20, max: 120, label: "Second example input" },
      ex2: { min: -20, max: 120, label: "Third example input" },
      hi: { min: 0, max: 120, label: "Threshold for excellent" },
      mid: { min: 0, max: 120, label: "Threshold for passing" },
    };
    var code = slot(root, "code");
    var out = slot(root, "out");
    var exLine = { ex0: 3, ex1: 4, ex2: 5 };

    code.innerHTML = SCORE_SRC.map(function (line, i) {
      var html = line
        .split(/(\{\w+\})/)
        .map(function (part) {
          var m = part.match(/^\{(\w+)\}$/);
          if (m) return scrubber(m[1], state[m[1]], meta[m[1]]);
          return H.highlight(part, "geno");
        })
        .join("");
      var tail = "";
      var exm = line.match(/\{(ex\d)\}/);
      if (exm) tail = '<span class="ex-status" data-ex-status="' + exm[1].slice(2) + '" aria-live="polite"></span>';
      return '<span class="ln" data-ln="' + (i + 1) + '">' + (html || " ") + tail + "</span>";
    }).join("\n");

    function label(s) {
      if (s < 0) return null;
      if (s >= state.hi) return "excellent";
      if (s >= state.mid) return "passing";
      return "needs work";
    }

    function render() {
      var passed = 0;
      var fails = [];
      [0, 1, 2].forEach(function (i) {
        var input = state["ex" + i];
        var got = label(input);
        var ok = got === SCORE_EXPECT[i];
        var st = $(code, '[data-ex-status="' + i + '"]');
        var ln = $(code, '[data-ln="' + exLine["ex" + i] + '"]');
        ln.classList.toggle("is-pass", ok);
        ln.classList.toggle("is-fail", !ok);
        if (ok) {
          passed++;
          st.className = "ex-status is-pass";
          st.textContent = "pass";
        } else if (got === null) {
          st.className = "ex-status is-fail";
          st.textContent = "requires failed";
          fails.push("score_label: Runtime error: score.geno:2:14: Runtime Error: Precondition failed for score_label: requires clause evaluated to false");
        } else {
          st.className = "ex-status is-fail";
          st.textContent = 'got "' + got + '"';
          fails.push('score_label: expected "' + SCORE_EXPECT[i] + '", got "' + got + '"');
        }
      });
      var failed = 3 - passed;
      var text = "$ geno test -v score.geno\n";
      text += (failed ? "  FAIL  " : "  PASS  ") + "score.geno (" + passed + "/3) 4ms\n";
      fails.forEach(function (f) {
        text += "        x " + f + "\n";
      });
      text += "\n" + passed + " passed, " + failed + " failed, in 0.02s";
      out.innerHTML = H.highlight(text, "sh");
      root.classList.toggle("is-failing", failed > 0);
      root.dataset.passed = passed;
      var sum = slot(root, "summary");
      if (sum) sum.textContent = failed ? passed + " of 3 examples hold" : "All 3 examples hold";
      root.dispatchEvent(new CustomEvent("demo:update", { detail: { passed: passed, failed: failed } }));
    }

    code.querySelectorAll(".scrub").forEach(function (el) {
      bindScrub(el, function (v) {
        state[el.dataset.scrub] = v;
        render();
      });
    });
    var reset = $(root, "[data-reset]");
    if (reset)
      reset.addEventListener("click", function () {
        ["ex0", "ex1", "ex2", "hi", "mid"].forEach(function (k) {
          var v = { ex0: 95, ex1: 70, ex2: 40, hi: 90, mid: 60 }[k];
          state[k] = v;
          var el = $(code, '[data-scrub="' + k + '"]');
          el.querySelector(".tok-num").textContent = v;
          el.setAttribute("aria-valuenow", v);
        });
        render();
      });
    render();
  }

  /* ------------------------------------------------------------------ */
  /* 2. Effects need permission                                          */
  /* ------------------------------------------------------------------ */
  var CAPS_SRC = [
    "func main() -> Unit",
    '    let notes = fs_read_text("notes.txt")',
    '    let count = length(split(notes, "\\n"))',
    '    let user = env_get_or("USER", "anon")',
    '    print(f"{user} has {count} notes")',
    "    return ()",
    "end func",
  ];
  // Call order matters: the first ungranted capability is the one reported.
  var CAPS_CALLS = [
    { cap: "fs", fn: "fs_read_text", line: 2 },
    { cap: "env", fn: "env_get_or", line: 4 },
    { cap: "print", fn: "print", line: 5 },
  ];

  function capsDemo(root) {
    var code = slot(root, "code");
    var out = slot(root, "out");
    var granted = new Set((root.dataset.granted || "print").split(",").filter(Boolean));
    code.innerHTML = lineHTML(CAPS_SRC.join("\n"));
    CAPS_CALLS.forEach(function (c) {
      var ln = $(code, '[data-ln="' + c.line + '"]');
      var tag = document.createElement("span");
      tag.className = "cap-tag";
      tag.dataset.capTag = c.cap;
      ln.appendChild(tag);
    });
    var toggles = root.querySelectorAll("[data-cap]");

    function render() {
      toggles.forEach(function (t) {
        t.setAttribute("aria-pressed", granted.has(t.dataset.cap) ? "true" : "false");
      });
      var list = ["print", "fs", "env"].filter(function (c) {
        return granted.has(c);
      });
      var cmd = list.length ? "$ geno run --unsafe --cap " + list.join(",") + " notes.geno" : "$ geno run notes.geno";
      var denied = null;
      for (var i = 0; i < CAPS_CALLS.length; i++) {
        if (!granted.has(CAPS_CALLS[i].cap)) {
          denied = CAPS_CALLS[i];
          break;
        }
      }
      // The sandboxed default lane grants print, clock and random only.
      if (!list.length) denied = CAPS_CALLS[0];
      var text = cmd + "\n";
      if (!denied) text += "ada has 3 notes";
      else if (!list.length) text += "Runtime Error: Capability denied: 'fs_read_text' requires '--cap fs'";
      else
        text +=
          "Stack trace (most recent call last):\n  in main, notes.geno:1:1\nRuntime Error: Capability denied: '" +
          denied.fn +
          "' requires a capability that was not granted";
      out.innerHTML = H.highlight(text, "sh");
      var reached = true;
      code.querySelectorAll(".ln").forEach(function (ln) {
        var n = +ln.dataset.ln;
        ln.classList.remove("is-denied", "is-granted", "is-unreached");
        if (n === 1) return;
        var call = CAPS_CALLS.filter(function (c) {
          return c.line === n;
        })[0];
        if (!reached) ln.classList.add("is-unreached");
        if (call) {
          var tag = $(ln, ".cap-tag");
          if (denied && denied.line === n) {
            ln.classList.add("is-denied");
            tag.textContent = "denied: --cap " + call.cap;
            reached = false;
          } else if (reached) {
            ln.classList.add("is-granted");
            tag.textContent = "--cap " + call.cap;
          } else tag.textContent = "";
        }
      });
      root.classList.toggle("is-failing", !!denied);
    }
    toggles.forEach(function (t) {
      t.addEventListener("click", function () {
        var c = t.dataset.cap;
        if (granted.has(c)) granted.delete(c);
        else granted.add(c);
        render();
      });
    });
    render();
  }

  /* ------------------------------------------------------------------ */
  /* 3. Every case, accounted for                                        */
  /* ------------------------------------------------------------------ */
  var MATCH_HEAD = [
    "type Shape = Circle(radius: Float)",
    "           | Rectangle(width: Float, height: Float)",
    "           | Triangle(base: Float, height: Float)",
    "",
    "func area(s: Shape) -> Float",
    "    example Circle(1.0) -> 3.14159",
    "    match s with",
  ];
  var MATCH_ARMS = [
    { id: "Circle", src: "        | Circle(r) -> return 3.14159 * r * r", missing: "Circle(<float>)" },
    { id: "Rectangle", src: "        | Rectangle(w, h) -> return w * h", missing: "Rectangle(<float>, <float>)" },
    { id: "Triangle", src: "        | Triangle(b, h) -> return 0.5 * b * h", missing: "Triangle(<float>, <float>)" },
    { id: "_", src: "        | _ -> return 0.0", missing: null },
  ];
  var MATCH_TAIL = ["    end match", "end func"];

  function matchDemo(root) {
    var code = slot(root, "code");
    var out = slot(root, "out");
    var on = new Set((root.dataset.arms || "Circle,Rectangle").split(","));
    var html = MATCH_HEAD.map(function (l, i) {
      return '<span class="ln" data-ln="' + (i + 1) + '">' + (H.highlight(l, "geno") || " ") + "</span>";
    });
    MATCH_ARMS.forEach(function (arm) {
      html.push(
        '<span class="ln arm" data-arm-line="' + arm.id + '" role="button" tabindex="0" aria-pressed="false" title="Toggle this match arm">' +
          arm.src.match(/^\s*/)[0] + '<span class="arm-code">' + H.highlight(arm.src.trim(), "geno") + "</span>" +
          '<span class="arm-tag"></span></span>'
      );
    });
    MATCH_TAIL.forEach(function (l) {
      html.push('<span class="ln">' + H.highlight(l, "geno") + "</span>");
    });
    code.innerHTML = html.join("\n");
    var toggles = root.querySelectorAll("[data-arm]");

    function flip(id) {
      if (on.has(id)) {
        // Keep at least one arm so the program stays a program.
        if (on.size === 1) return;
        on.delete(id);
      } else on.add(id);
      render();
    }

    function render() {
      var missing = MATCH_ARMS.filter(function (a) {
        return a.missing && !on.has(a.id);
      }).map(function (a) {
        return a.missing;
      });
      if (on.has("_")) missing = [];
      MATCH_ARMS.forEach(function (arm) {
        var ln = $(code, '[data-arm-line="' + arm.id + '"]');
        var active = on.has(arm.id);
        ln.classList.toggle("is-off", !active);
        ln.setAttribute("aria-pressed", active ? "true" : "false");
        $(ln, ".arm-tag").textContent = active ? "" : "removed";
      });
      toggles.forEach(function (t) {
        t.setAttribute("aria-pressed", on.has(t.dataset.arm) ? "true" : "false");
      });
      var text = "$ geno check shapes.geno\n";
      if (!missing.length) text += "Type check passed: shapes.geno\n  2 definitions, 1 modules";
      else text += "Type Error: shapes.geno:7:5: Type Error: Non-exhaustive patterns: missing " + missing.join(", ") + "\n      match s with\n      ^";
      out.innerHTML = H.highlight(text, "sh");
      root.classList.toggle("is-failing", missing.length > 0);
      var matchLn = $(code, '[data-ln="7"]');
      matchLn.classList.toggle("is-fail", missing.length > 0);
    }
    code.querySelectorAll("[data-arm-line]").forEach(function (ln) {
      ln.addEventListener("click", function () {
        flip(ln.dataset.armLine);
      });
      ln.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          flip(ln.dataset.armLine);
        }
      });
    });
    toggles.forEach(function (t) {
      t.addEventListener("click", function () {
        flip(t.dataset.arm);
      });
    });
    render();
  }

  /* ------------------------------------------------------------------ */
  /* 4. Every block says where it ends (streamed like model output)      */
  /* ------------------------------------------------------------------ */
  var BLOCKS_SRC = [
    "func count_vowels(text: String) -> Int",
    '    example "geno" -> 2',
    '    example "rhythm" -> 0',
    "    var total = 0",
    "    for ch: String in to_chars(text) do",
    '        if string_contains("aeiou", ch) then',
    "            total = total + 1",
    "        end if",
    "    end for",
    "    return total",
    "end func",
  ].join("\n");
  // Change points from geno.constraints.get_unclosed_blocks() on every
  // prefix of BLOCKS_SRC (innermost block first).
  var BLOCKS_STACK = [
    [4, ["func"]],
    [114, ["for", "func"]],
    [157, ["if", "for", "func"]],
    [236, ["for", "func"]],
    [248, ["func"]],
    [274, []],
  ];

  function stackAt(i) {
    var s = [];
    for (var k = 0; k < BLOCKS_STACK.length; k++) {
      if (BLOCKS_STACK[k][0] <= i) s = BLOCKS_STACK[k][1];
    }
    return s;
  }

  function blocksDemo(root) {
    var code = slot(root, "code");
    var stackEl = slot(root, "stack");
    var statusEl = slot(root, "status");
    var tokens = BLOCKS_SRC.match(/\s+|[A-Za-z_]\w*|"[^"]*"|\d+|->|./g);
    var timer = null;
    var running = false;
    var lastKey = "";

    function renderStack(pos, done) {
      var s = stackAt(pos);
      var key = s.join(">") + (done ? "!" : "");
      if (key === lastKey) return;
      var popped = lastKey && lastKey.replace("!", "").split(">").length > s.length;
      lastKey = key;
      if (stackEl) {
        stackEl.innerHTML = s.length
          ? s
              .map(function (b, i) {
                return '<span class="blk' + (i === 0 ? " is-top" : "") + '" data-blk="' + b + '"><span class="blk-k">end</span> ' + b + "</span>";
              })
              .join("")
          : '<span class="blk is-empty">' + (done ? "nothing left open" : "no open blocks") + "</span>";
        if (popped) {
          stackEl.classList.remove("did-pop");
          void stackEl.offsetWidth;
          stackEl.classList.add("did-pop");
        }
      }
      if (statusEl) {
        statusEl.innerHTML = done
          ? '<span class="tok-pass">valid program</span> <span class="dim">· 2 passed, 0 failed</span>'
          : "need_to_close=" +
            "[" +
            s
              .map(function (b) {
                return "<span class=\"tok-str\">'" + b + "'</span>";
              })
              .join(", ") +
            "]";
      }
    }

    function paint(n) {
      var text = tokens.slice(0, n).join("");
      code.innerHTML = H.highlight(text, "geno") + (n < tokens.length ? '<span class="caret" aria-hidden="true"></span>' : "");
      renderStack(text.length, n >= tokens.length);
    }

    function run() {
      if (running) return;
      running = true;
      var n = 0;
      function tick() {
        n++;
        paint(n);
        if (n < tokens.length) {
          var t = tokens[n - 1];
          var delay = /\n/.test(t) ? 140 : /^\s+$/.test(t) ? 10 : 36 + Math.random() * 50;
          timer = setTimeout(tick, delay);
        } else {
          running = false;
          if (root.dataset.loop !== "false")
            timer = setTimeout(function () {
              lastKey = "";
              run();
            }, 5200);
        }
      }
      tick();
    }

    // At rest the finished program is shown; streaming starts once it is seen.
    paint(tokens.length);
    if (reduceMotion) return;
    onVisible(root, function () {
      lastKey = "";
      run();
    }, { threshold: 0.35 });
    var replay = $(root, "[data-replay]");
    if (replay)
      replay.addEventListener("click", function () {
        clearTimeout(timer);
        running = false;
        lastKey = "";
        run();
      });
  }

  /* ------------------------------------------------------------------ */
  /* 5. Benchmark beeswarm                                               */
  /* ------------------------------------------------------------------ */
  // benchmarks/RESULTS.md, measured 2026-06-10 (Python 3.11, Linux):
  // [problem, geno ms, python ms, ratio]
  var BENCH = [["fib_rec_25",12.61,9.68,1.3],["ackermann_3_6",18.64,18.99,0.98],["collatz_batch",30.07,21.5,1.4],["choose_recursive",34.1,27.73,1.23],["nested_loop_sum",9.5,5.38,1.77],["sum_of_squares",8.12,3.44,2.36],["modpow_batch",14.39,5.32,2.71],["catalan_12",27.29,18.96,1.44],["while_countdown",9.67,4.98,1.94],["sum_divisors",6.56,4.23,1.55],["fizzbuzz_count",6.76,5.55,1.22],["conditional_chain",7.94,4.79,1.66],["multi_return",9.12,4.84,1.88],["nested_fn_calls",8.23,4.84,1.7],["deeply_nested_ifs",10.85,6.78,1.6],["accumulator_3var",8.05,3.4,2.37],["many_locals",5.2,4.38,1.19],["mixed_arith",7.54,3.96,1.91],["mutual_recursion",10.14,11.01,0.92],["harmonic_int",6.44,2.96,2.17],["fib_iterative",5.47,3.53,1.55],["triangular_batch",11.79,6.29,1.87],["list_sum",6.99,4.77,1.46],["list_max",6.53,6.18,1.06],["list_filter_count",11.85,10.3,1.15],["list_dot_product",10.71,5.72,1.87],["list_contains",46.1,45.66,1.01],["binary_search",51.7,31.47,1.64],["list_count_val",8.89,8.68,1.02],["list_map_manual",7.63,5.35,1.43],["list_index_of",21.1,14.89,1.42],["str_char_scan",6.23,3.74,1.66],["str_count_vowels",11.14,8.89,1.25],["str_length_batch",7.34,4.08,1.8],["compose_apply",6.21,4.0,1.55],["map_filter_sum",10.34,6.1,1.7],["matrix_trace",6.92,3.22,2.15],["abs_batch",7.23,4.42,1.64],["is_even_batch",7.26,6.18,1.17],["nested_fn_chain",7.9,5.31,1.49],["sum_cubes",6.7,2.57,2.6],["interleaved_ops",6.52,3.43,1.9],["digit_root_batch",7.2,4.53,1.59],["max_min_batch",8.4,5.06,1.66],["alternating_sum",6.42,3.59,1.79],["xor_accumulate",14.93,10.5,1.42],["sum_multiples",13.94,8.69,1.6],["list_scan_sum",10.37,6.8,1.52],["nested_300",12.23,6.44,1.9],["countdown_sum",9.24,4.61,2.01],["pascal_row",41.95,14.76,2.84],["running_max",14.9,10.28,1.45],["sum_abs_diff",7.08,4.07,1.74]];

  function cssVar(el, name, fallback) {
    var v = getComputedStyle(el).getPropertyValue(name).trim();
    return v || fallback;
  }

  function benchChart(root) {
    var host = slot(root, "chart");
    var tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.setAttribute("role", "status");
    tip.hidden = true;
    root.style.position = root.style.position || "relative";
    root.appendChild(tip);
    var drawn = false;
    var animate = !reduceMotion;

    function layout(width) {
      var compact = width < 520;
      var m = { l: 14, r: 14, t: compact ? 46 : 40, b: 34 };
      var x0 = 0.8;
      var x1 = 3.0;
      var w = width - m.l - m.r;
      var sx = function (v) {
        return m.l + ((v - x0) / (x1 - x0)) * w;
      };
      var r = compact ? 4.5 : 5.5;
      var gap = 1.5;
      // Greedy beeswarm: place each dot at the smallest |offset| that is free.
      var sorted = BENCH.map(function (d, i) {
        return { d: d, i: i, x: sx(d[3]) };
      }).sort(function (a, b) {
        return a.x - b.x;
      });
      var placed = [];
      sorted.forEach(function (p) {
        var step = 0;
        for (;;) {
          var cand = step === 0 ? 0 : (step % 2 ? 1 : -1) * Math.ceil(step / 2) * (r * 2 + gap) * 0.88;
          var clash = placed.some(function (q) {
            var dx = q.x - p.x;
            var dy = q.y - cand;
            return dx * dx + dy * dy < (2 * r + gap) * (2 * r + gap);
          });
          if (!clash) {
            p.y = cand;
            break;
          }
          step++;
        }
        placed.push(p);
      });
      var spread = placed.reduce(function (a, p) {
        return Math.max(a, Math.abs(p.y));
      }, 0);
      var plotH = spread * 2 + r * 2 + 24;
      var height = m.t + plotH + m.b;
      var cy = m.t + plotH / 2;
      return { m: m, sx: sx, r: r, pts: placed, height: height, cy: cy, compact: compact, x0: x0, x1: x1 };
    }

    function draw() {
      var width = Math.max(300, host.clientWidth || 600);
      var L = layout(width);
      var ink = cssVar(root, "--chart-ink", "#0F172A");
      var muted = cssVar(root, "--chart-muted", "#64748B");
      var grid = cssVar(root, "--chart-grid", "#D5DBD9");
      var accent = cssVar(root, "--chart-accent", "#0D9488");
      var over = cssVar(root, "--chart-over", "#94A3B8");
      var surface = cssVar(root, "--chart-surface", "#FFFFFF");
      var font = cssVar(root, "--chart-font", "ui-monospace, monospace");
      var ticks = [1, 1.5, 2, 2.5, 3];
      var s = '<svg viewBox="0 0 ' + width + " " + L.height + '" width="100%" height="' + L.height + '" role="img" aria-label="Beeswarm of 53 runtime ratios, Geno compiled time divided by hand-written Python time. Median 1.60x; 45 of 53 at or under 2x." style="font-family:' + font + '">';
      var base = L.height - L.m.b;
      // grid + ticks
      ticks.forEach(function (t) {
        var x = L.sx(t);
        s += '<line x1="' + x + '" x2="' + x + '" y1="' + (L.m.t - 6) + '" y2="' + base + '" stroke="' + grid + '" stroke-width="1"/>';
        s += '<text x="' + x + '" y="' + (base + 20) + '" fill="' + muted + '" font-size="11" text-anchor="middle">' + t.toFixed(1) + "×</text>";
      });
      s += '<line x1="' + L.m.l + '" x2="' + (L.sx(L.x1)) + '" y1="' + base + '" y2="' + base + '" stroke="' + grid + '" stroke-width="1"/>';
      // reference: target and median
      var tx = L.sx(2.0);
      var mx = L.sx(1.6);
      s += '<line x1="' + tx + '" x2="' + tx + '" y1="' + (L.m.t - 22) + '" y2="' + base + '" stroke="' + ink + '" stroke-width="1.5"/>';
      s += '<text x="' + (tx + 6) + '" y="' + (L.m.t - 12) + '" fill="' + ink + '" font-size="11" font-weight="600">target ≤ 2×</text>';
      s += '<line x1="' + mx + '" x2="' + mx + '" y1="' + (L.m.t - 22) + '" y2="' + base + '" stroke="' + accent + '" stroke-width="1.5"/>';
      s += '<text x="' + (mx - 6) + '" y="' + (L.m.t - 12) + '" fill="' + ink + '" font-size="11" text-anchor="end">median 1.60×</text>';
      // dots
      L.pts.forEach(function (p, k) {
        var d = p.d;
        var col = d[3] <= 2.0 ? accent : over;
        var cy = L.cy + p.y;
        s +=
          '<circle class="dot" data-i="' + p.i + '" data-k="' + k + '" cx="' + p.x.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="' + L.r + '" fill="' + col + '" stroke="' + surface + '" stroke-width="2"' +
          ' tabindex="0" aria-label="' + d[0] + ": " + d[3].toFixed(2) + 'x"/>';
      });
      s += "</svg>";
      host.innerHTML = s;
      drawn = true;
      host.querySelectorAll(".dot").forEach(function (c) {
        function show() {
          var d = BENCH[+c.dataset.i];
          tip.innerHTML =
            "<strong>" + d[0].replace(/_/g, " ") + "</strong><span>" + d[3].toFixed(2) + "× Python</span><span class=\"dim\">Geno " + d[1].toFixed(2) + " ms · Python " + d[2].toFixed(2) + " ms</span>";
          tip.hidden = false;
          var hb = host.getBoundingClientRect();
          var rb = root.getBoundingClientRect();
          var cx = +c.getAttribute("cx") + (hb.left - rb.left);
          var cyy = +c.getAttribute("cy") + (hb.top - rb.top);
          var tw = tip.offsetWidth;
          var left = Math.max(4, Math.min(rb.width - tw - 4, cx - tw / 2));
          tip.style.left = left + "px";
          tip.style.top = cyy - tip.offsetHeight - 14 + "px";
          c.setAttribute("r", L.r + 2);
        }
        function hide() {
          tip.hidden = true;
          c.setAttribute("r", L.r);
        }
        c.addEventListener("pointerenter", show);
        c.addEventListener("pointerleave", hide);
        c.addEventListener("focus", show);
        c.addEventListener("blur", hide);
      });
    }

    var tableSlot = slot(root, "table");
    if (tableSlot) {
      var rows = BENCH.slice()
        .sort(function (a, b) {
          return a[3] - b[3];
        })
        .map(function (d) {
          return "<tr><td>" + d[0] + "</td><td>" + d[1].toFixed(2) + "</td><td>" + d[2].toFixed(2) + "</td><td>" + d[3].toFixed(2) + "×</td></tr>";
        })
        .join("");
      tableSlot.innerHTML = "<table><thead><tr><th>Problem</th><th>Geno ms</th><th>Python ms</th><th>Ratio</th></tr></thead><tbody>" + rows + "</tbody></table>";
    }

    // Draw at rest so the figure is complete without scrolling; the first
    // time it comes into view, the dots settle into place.
    var started = true;
    draw();
    if (animate && host.animate) {
      onVisible(root, function () {
        host.querySelectorAll(".dot").forEach(function (c) {
          var k = +c.dataset.k;
          c.animate(
            [
              { opacity: 0, transform: "translateY(" + (-22 - (k % 5) * 4) + "px)" },
              { opacity: 1, transform: "none" },
            ],
            { duration: 700, delay: k * 16, easing: "cubic-bezier(.2,.9,.25,1.15)", fill: "backwards" }
          );
        });
      }, { threshold: 0.35 });
    }
    var rw = 0;
    if ("ResizeObserver" in global)
      new ResizeObserver(function () {
        var w = host.clientWidth;
        if (started && Math.abs(w - rw) > 4) {
          rw = w;
          draw();
        }
      }).observe(host);
    global.addEventListener("themechange", function () {
      if (started) draw();
    });
    if (global.matchMedia)
      global.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
        if (started) draw();
      });
  }

  /* ------------------------------------------------------------------ */
  /* Small utilities: copy buttons, tabs                                 */
  /* ------------------------------------------------------------------ */
  function copyButtons(root) {
    (root || document).querySelectorAll("[data-copy]").forEach(function (btn) {
      if (btn.dataset.copyBound) return;
      btn.dataset.copyBound = "1";
      btn.addEventListener("click", function () {
        var text = btn.dataset.copy;
        if (!text && btn.dataset.copyTarget) {
          var t = document.querySelector(btn.dataset.copyTarget);
          text = t ? t.innerText : "";
        }
        function done() {
          btn.setAttribute("data-copied", "");
          setTimeout(function () {
            btn.removeAttribute("data-copied");
          }, 1600);
        }
        try {
          navigator.clipboard.writeText(text).then(done, function () {
            fallback(text);
            done();
          });
        } catch (e) {
          fallback(text);
          done();
        }
      });
    });
    function fallback(text) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch (e) {
        /* selection is left for the user */
      }
      document.body.removeChild(ta);
    }
  }

  function tabs(root) {
    (root || document).querySelectorAll("[data-tabs]").forEach(function (group) {
      var list = group.querySelectorAll('[role="tab"]');
      function select(tab, focus) {
        list.forEach(function (t) {
          var on = t === tab;
          t.setAttribute("aria-selected", on ? "true" : "false");
          t.tabIndex = on ? 0 : -1;
          var panel = document.getElementById(t.getAttribute("aria-controls"));
          if (panel) panel.hidden = !on;
        });
        if (focus) tab.focus();
        group.dispatchEvent(new CustomEvent("tabs:change", { detail: { tab: tab } }));
      }
      list.forEach(function (t, i) {
        t.addEventListener("click", function () {
          select(t);
        });
        t.addEventListener("keydown", function (e) {
          var k = e.key;
          var n = list.length;
          if (k === "ArrowRight") select(list[(i + 1) % n], true);
          else if (k === "ArrowLeft") select(list[(i - 1 + n) % n], true);
          else return;
          e.preventDefault();
        });
      });
    });
  }

  // Copy buttons for long-form code blocks. Shell blocks copy only the
  // commands (lines after a "$ " prompt), not their output.
  function codeCopy(selector) {
    document.querySelectorAll(selector).forEach(function (pre) {
      if (pre.dataset.copyReady) return;
      pre.dataset.copyReady = "1";
      var code = pre.querySelector("code");
      var raw = (code || pre).textContent;
      var isShell = code && code.getAttribute("data-lang") === "sh";
      var text = raw;
      if (isShell) {
        var cmds = raw.split("\n").filter(function (l) {
          return /^\$ /.test(l);
        });
        if (cmds.length) text = cmds.map(function (l) { return l.slice(2); }).join("\n");
      }
      var wrap = document.createElement("div");
      wrap.className = "pre-wrap";
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      var b = document.createElement("button");
      b.type = "button";
      b.className = "pre-copy";
      b.setAttribute("aria-label", isShell ? "Copy commands" : "Copy code");
      b.dataset.copy = text;
      b.innerHTML = '<span class="pc-a">Copy</span><span class="pc-b">Copied</span>';
      wrap.appendChild(b);
    });
    copyButtons(document);
  }

  function init(root) {
    root = root || document;
    root.querySelectorAll("[data-demo]").forEach(function (el) {
      if (el.dataset.demoBound) return;
      el.dataset.demoBound = "1";
      var kind = el.dataset.demo;
      try {
        if (kind === "score") scoreDemo(el);
        else if (kind === "caps") capsDemo(el);
        else if (kind === "match") matchDemo(el);
        else if (kind === "blocks") blocksDemo(el);
        else if (kind === "bench") benchChart(el);
      } catch (e) {
        if (global.console) console.error("demo " + kind + " failed", e);
      }
    });
    copyButtons(root);
    tabs(root);
    H.highlightAll(root);
  }

  global.GenoDemos = { init: init, copyButtons: copyButtons, codeCopy: codeCopy, tabs: tabs, onVisible: onVisible, bench: BENCH, reduceMotion: reduceMotion };
})(window);
