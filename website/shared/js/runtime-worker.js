/* Geno playground worker.
 *
 * Boots Pyodide (CPython compiled to WebAssembly), unpacks the Geno package
 * built by website/build.py, and drives the real `geno` CLI against an
 * in-memory project. Nothing here reimplements Geno: check, test, run and
 * compile output is whatever the shipped toolchain prints.
 */
/* global importScripts, loadPyodide */
"use strict";

var pyodide = null;
var booting = null;
var out = [];

function post(msg) {
  self.postMessage(msg);
}

function boot(config) {
  if (booting) return booting;
  booting = (async function () {
    post({ type: "progress", stage: "python", label: "Loading Python (WebAssembly)" });
    importScripts(config.pyodideBase + "pyodide.js");
    pyodide = await loadPyodide({
      indexURL: config.pyodideBase,
      stdout: function (line) {
        out.push(line);
      },
      stderr: function (line) {
        out.push(line);
      },
    });
    post({ type: "progress", stage: "geno", label: "Loading the Geno toolchain" });
    var res = await fetch(config.bundleUrl);
    if (!res.ok) throw new Error("Could not fetch the Geno bundle (" + res.status + ")");
    var buf = new Uint8Array(await res.arrayBuffer());
    pyodide.FS.writeFile("/tmp/geno.zip", buf);
    await pyodide.runPythonAsync(
      [
        "import sys, os, zipfile",
        "zipfile.ZipFile('/tmp/geno.zip').extractall('/opt/geno')",
        "sys.path.insert(0, '/opt/geno')",
        "os.makedirs('/work', exist_ok=True)",
        "os.chdir('/work')",
        "os.environ['USER'] = 'ada'",
        "os.environ['NO_COLOR'] = '1'",
        "import geno, geno.constraints, geno.__main__",
        "from geno._version import __version__ as GENO_VERSION",
        "def _geno_cli(argv):",
        "    from geno.__main__ import main",
        "    try:",
        "        code = main(list(argv))",
        "    except SystemExit as e:",
        "        code = e.code",
        "    if code is None: code = 0",
        "    if not isinstance(code, int): code = 1",
        "    return code",
        "def _geno_unclosed(prefix):",
        "    try:",
        "        return list(geno.constraints.get_unclosed_blocks(prefix))",
        "    except Exception:",
        "        return None",
      ].join("\n")
    );
    var version = pyodide.globals.get("GENO_VERSION");
    post({ type: "ready", version: version });
  })();
  return booting;
}

function writeFiles(files) {
  Object.keys(files || {}).forEach(function (name) {
    pyodide.FS.writeFile("/work/" + name, files[name]);
  });
}

self.onmessage = async function (ev) {
  var msg = ev.data || {};
  try {
    if (msg.type === "boot") {
      await boot(msg.config);
      return;
    }
    await booting;
    if (msg.type === "cli") {
      writeFiles(msg.files);
      out = [];
      var t0 = performance.now();
      var cli = pyodide.globals.get("_geno_cli");
      var code = cli(pyodide.toPy(msg.args));
      cli.destroy();
      var ms = performance.now() - t0;
      var extra = {};
      if (msg.readBack) {
        try {
          extra.file = pyodide.FS.readFile("/work/" + msg.readBack, { encoding: "utf8" });
        } catch (e) {
          extra.file = null;
        }
      }
      post({ type: "result", id: msg.id, code: code, output: out.join("\n"), ms: ms, file: extra.file });
    } else if (msg.type === "unclosed") {
      var fn = pyodide.globals.get("_geno_unclosed");
      var r = fn(msg.prefix);
      fn.destroy();
      var list = r && r.toJs ? r.toJs() : r;
      if (r && r.destroy) r.destroy();
      post({ type: "result", id: msg.id, blocks: list });
    }
  } catch (err) {
    post({ type: "error", id: msg.id, message: String((err && err.message) || err) });
  }
};
