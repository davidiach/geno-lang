/* Client for the Geno playground worker.
 *
 *   GenoRuntime.boot()                         -> Promise<version>
 *   GenoRuntime.cli(args, files, opts)         -> Promise<{code, output, ms, file}>
 *   GenoRuntime.unclosed(prefix)               -> Promise<string[] | null>
 *   GenoRuntime.on(event, fn)                  events: progress, ready, error, state
 *
 * The worker and the Geno bundle are resolved relative to this script, so a
 * page only needs <script src=".../shared/js/runtime.js">.
 */
(function (global) {
  "use strict";

  var here = (document.currentScript && document.currentScript.src) || location.href;
  var sharedBase = new URL("../", here).href; // .../shared/
  function metaContent(name) {
    var m = document.querySelector('meta[name="' + name + '"]');
    return m ? m.content : null;
  }
  // Optional overrides for self-hosting: <meta name="geno-pyodide-base">,
  // <meta name="geno-bundle-url"> and <meta name="geno-stdlib-url">.
  var config = {
    pyodideBase: metaContent("geno-pyodide-base") || global.GENO_PYODIDE_BASE || "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/",
    bundleUrl: metaContent("geno-bundle-url") || new URL("playground/geno.zip", sharedBase).href,
    stdlibUrl: metaContent("geno-stdlib-url"),
    workerUrl: new URL("js/runtime-worker.js", sharedBase).href,
  };
  // Pyodide's own loader needs absolute URLs.
  config.pyodideBase = new URL(config.pyodideBase, location.href).href;
  config.bundleUrl = new URL(config.bundleUrl, location.href).href;
  if (config.stdlibUrl) config.stdlibUrl = new URL(config.stdlibUrl, location.href).href;

  var worker = null;
  var bootPromise = null;
  var pending = new Map();
  var seq = 0;
  var listeners = {};
  var state = "idle";
  var version = null;

  function emit(name, data) {
    (listeners[name] || []).forEach(function (fn) {
      try {
        fn(data);
      } catch (e) {
        /* listener errors stay local */
      }
    });
  }
  function setState(s, data) {
    state = s;
    emit("state", { state: s, data: data });
  }

  function spawn() {
    worker = new Worker(config.workerUrl);
    worker.onmessage = function (ev) {
      var msg = ev.data;
      if (msg.type === "progress") emit("progress", msg);
      else if (msg.type === "ready") {
        version = msg.version;
        setState("ready", msg);
        emit("ready", msg);
        if (bootPromise && bootPromise.resolve) bootPromise.resolve(msg.version);
      } else if (msg.type === "result" || msg.type === "error") {
        var p = pending.get(msg.id);
        if (msg.type === "error" && msg.id == null && bootPromise && bootPromise.reject) {
          setState("error", msg);
          emit("error", msg);
          failBoot(new Error(msg.message));
          return;
        }
        if (!p) return;
        pending.delete(msg.id);
        clearTimeout(p.timer);
        if (msg.type === "error") p.reject(new Error(msg.message));
        else p.resolve(msg);
      }
    };
    worker.onerror = function (e) {
      var message = (e && e.message) || "The playground worker failed to start.";
      setState("error", { message: message });
      emit("error", { message: message });
      failBoot(new Error(message));
    };
  }

  // A failed load is not cached: the next request starts a fresh worker.
  function failBoot(err) {
    var b = bootPromise;
    bootPromise = null;
    if (worker) worker.terminate();
    worker = null;
    if (b && b.reject) b.reject(err);
  }

  function boot() {
    if (bootPromise) return bootPromise.promise;
    var resolve, reject;
    var promise = new Promise(function (res, rej) {
      resolve = res;
      reject = rej;
    });
    bootPromise = { promise: promise, resolve: resolve, reject: reject };
    setState("loading");
    try {
      spawn();
      worker.postMessage({ type: "boot", config: { pyodideBase: config.pyodideBase, bundleUrl: config.bundleUrl, stdlibUrl: config.stdlibUrl } });
    } catch (e) {
      setState("error", { message: String(e.message || e) });
      reject(e);
    }
    promise.catch(function () {
      /* surfaced through state */
    });
    return promise;
  }

  // Restart the worker. Every request still waiting is settled, so no
  // caller is left holding a promise that never resolves.
  function reset(reason) {
    if (worker) worker.terminate();
    worker = null;
    bootPromise = null;
    var waiting = Array.from(pending.values());
    pending.clear();
    waiting.forEach(function (p) {
      clearTimeout(p.timer);
      p.resolve({ code: 124, output: reason || "Stopped: the runtime restarted.", ms: 0, timedOut: true });
    });
    setState("idle");
  }

  function request(payload, timeoutMs, soft) {
    return boot().then(function () {
      return new Promise(function (resolve, reject) {
        var id = ++seq;
        payload.id = id;
        var timer = setTimeout(function () {
          var p = pending.get(id);
          pending.delete(id);
          if (soft) {
            // Advisory requests (the open-blocks hint) just give up.
            resolve({ timedOut: true });
            return;
          }
          // A runaway program: restart the interpreter so the next run is clean.
          resolve({ code: 124, output: "Stopped: the program ran longer than " + Math.round(timeoutMs / 1000) + " s in the browser.", ms: timeoutMs, timedOut: true });
          if (p) {
            reset();
            boot();
          }
        }, timeoutMs || 20000);
        pending.set(id, { resolve: resolve, reject: reject, timer: timer, kind: payload.type });
        worker.postMessage(payload);
      });
    });
  }
  function cliPending() {
    var busy = false;
    pending.forEach(function (p) {
      if (p.kind === "cli") busy = true;
    });
    return busy;
  }

  global.GenoRuntime = {
    config: config,
    get state() {
      return state;
    },
    get version() {
      return version;
    },
    on: function (name, fn) {
      (listeners[name] = listeners[name] || []).push(fn);
      return this;
    },
    boot: boot,
    cli: function (args, files, opts) {
      opts = opts || {};
      return request({ type: "cli", args: args, files: files, readBack: opts.readBack }, opts.timeoutMs || 20000);
    },
    unclosed: function (prefix) {
      // The worker runs one thing at a time; never queue behind a program.
      if (state !== "ready" || cliPending()) return Promise.resolve(null);
      return request({ type: "unclosed", prefix: prefix }, 4000, true).then(function (r) {
        return (r && r.blocks) || null;
      });
    },
  };
})(window);
