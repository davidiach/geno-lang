import * as assert from "node:assert";
import {
  formatLspStartupFailure,
  lspStartupFailureFromError,
} from "./lspStatus";

const moduleError = Object.assign(
  new Error("Cannot find module 'vscode-languageclient/node'"),
  { code: "MODULE_NOT_FOUND" }
);
assert.deepStrictEqual(
  lspStartupFailureFromError(moduleError),
  {
    kind: "client-module-unavailable",
    message: "vscode-languageclient is unavailable; using fallback diagnostics",
    detail: "Cannot find module 'vscode-languageclient/node'",
  },
  "missing vscode-languageclient should be typed"
);

const serverError = Object.assign(new Error("spawn geno ENOENT"), {
  code: "ENOENT",
});
assert.deepStrictEqual(
  lspStartupFailureFromError(serverError),
  {
    kind: "server-launch-failed",
    message: "geno lsp could not be launched; using fallback diagnostics",
    detail: "spawn geno ENOENT",
  },
  "missing geno executable should be typed"
);

const unknownFailure = lspStartupFailureFromError(new Error("handshake failed"));
assert.strictEqual(unknownFailure.kind, "unknown");
assert.strictEqual(
  formatLspStartupFailure(unknownFailure),
  "[unknown] Geno language server startup failed; using fallback diagnostics. Detail: handshake failed"
);

console.log("All lspStatus tests passed.");
