import * as assert from "node:assert";
import Module = require("node:module");

type Document = {
  uri: { scheme: string; fsPath: string; toString(): string };
  languageId: string;
  version: number;
  isClosed: boolean;
  isDirty: boolean;
  lineCount: number;
  lineAt(line: number): { text: string };
};
type Callback = (error: Error | null, stdout: string, stderr: string) => void;
class Position {
  constructor(public line: number, public character: number) {}
}
class Range {
  constructor(public start: Position, public end: Position) {}
}
class Diagnostic {
  constructor(public range: Range, public message: string, public severity: number) {}
}

const callbacks: Callback[] = [];
const diagnostics = new Map<string, Diagnostic[]>();
let save: (document: Document) => void;
let close: (document: Document) => void;
let change: (event: { document: Document }) => void;
const disposable = { dispose() {} };
let mockLsp: unknown;
const vscode = {
  window: { createOutputChannel: () => ({ ...disposable, appendLine() {} }) },
  commands: { registerCommand: () => disposable },
  workspace: {
    isTrusted: true,
    textDocuments: [],
    getConfiguration: () => ({ get: () => "geno" }),
    createFileSystemWatcher: () => disposable,
    onDidSaveTextDocument: (callback: typeof save) => { save = callback; return disposable; },
    onDidOpenTextDocument: () => disposable,
    onDidCloseTextDocument: (callback: typeof close) => { close = callback; return disposable; },
    onDidChangeTextDocument: (callback: typeof change) => { change = callback; return disposable; },
  },
  languages: {
    createDiagnosticCollection: () => ({
      ...disposable,
      set: (uri: Document["uri"], values: Diagnostic[]) => diagnostics.set(uri.toString(), values),
      delete: (uri: Document["uri"]) => diagnostics.delete(uri.toString()),
    }),
  },
  Position, Range, Diagnostic,
  DiagnosticSeverity: { Error: 0 },
};

// Exercise the compiled extension entrypoint with mocked VS Code/process edges.
const loader = Module as unknown as {
  _load(request: string, parent: unknown, isMain: boolean): unknown;
};
const originalLoad = loader._load;
loader._load = function(request, parent, isMain) {
  if (request === "vscode") return vscode;
  if (request === "child_process") {
    return { execFile: (_path: string, _args: string[], _opts: unknown, callback: Callback) => callbacks.push(callback) };
  }
  if (request === "vscode-languageclient/node") {
    if (mockLsp) return mockLsp;
    throw new Error("LSP unavailable");
  }
  return originalLoad.call(this, request, parent, isMain);
};

function document(scheme = "file"): Document {
  return {
    uri: { scheme, fsPath: "/tmp/main.geno", toString: () => `${scheme}:///tmp/main.geno` },
    languageId: "geno", version: 1, isClosed: false, isDirty: false, lineCount: 1,
    lineAt: (line) => { assert.strictEqual(line, 0); return { text: "abc" }; },
  };
}
function complete(error: Error | null, stderr = ""): void {
  callbacks.shift()!(error, "", stderr);
}

async function main(): Promise<void> {
  const extension = require("./extension") as {
    activate(context: { subscriptions: unknown[] }): void;
    deactivate(): unknown;
  };
  extension.activate({ subscriptions: [] });
  await new Promise<void>((resolve) => setImmediate(resolve));

  const doc = document();
  const key = doc.uri.toString();
  save(doc);
  complete(new Error("spawn geno ENOENT"));
  assert.match(diagnostics.get(key)![0].message, /ENOENT/, "launch failures must be visible");

  save(doc);
  save(doc);
  const stale = callbacks.shift()!;
  complete(null);
  stale(new Error("old"), "", "Type Error: main.geno:1:1: stale error");
  assert.deepStrictEqual(diagnostics.get(key), [], "older requests must not overwrite newer results");

  save(doc);
  doc.version++;
  change({ document: doc });
  complete(new Error("old"), "Type Error: main.geno:1:1: stale error");
  assert.ok(!diagnostics.has(key), "edits must invalidate in-flight diagnostics");

  save(doc);
  doc.isClosed = true;
  close(doc);
  complete(new Error("old"));
  assert.ok(!diagnostics.has(key), "closed files must stay cleared");

  doc.isClosed = false;
  save(doc);
  complete(new Error("check failed"), "Type Error: main.geno:999:999: error");
  assert.deepStrictEqual(diagnostics.get(key)![0].range, new Range(new Position(0, 3), new Position(0, 3)));

  save(document("untitled"));
  doc.isDirty = true;
  save(doc);
  assert.strictEqual(callbacks.length, 0, "virtual and dirty buffers must not be checked from disk");

  doc.isDirty = false;
  save(doc);
  diagnostics.clear();
  extension.deactivate();
  complete(new Error("old"));
  assert.strictEqual(diagnostics.size, 0, "deactivation must invalidate callbacks");

  const clients: Array<{ running: boolean; finishStart(): void }> = [];
  mockLsp = {
    TransportKind: { stdio: 0 },
    LanguageClient: class {
      running = false;
      finishStart = () => {};
      constructor() { clients.push(this); }
      start(): Promise<void> {
        return new Promise((resolve) => {
          this.finishStart = () => { this.running = true; resolve(); };
        });
      }
      stop(): Promise<void> { this.running = false; return Promise.resolve(); }
    },
  };
  extension.activate({ subscriptions: [] });
  await extension.deactivate();
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.strictEqual(clients.length, 0, "deactivation before import must prevent startup");

  extension.activate({ subscriptions: [] });
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.strictEqual(clients.length, 1);
  await extension.deactivate();
  extension.activate({ subscriptions: [] });
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.strictEqual(clients.length, 2);
  clients[1].finishStart();
  clients[0].finishStart();
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.ok(!clients[0].running, "late cancelled startup must stop its own client");
  assert.ok(clients[1].running, "older startup must not stop a new activation's client");
  await extension.deactivate();
  console.log("All extension diagnostics tests passed.");
}

main().catch((error) => { console.error(error); process.exitCode = 1; }).finally(() => {
  loader._load = originalLoad;
});
