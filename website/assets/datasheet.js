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

  /* table of contents scrollspy: the rail on desktop, the sticky
     contents control on tablets and phones ------------------------------- */
  var tocs = document.querySelectorAll("[data-toc], [data-toc-m]");
  var tocM = document.querySelector("[data-toc-m]");
  var tocNow = document.querySelector("[data-toc-now]");
  if (tocs.length) {
    var links = {};
    tocs.forEach(function (t) {
      t.querySelectorAll("a[href^='#']").forEach(function (a) {
        var id = a.getAttribute("href").slice(1);
        (links[id] = links[id] || []).push(a);
      });
    });
    var sections = Object.keys(links)
      .map(function (id) {
        return document.getElementById(id);
      })
      .filter(Boolean);
    var current = null;
    // The current section is the last one whose top has passed a line
    // a third of the way down the viewport.
    var spy = function () {
      var line = window.innerHeight * 0.33;
      var best = sections[0].id;
      sections.forEach(function (el) {
        if (el.getBoundingClientRect().top <= line) best = el.id;
      });
      if (best === current) return;
      current = best;
      Object.keys(links).forEach(function (id) {
        links[id].forEach(function (a) {
          a.setAttribute("aria-current", id === best ? "true" : "false");
        });
      });
      if (tocNow) {
        var a = links[best][0];
        tocNow.textContent = a.querySelector("span").textContent + " " + a.lastChild.textContent;
      }
    };
    var queued = false;
    window.addEventListener(
      "scroll",
      function () {
        if (queued) return;
        queued = true;
        window.requestAnimationFrame(function () {
          queued = false;
          spy();
        });
      },
      { passive: true }
    );
    window.addEventListener("resize", spy);
    spy();
  }
  if (tocM) {
    tocM.addEventListener("click", function (e) {
      if (e.target.closest("a")) tocM.open = false;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && tocM.open) {
        tocM.open = false;
        tocM.querySelector("summary").focus();
      }
    });
  }

  /* pinout: table rows <-> code lines <-> chip pins ----------------------- */
  var chipPin = function () {};
  var pinLight = function () {};
  var pinout = document.querySelector("[data-pinout]");
  if (pinout) {
    var lines = pinout.querySelectorAll("[data-pinline]");
    var rows = document.querySelectorAll("tr[data-pin]");
    var light = function (pin) {
      lines.forEach(function (ln) {
        ln.classList.toggle("is-lit", pin !== null && ln.dataset.pinline.split(" ").indexOf(pin) !== -1);
      });
      rows.forEach(function (r) {
        r.classList.toggle("is-lit", r.dataset.pin === pin);
      });
      chipPin(pin === null ? 0 : +pin);
    };
    pinLight = light;
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

  /* Figure 4-1 chip: run the four test vectors --------------------------- */
  var chip = document.querySelector("[data-chip]");
  var vectors = document.querySelector("[data-vectors]");
  if (chip && vectors && !reduce && chip.animate) {
    var VEC = [
      ["90", '"excellent"'],
      ["89", '"passing"'],
      ["60", '"passing"'],
      ["59", '"needs work"'],
    ];
    var inText = chip.querySelector("[data-chip-in]");
    var outText = chip.querySelector("[data-chip-out]");
    var count = chip.querySelector("[data-chip-count]");
    var pIn = chip.querySelector("[data-pulse-in]");
    var pOut = chip.querySelector("[data-pulse-out]");
    var vrows = vectors.querySelectorAll("tbody tr");
    var running = false;
    var inView = !("IntersectionObserver" in window);

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
      count.textContent = "0/" + VEC.length + " pass";
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
        count.textContent = i + 1 + "/" + VEC.length + " pass";
        await wait(380);
      }
      await wait(900);
      vrows.forEach(function (r) {
        r.classList.remove("is-hit");
      });
      inText.textContent = VEC[0][0];
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

  /* Figure 4-1 chip: hover a pin to read its function (from Table 4-1) --- */
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
    var mark = function (n) {
      clear();
      if (!n) return;
      chipEl.classList.add("has-pin");
      groups.forEach(function (g) {
        var el = g[n - 1];
        if (el) {
          el.classList.add("is-on");
          el.setAttribute("data-pinset", "");
        }
      });
    };
    chipPin = mark;
    chipEl.querySelectorAll("[data-chip-pin]").forEach(function (hit) {
      var n = +hit.dataset.chipPin;
      var row = document.querySelector('tr[data-pin="' + n + '"]');
      hit.addEventListener("pointerenter", function () {
        pinLight(String(n));
        mark(n);
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
      hit.addEventListener("pointerleave", function () {
        pinLight(null);
        clear();
      });
      hit.addEventListener("click", function () {
        if (!row) return;
        row.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "nearest" });
        row.focus({ preventScroll: true });
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
