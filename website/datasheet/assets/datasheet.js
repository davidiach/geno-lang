/* Datasheet edition: page behaviour. Shared demos live in ../shared/js. */
(function () {
  "use strict";

  var root = document.documentElement;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* theme: paper / night ------------------------------------------------ */
  function currentTheme() {
    if (root.dataset.theme) return root.dataset.theme;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try {
        localStorage.setItem("geno-theme", next);
      } catch (e) {
        /* private mode */
      }
      window.dispatchEvent(new Event("themechange"));
    });
  });

  /* bar shadow + mobile drawer -------------------------------------------- */
  var bar = document.querySelector("[data-bar]");
  function onScroll() {
    if (bar) bar.classList.toggle("is-scrolled", window.scrollY > 8);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  var menu = document.querySelector("[data-menu]");
  var drawer = document.getElementById("drawer");
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

  /* table of contents scrollspy ------------------------------------------ */
  var toc = document.querySelector("[data-toc]");
  if (toc && "IntersectionObserver" in window) {
    var links = {};
    toc.querySelectorAll("a[href^='#']").forEach(function (a) {
      links[a.getAttribute("href").slice(1)] = a;
    });
    var visible = new Map();
    var spy = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          visible.set(e.target.id, e.isIntersecting ? e.boundingClientRect.top : null);
        });
        var best = null;
        var bestTop = Infinity;
        visible.forEach(function (top, id) {
          if (top !== null && Math.abs(top) < bestTop) {
            bestTop = Math.abs(top);
            best = id;
          }
        });
        if (!best) return;
        Object.keys(links).forEach(function (id) {
          links[id].setAttribute("aria-current", id === best ? "true" : "false");
        });
      },
      { rootMargin: "-20% 0px -60% 0px" }
    );
    Object.keys(links).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) spy.observe(el);
    });
  }

  /* pinout: table rows <-> code lines ------------------------------------ */
  var pinout = document.querySelector("[data-pinout]");
  if (pinout) {
    var lines = pinout.querySelectorAll("[data-pinline]");
    var rows = pinout.querySelectorAll("tr[data-pin]");
    var light = function (pin) {
      lines.forEach(function (ln) {
        ln.classList.toggle("is-lit", pin !== null && ln.dataset.pinline.split(" ").indexOf(pin) !== -1);
      });
      rows.forEach(function (r) {
        r.classList.toggle("is-lit", r.dataset.pin === pin);
      });
    };
    rows.forEach(function (r) {
      r.addEventListener("pointerenter", function () {
        light(r.dataset.pin);
      });
      r.addEventListener("focus", function () {
        light(r.dataset.pin);
      });
      r.addEventListener("pointerleave", function () {
        light(null);
      });
      r.addEventListener("blur", function () {
        light(null);
      });
    });
    lines.forEach(function (ln) {
      ln.addEventListener("pointerenter", function () {
        light(ln.dataset.pinline.split(" ")[0]);
      });
      ln.addEventListener("pointerleave", function () {
        light(null);
      });
    });
  }

  /* hero chip: run the three test vectors -------------------------------- */
  var chip = document.querySelector("[data-chip]");
  var vectors = document.querySelector("[data-vectors]");
  if (chip && vectors && !reduce && chip.animate) {
    var VEC = [
      ["95", '"excellent"'],
      ["70", '"passing"'],
      ["40", '"needs work"'],
    ];
    var inText = chip.querySelector("[data-chip-in]");
    var outText = chip.querySelector("[data-chip-out]");
    var count = chip.querySelector("[data-chip-count]");
    var pIn = chip.querySelector("[data-pulse-in]");
    var pOut = chip.querySelector("[data-pulse-out]");
    var vrows = vectors.querySelectorAll("tbody tr");
    var running = false;
    var inView = true;

    function wait(ms) {
      return new Promise(function (r) {
        setTimeout(r, ms);
      });
    }
    function travel(el, dx) {
      return el.animate(
        [
          { transform: "translateX(0)", opacity: 0 },
          { opacity: 1, offset: 0.15 },
          { opacity: 1, offset: 0.85 },
          { transform: "translateX(" + dx + "px)", opacity: 0 },
        ],
        { duration: 520, easing: "cubic-bezier(.4,0,.2,1)" }
      ).finished;
    }
    async function cycle() {
      if (running) return;
      running = true;
      vrows.forEach(function (r) {
        r.classList.add("is-pending");
        r.querySelector(".res").textContent = "····";
      });
      count.textContent = "0/3 pass";
      for (var i = 0; i < VEC.length; i++) {
        inText.textContent = VEC[i][0];
        outText.textContent = "";
        vrows.forEach(function (r) {
          r.classList.remove("is-hit");
        });
        await travel(pIn, 101);
        chip.classList.add("is-busy");
        await wait(240);
        chip.classList.remove("is-busy");
        outText.textContent = VEC[i][1];
        await travel(pOut, 101);
        vrows[i].classList.remove("is-pending");
        vrows[i].classList.add("is-hit");
        vrows[i].querySelector(".res").textContent = "PASS";
        count.textContent = i + 1 + "/3 pass";
        await wait(380);
      }
      await wait(900);
      vrows.forEach(function (r) {
        r.classList.remove("is-hit");
      });
      inText.textContent = "95";
      outText.textContent = '"excellent"';
      running = false;
    }
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (es) {
        inView = es[0].isIntersecting;
      }).observe(chip);
    }
    setTimeout(function loop() {
      if (inView && !document.hidden) cycle();
      setTimeout(loop, 9000);
    }, 1900);
  }

  /* hero chip: hover a pin to read its function (from Table 4-1) --------- */
  var chipEl = document.querySelector("[data-chip]");
  var tip = document.querySelector("[data-chip-tip]");
  if (chipEl && tip) {
    var fig = chipEl.closest("figure");
    var groups = ["wires line", "legs rect", "lbl text"].map(function (sel) {
      return chipEl.querySelectorAll("." + sel.split(" ")[0] + " " + sel.split(" ")[1]);
    });
    var clear = function () {
      chipEl.classList.remove("has-pin");
      groups.forEach(function (g) {
        g.forEach(function (el) {
          el.classList.remove("is-on");
          el.removeAttribute("data-pinset");
        });
      });
      tip.hidden = true;
    };
    chipEl.querySelectorAll("[data-chip-pin]").forEach(function (hit) {
      var n = +hit.dataset.chipPin;
      var row = document.querySelector('tr[data-pin="' + n + '"]');
      hit.addEventListener("pointerenter", function () {
        clear();
        chipEl.classList.add("has-pin");
        groups.forEach(function (g) {
          var el = g[n - 1];
          if (el) {
            el.classList.add("is-on");
            el.setAttribute("data-pinset", "");
          }
        });
        if (row) {
          var cells = row.querySelectorAll("td");
          tip.innerHTML = "<b>PIN " + n + " · " + cells[1].textContent + "</b>" + cells[3].innerHTML;
        }
        tip.hidden = false;
        var hb = hit.getBoundingClientRect();
        var fb = fig.getBoundingClientRect();
        var left = Math.min(fb.width - tip.offsetWidth - 8, Math.max(8, hb.left - fb.left + hb.width / 2 - tip.offsetWidth / 2));
        var top = hb.top - fb.top - tip.offsetHeight - 6;
        if (top < 4) top = hb.bottom - fb.top + 6;
        tip.style.left = left + "px";
        tip.style.top = top + "px";
      });
      hit.addEventListener("pointerleave", clear);
      hit.addEventListener("click", function () {
        var target = document.getElementById("pinout");
        if (target) target.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
        if (row) {
          setTimeout(function () {
            row.focus({ preventScroll: true });
          }, reduce ? 0 : 700);
        }
      });
    });
  }

  /* block diagram pulses only while visible -------------------------------- */
  var bd = document.querySelector(".fig-blocks");
  if (bd && !reduce && "IntersectionObserver" in window) {
    new IntersectionObserver(function (es) {
      bd.classList.toggle("is-live", es[0].isIntersecting);
    }).observe(bd);
  }

  /* shared demos + playground -------------------------------------------- */
  if (window.GenoDemos) {
    window.GenoDemos.init(document);
    window.GenoDemos.codeCopy(".prose pre");
  }
  if (window.GenoPlayground) window.GenoPlayground.init();
})();
