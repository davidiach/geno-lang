"use strict";

const fs = require("node:fs");

// vscode-languageclient 11.0.0 calls makeRe() only to reject a glob with no
// parsed alternatives. Minimatch 11 exposes those alternatives in set and
// matches them directly. Remove this adjustment when the client supports v11.
const oldCheck = "if (!matcher.makeRe()) {";
for (const moduleName of ["fileOperations", "utils/globPattern"]) {
  const file = require.resolve(`vscode-languageclient/$test/common/${moduleName}`);
  const source = fs.readFileSync(file, "utf8");
  if (source.includes(oldCheck)) {
    fs.writeFileSync(file, source.replace(oldCheck, "if (matcher.set.length === 0) {"));
  }
}
