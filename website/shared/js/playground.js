/* Playground UI shared by both site versions.
 *
 * Markup contract (the page supplies the frame and styles every class):
 *
 *   <div data-playground [data-autoboot]>
 *     <select data-pg="examples"></select>
 *     <button data-pg="run">  <button data-pg="test">  <button data-pg="check">
 *     <button data-pg="compile" data-target="py|js">  <button data-pg="share">
 *     <div data-pg="editor"></div>      editor is built here
 *     <div data-pg="caps"></div>        capability checkboxes are built here
 *     <span data-pg="status"></span>    runtime state
 *     <span data-pg="blocks"></span>    open blocks at the cursor
 *     <span data-pg="meta"></span>      last command: exit code and time
 *     <pre data-pg="out"></pre>         CLI output
 *   </div>
 *
 * Requires highlight.js, runtime.js and examples.js.
 */
(function (global) {
  "use strict";

  var H = global.GenoHighlight;
  var R = global.GenoRuntime;
  var EXAMPLES = global.GenoExamples || [];
  var CAPS = [
    { id: "print", on: true, hint: "print" },
    { id: "clock", on: true, hint: "clock_now, sleep_ms" },
    { id: "random", on: false, hint: "random_int" },
    { id: "fs", on: false, hint: "fs_read_text (in-browser files)" },
    { id: "env", on: false, hint: "env_get" },
  ];
  var FILE = "Main.geno";

  function q(root, name) {
    return root.querySelector('[data-pg="' + name + '"]');
  }
  function qa(root, name) {
    return root.querySelectorAll('[data-pg="' + name + '"]');
  }
  function store(key, val) {
    try {
      if (val === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, val);
    } catch (e) {
      return null;
    }
  }

  function b64encode(s) {
    var bytes = new TextEncoder().encode(s);
    var bin = "";
    bytes.forEach(function (b) {
      bin += String.fromCharCode(b);
    });
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function b64decode(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/");
    var bin = atob(s);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }

  // Quick local estimate of open blocks, used until the real engine is ready.
  function estimateBlocks(prefix) {
    var stack = [];
    prefix.split("\n").forEach(function (raw) {
      var line = raw.replace(/"(?:[^"\\]|\\.)*"/g, '""').replace(/\/\/.*$/, "");
      var m;
      if ((m = line.match(/^\s*end\s+(\w+)/))) {
        if (stack.length) stack.pop();
        return;
      }
      if (/^\s*else\b/.test(line)) return;
      m = line.match(/^\s*(?:export\s+)?(?:async\s+)?(func|if|for|while|match|try|trait|impl)\b/);
      if (m) {
        // Abstract trait methods have no body.
        if (m[1] === "func" && stack[stack.length - 1] === "trait") return;
        stack.push(m[1]);
        var closes = line.match(new RegExp("\\bend\\s+" + m[1] + "\\b"));
        if (closes) stack.pop();
      }
    });
    return stack.reverse();
  }

  function userCode(full, target, src) {
    var lines = full.split("\n");
    var start = -1;
    if (target === "py") {
      start = lines.findIndex(function (l) {
        return /Generated Code Follows/.test(l);
      });
      if (start >= 0) start += 2;
    } else {
      var names = [];
      src.replace(/^\s*(?:export\s+)?(?:async\s+)?func\s+(\w+)/gm, function (_, n) {
        names.push(n);
      });
      src.replace(/^\s*type\s+(\w+)/gm, function (_, n) {
        names.push(n);
      });
      names.forEach(function (n) {
        var re = new RegExp("^(?:async\\s+)?(?:function\\*?|class|const|let)\\s+" + n + "\\b");
        var idx = lines.findIndex(function (l) {
          return re.test(l);
        });
        if (idx >= 0 && (start < 0 || idx < start)) start = idx;
      });
    }
    if (start < 0) start = Math.max(0, lines.length - 120);
    var body = lines
      .slice(start)
      .join("\n")
      .replace(/^\s*\n/, "")
      .replace(/\/work\//g, "");
    return { body: body, hidden: start, total: lines.length };
  }

  function mount(root) {
    var editorHost = q(root, "editor");
    var out = q(root, "out");
    var statusEl = q(root, "status");
    var blocksEl = q(root, "blocks");
    var metaEl = q(root, "meta");
    var select = q(root, "examples");
    var capsHost = q(root, "caps");
    var autoboot = root.hasAttribute("data-autoboot");
    var busy = false;

    // ---- editor -------------------------------------------------------
    editorHost.classList.add("pg-editor");
    editorHost.innerHTML =
      '<pre class="pg-gutter" aria-hidden="true"></pre>' +
      '<div class="pg-code"><pre class="pg-hl" aria-hidden="true"><code></code></pre>' +
      '<textarea class="pg-input" id="' + (root.id || "pg") + '-src" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off" wrap="off" aria-label="Geno source code, ' + FILE + '"></textarea></div>';
    var ta = editorHost.querySelector("textarea");
    var hl = editorHost.querySelector(".pg-hl code");
    var hlPre = editorHost.querySelector(".pg-hl");
    var gutter = editorHost.querySelector(".pg-gutter");
    var errLines = new Set();

    function paint() {
      var src = ta.value;
      var lines = src.split("\n");
      hl.innerHTML =
        lines
          .map(function (l, i) {
            return '<span class="pg-line' + (errLines.has(i + 1) ? " is-err" : "") + '">' + (H.highlight(l, "geno") || " ") + "</span>";
          })
          .join("\n") + "\n";
      gutter.innerHTML = lines
        .map(function (_, i) {
          return '<span class="' + (errLines.has(i + 1) ? "is-err" : "") + '">' + (i + 1) + "</span>";
        })
        .join("\n");
    }
    function syncScroll() {
      hlPre.scrollTop = ta.scrollTop;
      hlPre.scrollLeft = ta.scrollLeft;
      gutter.scrollTop = ta.scrollTop;
    }
    ta.addEventListener("scroll", syncScroll);

    var blocksTimer = null;
    function updateBlocks() {
      if (!blocksEl) return;
      clearTimeout(blocksTimer);
      blocksTimer = setTimeout(function () {
        var prefix = ta.value.slice(0, ta.selectionStart);
        var show = function (list, real) {
          blocksEl.innerHTML = list && list.length
            ? '<span class="pg-blocks-label">open</span> ' +
              list
                .map(function (b) {
                  return '<span class="pg-blk">' + b + "</span>";
                })
                .join('<span class="pg-blk-sep">›</span>')
            : '<span class="pg-blocks-label">open</span> <span class="pg-blk is-none">none</span>';
          blocksEl.title = real ? "From geno.constraints, the engine behind geno constrain" : "Local estimate; the real engine takes over once Geno has loaded";
        };
        show(estimateBlocks(prefix), false);
        if (R.state === "ready")
          R.unclosed(prefix).then(function (list) {
            if (list) show(list, true);
          });
      }, 120);
    }

    var saveTimer = null;
    ta.addEventListener("input", function () {
      errLines.clear();
      paint();
      updateBlocks();
      clearTimeout(saveTimer);
      saveTimer = setTimeout(function () {
        store("geno-playground-draft", ta.value);
      }, 400);
    });
    ["click", "keyup"].forEach(function (ev) {
      ta.addEventListener(ev, updateBlocks);
    });

    var tabEscape = false;
    ta.addEventListener("keydown", function (e) {
      var mod = e.metaKey || e.ctrlKey;
      if (mod && e.key === "Enter") {
        e.preventDefault();
        command(e.shiftKey ? "test" : "run");
        return;
      }
      if (e.key === "Escape") {
        tabEscape = true;
        return;
      }
      if (e.key === "Tab" && !tabEscape && !mod) {
        e.preventDefault();
        insert(e.shiftKey ? null : "    ");
        return;
      }
      tabEscape = false;
      if (e.key === "Enter" && !mod) {
        e.preventDefault();
        var before = ta.value.slice(0, ta.selectionStart);
        var cur = before.slice(before.lastIndexOf("\n") + 1);
        var indent = (cur.match(/^\s*/) || [""])[0];
        var bare = cur.replace(/\/\/.*$/, "").trimEnd();
        if (/\b(then|do|with|else|try)$/.test(bare) || /^\s*(?:export\s+)?(?:async\s+)?func\b.*\)\s*(->.*)?$/.test(bare) || /^\s*(trait|impl)\b/.test(bare) || /->$/.test(bare)) indent += "    ";
        insert("\n" + indent);
      }
    });
    function insert(text) {
      var s = ta.selectionStart;
      var e = ta.selectionEnd;
      if (text === null) {
        // Shift+Tab: outdent the current line.
        var ls = ta.value.lastIndexOf("\n", s - 1) + 1;
        if (ta.value.slice(ls, ls + 4) === "    ") {
          ta.setRangeText("", ls, ls + 4, "end");
          ta.selectionStart = ta.selectionEnd = Math.max(ls, s - 4);
        }
      } else {
        ta.setRangeText(text, s, e, "end");
      }
      ta.dispatchEvent(new Event("input"));
    }

    function setSource(src) {
      ta.value = src;
      errLines.clear();
      paint();
      ta.scrollTop = 0;
      syncScroll();
      updateBlocks();
    }

    // ---- examples ------------------------------------------------------
    if (select) {
      select.innerHTML = EXAMPLES.map(function (ex, i) {
        return '<option value="' + i + '">' + ex.title + "</option>";
      }).join("");
      select.addEventListener("change", function () {
        var ex = EXAMPLES[+select.value];
        if (!ex) return;
        setSource(ex.source);
        out.innerHTML = '<span class="pg-hint">Loaded “' + ex.title + "”. " + ex.blurb + "</span>";
        if (metaEl) metaEl.textContent = "";
      });
    }

    // ---- capabilities --------------------------------------------------
    if (capsHost) {
      capsHost.innerHTML =
        '<span class="pg-caps-label" id="' + (root.id || "pg") + '-caps">--cap</span>' +
        CAPS.map(function (c) {
          var id = (root.id || "pg") + "-cap-" + c.id;
          return '<label class="pg-cap" title="' + c.hint + '"><input type="checkbox" id="' + id + '" value="' + c.id + '"' + (c.on ? " checked" : "") + "><span>" + c.id + "</span></label>";
        }).join("");
    }
    function caps() {
      if (!capsHost) return ["print"];
      return Array.prototype.map.call(capsHost.querySelectorAll("input:checked"), function (i) {
        return i.value;
      });
    }

    // ---- runtime state -------------------------------------------------
    function setStatus(html, kind) {
      if (!statusEl) return;
      statusEl.innerHTML = html;
      statusEl.dataset.state = kind || "";
    }
    R.on("progress", function (p) {
      setStatus('<span class="pg-spin" aria-hidden="true"></span>' + p.label + "…", "loading");
    });
    R.on("ready", function (m) {
      setStatus("Geno " + m.version + " · running in this tab", "ready");
      updateBlocks();
    });
    R.on("error", function (m) {
      setStatus("Runtime unavailable", "error");
      out.innerHTML =
        '<span class="tok-fail">The in-browser runtime could not load.</span>\n<span class="pg-hint">' +
        H.escape(m.message || "") +
        "\n\nGeno runs anywhere Python 3.10 to 3.13 does:\n  pip install geno-lang\n  geno test Main.geno</span>";
    });
    if (R.state === "ready") setStatus("Geno " + R.version + " · running in this tab", "ready");
    else setStatus("Real compiler · loads on first run (about 13 MB)", "idle");

    // ---- commands ------------------------------------------------------
    var LABEL = { run: "Run", test: "Test", check: "Check", compile: "Compile" };
    function argsFor(kind, target) {
      if (kind === "run") {
        var c = caps();
        // With nothing granted, use the fail-closed JSON lane, which grants nothing.
        if (!c.length) return ["run", "--json", FILE];
        return ["run", "--unsafe", "--timeout", "8", "--cap", c.join(","), FILE];
      }
      if (kind === "test") return ["test", "-v", FILE];
      if (kind === "check") return ["check", FILE];
      return ["compile", FILE, "--target", target === "py" ? "python" : "js", "-o", target === "py" ? "out.py" : "out.js"];
    }

    function markErrors(text) {
      errLines.clear();
      var re = /Main\.geno:(\d+):\d+/g;
      var m;
      while ((m = re.exec(text))) errLines.add(+m[1]);
      paint();
    }

    function command(kind, target) {
      if (busy) return;
      busy = true;
      root.classList.add("is-busy");
      var args = argsFor(kind, target);
      var shown = "$ geno " + args.join(" ");
      out.innerHTML = H.highlight(shown, "sh") + "\n" + (R.state === "ready" ? "" : '<span class="pg-hint">Starting Python and Geno in your browser…</span>');
      if (metaEl) metaEl.textContent = "";
      var files = {};
      files[FILE] = ta.value;
      var opts = kind === "compile" ? { readBack: target === "py" ? "out.py" : "out.js" } : {};
      R.cli(args, files, opts)
        .then(function (res) {
          var text = (res.output || "").replace(/\/work\//g, "").replace(/\s+$/, "");
          var body;
          if (kind === "compile" && res.code === 0 && res.file) {
            var uc = userCode(res.file, target, ta.value);
            body =
              '<span class="pg-hint">// ' + (target === "py" ? "out.py" : "out.js") + ": " + uc.total.toLocaleString() + " lines. Showing the code generated for " + FILE + "; the " + uc.hidden.toLocaleString() + "-line runtime prelude above it holds the safety checks and builtins.</span>\n\n" +
              H.highlight(uc.body, target === "py" ? "py" : "js");
          } else {
            body = H.highlight(text || (res.code === 0 ? "(no output)" : ""), "sh");
          }
          out.innerHTML = H.highlight(shown, "sh") + "\n" + body;
          markErrors(text);
          if (metaEl) {
            metaEl.innerHTML =
              '<span class="' + (res.code === 0 ? "tok-pass" : "tok-fail") + '">exit ' + res.code + "</span> · " + Math.max(1, Math.round(res.ms)) + " ms";
          }
          root.dispatchEvent(new CustomEvent("pg:result", { detail: { kind: kind, code: res.code } }));
        })
        .catch(function (err) {
          out.innerHTML = H.highlight(shown, "sh") + '\n<span class="tok-fail">' + H.escape(String(err.message || err)) + "</span>";
        })
        .then(function () {
          busy = false;
          root.classList.remove("is-busy");
        });
    }

    qa(root, "run").forEach(function (b) {
      b.addEventListener("click", function () {
        command("run");
      });
    });
    qa(root, "test").forEach(function (b) {
      b.addEventListener("click", function () {
        command("test");
      });
    });
    qa(root, "check").forEach(function (b) {
      b.addEventListener("click", function () {
        command("check");
      });
    });
    qa(root, "compile").forEach(function (b) {
      b.addEventListener("click", function () {
        command("compile", b.dataset.target || "js");
      });
    });
    qa(root, "share").forEach(function (b) {
      b.addEventListener("click", function () {
        var url = location.href.split("#")[0] + "#src=" + b64encode(ta.value);
        try {
          history.replaceState(null, "", url);
        } catch (e) {
          /* some hosts forbid it */
        }
        var done = function () {
          b.setAttribute("data-copied", "");
          setTimeout(function () {
            b.removeAttribute("data-copied");
          }, 1600);
        };
        try {
          navigator.clipboard.writeText(url).then(done, done);
        } catch (e) {
          done();
        }
      });
    });
    qa(root, "reset").forEach(function (b) {
      b.addEventListener("click", function () {
        var ex = EXAMPLES[select ? +select.value : 0];
        if (ex) setSource(ex.source);
      });
    });

    // ---- initial source -----------------------------------------------
    var initial = null;
    var hash = location.hash.match(/^#src=([\w-]+)/);
    if (hash) {
      try {
        initial = b64decode(hash[1]);
      } catch (e) {
        initial = null;
      }
    }
    if (!initial && root.hasAttribute("data-restore")) initial = store("geno-playground-draft");
    var start = +(root.dataset.example || 0);
    if (select) select.value = String(start);
    setSource(initial || (EXAMPLES[start] && EXAMPLES[start].source) || "");
    out.innerHTML =
      '<span class="pg-hint">' +
      (root.dataset.intro || "Press Test to run the examples, Run to execute main, or Check to type-check. This is the real Geno CLI, compiled to WebAssembly and running in this tab.") +
      "</span>";

    if (autoboot) R.boot();
    else {
      var warm = function () {
        R.boot();
      };
      ta.addEventListener("focus", warm, { once: true });
      root.addEventListener("pointerenter", function () {
        // Warm up when a pointer user shows intent; phones wait for a tap.
        if (matchMedia("(hover: hover)").matches) R.boot();
      }, { once: true });
    }
    return { setSource: setSource, command: command };
  }

  global.GenoPlayground = {
    mount: mount,
    init: function () {
      document.querySelectorAll("[data-playground]").forEach(function (el) {
        if (!el.dataset.pgBound) {
          el.dataset.pgBound = "1";
          mount(el);
        }
      });
    },
  };
})(window);
