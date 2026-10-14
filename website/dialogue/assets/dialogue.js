/* Dialogue edition: page behaviour. Shared demos live in ../shared/js. */
(function () {
  "use strict";

  var H = window.GenoHighlight;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function $(sel, root) {
    return (root || document).querySelector(sel);
  }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  /* nav ------------------------------------------------------------------ */
  var nav = $("[data-nav]");
  var menu = $("[data-menu]");
  var drawer = $("#nav-drawer");
  if (menu && drawer) {
    menu.addEventListener("click", function () {
      var open = menu.getAttribute("aria-expanded") !== "true";
      menu.setAttribute("aria-expanded", String(open));
      drawer.hidden = !open;
    });
    drawer.addEventListener("click", function (e) {
      if (e.target.closest("a")) {
        menu.setAttribute("aria-expanded", "false");
        drawer.hidden = true;
      }
    });
  }

  /* rail: act index + running timecode ----------------------------------- */
  var rail = $("[data-rail]");
  var tc = $("[data-tc]");
  var hero = $(".hero");
  var credits = $(".credits");
  var acts = $$("[data-act]");
  var RUNTIME = 8 * 60; // seconds of "film" across the whole page
  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }
  function onScroll() {
    var y = window.scrollY;
    if (nav) nav.classList.toggle("is-scrolled", y > 12);
    if (!rail) return;
    var max = document.documentElement.scrollHeight - innerHeight;
    var p = max > 0 ? y / max : 0;
    var secs = p * RUNTIME;
    var frames = Math.floor((secs % 1) * 24);
    if (tc) tc.textContent = "00:" + pad(Math.floor(secs / 60)) + ":" + pad(Math.floor(secs % 60)) + ":" + pad(frames);
    var heroEnd = hero ? hero.offsetTop + hero.offsetHeight * 0.75 : 600;
    var creditsTop = credits ? credits.offsetTop - innerHeight * 0.4 : Infinity;
    rail.classList.toggle("is-on", y > heroEnd && y < creditsTop);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  if (rail && "IntersectionObserver" in window) {
    var links = {};
    $$("[data-rail-link]", rail).forEach(function (a) {
      links[a.dataset.railLink] = a;
    });
    var spy = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          Object.keys(links).forEach(function (id) {
            links[id].setAttribute("aria-current", id === e.target.id ? "true" : "false");
          });
        });
      },
      { rootMargin: "-45% 0px -50% 0px" }
    );
    acts.forEach(function (a) {
      spy.observe(a);
    });
  }

  /* typed Geno replies in each act ---------------------------------------- */
  function typeInto(el, text, speed) {
    return new Promise(function (resolve) {
      var i = 0;
      el.classList.add("is-typing");
      el.textContent = "";
      (function step() {
        i += 1 + Math.floor(Math.random() * 2);
        el.textContent = text.slice(0, i);
        if (i < text.length) setTimeout(step, speed + Math.random() * speed);
        else {
          setTimeout(function () {
            el.classList.remove("is-typing");
          }, 900);
          resolve();
        }
      })();
    });
  }
  if (!reduce && window.GenoDemos) {
    $$("[data-exchange]").forEach(function (ex) {
      var say = $("[data-type]", ex);
      if (!say) return;
      var text = say.textContent;
      window.GenoDemos.onVisible(
        ex,
        function () {
          setTimeout(function () {
            typeInto(say, text, 16);
          }, 650);
        },
        { threshold: 0.6 }
      );
    });
  }

  /* hero duet --------------------------------------------------------------- */
  var duet = $("[data-duet]");
  if (duet && !reduce) runDuet(duet);

  function runDuet(root) {
    var codeEl = $("[data-duet-code] code", root);
    var script = $("[data-script]", root);
    var hudOpen = $("[data-hud-open]", root);
    var verdict = $("[data-verdict]", root);
    var BUGGY = [
      "func score_label(score: Int) -> String",
      "    requires score >= 0",
      '    example 95 -> "excellent"',
      '    example 70 -> "passing"',
      '    example 40 -> "needs work"',
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
    // Real `geno test -v` / `geno compile` output for this program.
    var FAIL_OUT = '  FAIL  score.geno (2/3) 12ms\n        x score_label: expected "passing", got "needs work"';
    var PASS_OUT = "  PASS  score.geno (3/3) 4ms\n\n3 passed, 0 failed, in 0.02s";

    var visible = true;
    if ("IntersectionObserver" in window)
      new IntersectionObserver(function (es) {
        visible = es[0].isIntersecting;
      }).observe(root);

    // Pauses while the screen is scrolled away or the tab is hidden.
    function sleep(ms) {
      return new Promise(function (r) {
        var left = ms;
        var last = Date.now();
        (function tick() {
          var now = Date.now();
          if (visible && !document.hidden) left -= now - last;
          last = now;
          if (left <= 0) r();
          else setTimeout(tick, Math.min(100, Math.max(16, left)));
        })();
      });
    }
    function line(who) {
      var li = document.createElement("li");
      li.className = "sl sl-" + who + " is-new";
      li.innerHTML = "<b>" + (who === "model" ? "Model" : "Geno") + "</b>";
      script.appendChild(li);
      while (script.children.length > 7) script.removeChild(script.firstChild);
      return li;
    }
    async function modelSays(text) {
      var p = document.createElement("p");
      line("model").appendChild(p);
      for (var i = 1; i <= text.length; i++) {
        p.textContent = text.slice(0, i);
        await sleep(text[i - 1] === " " ? 30 : 18 + Math.random() * 22);
      }
    }
    function genoSays(text, out) {
      var li = line("geno");
      if (text) {
        var p = document.createElement("p");
        p.textContent = text;
        li.appendChild(p);
      }
      var pre = null;
      if (out != null) {
        pre = document.createElement("pre");
        pre.innerHTML = "<code>" + H.highlight(out, "sh") + "</code>";
        li.appendChild(pre);
      }
      return pre;
    }
    function setOut(pre, out) {
      pre.innerHTML = "<code>" + H.highlight(out, "sh") + "</code>";
    }
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
    function render(text, marks, caret) {
      marks = marks || {};
      var lines = text.split("\n");
      codeEl.innerHTML = lines
        .map(function (l, i) {
          var html = H.highlight(l, "geno") || " ";
          var mk = marks[i + 1];
          if (mk && mk.sel) html = html.replace('<span class="tok-num">' + mk.sel + "</span>", '<span class="tok-num sel">' + mk.sel + "</span>");
          var tail = caret && i === lines.length - 1 ? '<span class="caret"></span>' : "";
          return '<span class="ln' + (mk && mk.cls ? " " + mk.cls : "") + '">' + html + tail + "</span>";
        })
        .join("\n");
    }
    function setVerdict(state, label) {
      verdict.dataset.state = state;
      verdict.textContent = label;
    }
    function hud(stack) {
      hudOpen.textContent = stack.length ? stack.join(" › ") : "none";
    }
    async function stream(text) {
      var tokens = text.match(/\s+|[A-Za-z_]\w*|"[^"]*"|\d+|->|>=|./g);
      var acc = "";
      for (var i = 0; i < tokens.length; i++) {
        acc += tokens[i];
        render(acc, {}, true);
        hud(openBlocks(acc));
        var t = tokens[i];
        await sleep(/\n/.test(t) ? 100 : /^\s+$/.test(t) ? 8 : 26 + Math.random() * 40);
      }
      render(acc, {}, false);
    }

    async function loop() {
      for (;;) {
        script.innerHTML = "";
        codeEl.innerHTML = "";
        setVerdict("writing", "Writing");
        hud([]);
        await modelSays("Here’s a function that labels exam scores.");
        await sleep(300);
        await stream(BUGGY);
        hud([]);
        await sleep(450);

        setVerdict("writing", "Testing");
        var pre = genoSays("Every block is closed. Running the examples.", "$ geno test -v score.geno");
        await sleep(900);
        setOut(pre, FAIL_OUT);
        render(BUGGY, { 4: { cls: "is-fail" }, 9: { cls: "is-fail", sel: "75" } });
        setVerdict("failing", "1 failing");
        await sleep(2600);

        await modelSays("The examples say 70 passes. The bar is 60, not 75.");
        render(BUGGY, { 9: { cls: "is-fix", sel: "75" } });
        await sleep(900);
        render(BUGGY.replace(">= 75", ">= 6"), { 9: { cls: "is-fix" } });
        await sleep(150);
        var fixed = BUGGY.replace(">= 75", ">= 60");
        render(fixed, { 9: { cls: "is-fix", sel: "60" } });
        await sleep(900);

        pre = genoSays(null, "$ geno test -v score.geno");
        await sleep(700);
        setOut(pre, PASS_OUT);
        render(fixed, { 3: { cls: "is-pass" }, 4: { cls: "is-pass" }, 5: { cls: "is-pass" } });
        setVerdict("proven", "Proven");
        await sleep(2000);

        await modelSays("Then ship it.");
        render(fixed, {});
        pre = genoSays(null, "$ geno compile score.geno -o score.py");
        await sleep(600);
        setOut(pre, "$ geno compile score.geno -o score.py\nCompiled to score.py\n$ geno compile score.geno --target js -o score.js");
        await sleep(500);
        setOut(pre, "$ geno compile score.geno -o score.py\nCompiled to score.py\n$ geno compile score.geno --target js -o score.js\nCompiled to score.js");
        await sleep(6500);
      }
    }
    // The page opens on the finished scene; the replay starts once the
    // opening animation has settled.
    setTimeout(loop, 700);
  }

  /* act V: animate the fan while visible ---------------------------------- */
  var fan = $("[data-fan]");
  if (fan && "IntersectionObserver" in window)
    new IntersectionObserver(function (es) {
      fan.classList.toggle("is-live", es[0].isIntersecting);
    }).observe(fan);

  /* reel: buttons + drag ---------------------------------------------------- */
  var track = $("[data-reel]");
  if (track) {
    var step = function () {
      var f = $(".frame", track);
      return f ? f.getBoundingClientRect().width + 18 : 400;
    };
    var prev = $("[data-reel-prev]");
    var next = $("[data-reel-next]");
    if (prev)
      prev.addEventListener("click", function () {
        track.scrollBy({ left: -step(), behavior: reduce ? "auto" : "smooth" });
      });
    if (next)
      next.addEventListener("click", function () {
        track.scrollBy({ left: step(), behavior: reduce ? "auto" : "smooth" });
      });
    var down = false;
    var sx = 0;
    var sl = 0;
    var moved = false;
    track.addEventListener("pointerdown", function (e) {
      if (e.pointerType !== "mouse") return;
      down = true;
      moved = false;
      sx = e.clientX;
      sl = track.scrollLeft;
    });
    window.addEventListener("pointermove", function (e) {
      if (!down) return;
      var dx = e.clientX - sx;
      if (Math.abs(dx) > 4) {
        moved = true;
        track.classList.add("is-dragging");
      }
      track.scrollLeft = sl - dx;
    });
    window.addEventListener("pointerup", function () {
      if (!down) return;
      down = false;
      track.classList.remove("is-dragging");
    });
    track.addEventListener(
      "click",
      function (e) {
        if (moved) {
          e.preventDefault();
          e.stopPropagation();
        }
      },
      true
    );
  }

  /* posters: generated key art -------------------------------------------- */
  var T = "#2dd4bf";
  var A = "#f2b880";
  var B = "#e8efec";
  var D = "#0a1918";
  function svg(inner, bg) {
    return (
      '<svg viewBox="0 0 300 400" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg">' +
      '<defs><radialGradient id="g" cx="30%" cy="20%" r="90%"><stop offset="0" stop-color="' + (bg || "#12302c") + '"/><stop offset="1" stop-color="' + D + '"/></radialGradient></defs>' +
      '<rect width="300" height="400" fill="url(#g)"/>' + inner + "</svg>"
    );
  }
  var ART = {
    check: function () {
      var s = "";
      for (var r = 0; r < 6; r++)
        for (var c = 0; c < 4; c++) {
          var x = 40 + c * 58;
          var y = 30 + r * 46;
          var bad = r === 3 && c === 2;
          s += '<rect x="' + x + '" y="' + y + '" width="36" height="36" rx="7" fill="none" stroke="' + (bad ? A : T) + '" stroke-opacity="' + (bad ? 1 : 0.55) + '" stroke-width="2"/>';
          s += bad
            ? '<path d="M' + (x + 11) + " " + (y + 11) + "l14 14M" + (x + 25) + " " + (y + 11) + 'l-14 14" stroke="' + A + '" stroke-width="3" stroke-linecap="round"/>'
            : '<path d="M' + (x + 9) + " " + (y + 19) + "l6 6 12-13" + '" fill="none" stroke="' + T + '" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" stroke-opacity=".9"/>';
        }
      return svg(s);
    },
    dash: function () {
      var h = [70, 120, 95, 160, 135, 200, 175, 230];
      var s = "";
      h.forEach(function (v, i) {
        s += '<rect x="' + (28 + i * 32) + '" y="' + (300 - v) + '" width="20" height="' + v + '" rx="3" fill="' + T + '" fill-opacity="' + (0.35 + i * 0.08) + '"/>';
      });
      s += '<path d="M20 150 L60 128 L100 140 L140 96 L180 112 L220 64 L280 44" fill="none" stroke="' + A + '" stroke-width="3" stroke-linejoin="round"/>';
      s += '<circle cx="280" cy="44" r="6" fill="' + A + '"/>';
      return svg(s, "#103230");
    },
    snap: function () {
      return svg(
        '<circle cx="115" cy="150" r="82" fill="none" stroke="' + T + '" stroke-width="2"/>' +
          '<circle cx="190" cy="190" r="82" fill="' + A + '" fill-opacity=".14" stroke="' + A + '" stroke-width="2"/>' +
          '<path d="M70 300h150m-14-10 14 10-14 10" fill="none" stroke="' + B + '" stroke-width="2" stroke-opacity=".7"/>' +
          '<path d="M230 330H80m14-10-14 10 14 10" fill="none" stroke="' + T + '" stroke-width="2"/>',
        "#132a2c"
      );
    },
    log: function () {
      var s = "";
      for (var i = 0; i < 12; i++) {
        var y = 34 + i * 26;
        var w = [180, 120, 210, 90, 160, 200, 140, 100, 190, 130, 170, 110][i];
        s += '<rect x="30" y="' + y + '" width="34" height="8" rx="2" fill="' + B + '" fill-opacity=".25"/>';
        s += '<rect x="74" y="' + y + '" width="' + w + '" height="8" rx="2" fill="' + (i === 5 ? A : T) + '" fill-opacity="' + (i === 5 ? 0.95 : 0.45) + '"/>';
      }
      return svg(s, "#0f2624");
    },
    pipe: function () {
      var s = "";
      [40, 140, 240].forEach(function (y, i) {
        s += '<rect x="70" y="' + y + '" width="160" height="56" rx="12" fill="none" stroke="' + (i === 1 ? A : T) + '" stroke-width="2"/>';
        s += '<rect x="92" y="' + (y + 22) + '" width="' + (60 + i * 30) + '" height="10" rx="3" fill="' + (i === 1 ? A : T) + '" fill-opacity=".5"/>';
      });
      s += '<path d="M150 96v44M150 196v44" stroke="' + B + '" stroke-opacity=".5" stroke-width="2"/>';
      s += '<circle cx="150" cy="118" r="5" fill="' + T + '"/><circle cx="150" cy="218" r="5" fill="' + A + '"/>';
      return svg(s, "#0e2c29");
    },
    form: function () {
      var s = "";
      [50, 120, 190].forEach(function (y, i) {
        s += '<rect x="40" y="' + y + '" width="80" height="8" rx="2" fill="' + B + '" fill-opacity=".35"/>';
        s += '<rect x="40" y="' + (y + 18) + '" width="220" height="34" rx="8" fill="none" stroke="' + (i === 2 ? A : T) + '" stroke-width="2"/>';
      });
      s += '<rect x="160" y="270" width="100" height="40" rx="20" fill="' + T + '"/>';
      s += '<path d="M48 228h70" stroke="' + A + '" stroke-width="3"/>';
      return svg(s, "#11302d");
    },
    mark: function () {
      return svg(
        '<text x="34" y="190" font-family="Archivo, sans-serif" font-weight="800" font-size="200" fill="' + T + '" fill-opacity=".9">#</text>' +
          '<rect x="34" y="230" width="230" height="10" rx="3" fill="' + B + '" fill-opacity=".35"/>' +
          '<rect x="34" y="254" width="190" height="10" rx="3" fill="' + B + '" fill-opacity=".25"/>' +
          '<rect x="34" y="278" width="210" height="10" rx="3" fill="' + B + '" fill-opacity=".25"/>' +
          '<rect x="34" y="310" width="120" height="10" rx="3" fill="' + A + '" fill-opacity=".85"/>',
        "#132d2a"
      );
    },
    tetris: function () {
      var cells = [
        [3, 1, T],
        [4, 1, T],
        [4, 2, T],
        [5, 2, T],
        [1, 8, A],
        [2, 8, A],
        [3, 8, T],
        [4, 8, T],
        [6, 8, T],
        [7, 8, A],
        [7, 7, A],
        [8, 8, T],
        [0, 7, T],
        [0, 8, T],
        [5, 7, B],
        [5, 8, B],
      ];
      var s = "";
      cells.forEach(function (c) {
        s += '<rect x="' + (14 + c[0] * 30) + '" y="' + (60 + c[1] * 30) + '" width="27" height="27" rx="4" fill="' + c[2] + '" fill-opacity="' + (c[2] === B ? 0.35 : 0.85) + '"/>';
      });
      return svg(s, "#122c2a");
    },
  };
  $$("[data-poster]").forEach(function (p) {
    var art = $(".poster-art", p);
    var fn = ART[p.dataset.poster];
    if (art && fn) art.innerHTML = fn();
  });

  /* shared demos + playground --------------------------------------------- */
  if (window.GenoDemos) window.GenoDemos.init(document);
  if (window.GenoPlayground) window.GenoPlayground.init();
})();
