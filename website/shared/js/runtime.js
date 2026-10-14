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
  var meta = document.querySelector('meta[name="geno-pyodide-base"]');
  var config = {
    pyodideBase: (meta && meta.content) || global.GENO_PYODIDE_BASE || "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/",
    bundleUrl: new URL("playground/geno.zip", sharedBase).href,
    workerUrl: new URL("js/runtime-worker.js", sharedBase).href,
  };
  // Pyodide's own loader needs an absolute base.
  config.pyodideBase = new URL(config.pyodideBase, location.href).href;

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
          bootPromise.reject(new Error(msg.message));
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
      if (bootPromise && bootPromise.reject) bootPromise.reject(new Error(message));
    };
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
      worker.postMessage({ type: "boot", config: { pyodideBase: config.pyodideBase, bundleUrl: config.bundleUrl } });
    } catch (e) {
      setState("error", { message: String(e.message || e) });
      reject(e);
    }
    promise.catch(function () {
      /* surfaced through state */
    });
    return promise;
  }

  function reset() {
    if (worker) worker.terminate();
    worker = null;
    bootPromise = null;
    pending.forEach(function (p) {
      clearTimeout(p.timer);
    });
    pending.clear();
    setState("idle");
  }

  function request(payload, timeoutMs) {
    return boot().then(function () {
      return new Promise(function (resolve, reject) {
        var id = ++seq;
        payload.id = id;
        var timer = setTimeout(function () {
          pending.delete(id);
          // A runaway program: restart the interpreter so the next run is clean.
          reset();
          boot();
          resolve({ code: 124, output: "Stopped: the program ran longer than " + Math.round(timeoutMs / 1000) + " s in the browser.", ms: timeoutMs, timedOut: true });
        }, timeoutMs || 20000);
        pending.set(id, { resolve: resolve, reject: reject, timer: timer });
        worker.postMessage(payload);
      });
    });
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
      if (state !== "ready") return Promise.resolve(null);
      return request({ type: "unclosed", prefix: prefix }, 4000).then(function (r) {
        return r.blocks || null;
      });
    },
  };
})(window);
