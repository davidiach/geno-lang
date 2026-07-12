import * as assert from "node:assert";
import {
  buildRunFileInvocation,
  detectShellKind,
  shellEscape,
} from "./shellEscape";

const detectCases: [string, string | undefined, NodeJS.Platform, string][] = [
  ["bash on Unix", "/bin/bash", "linux", "posix"],
  [
    "PowerShell on Windows",
    "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
    "win32",
    "powershell",
  ],
  ["pwsh on Windows", "C:\\Program Files\\PowerShell\\7\\pwsh.exe", "win32", "powershell"],
  ["detected pwsh shell id", "pwsh", "win32", "powershell"],
  ["Git Bash on Windows", "C:\\Program Files\\Git\\bin\\bash.exe", "win32", "posix"],
  ["detected gitbash shell id", "gitbash", "win32", "posix"],
  ["WSL on Windows", "C:\\Windows\\System32\\wsl.exe", "win32", "posix"],
  ["cmd on Windows", "C:\\Windows\\System32\\cmd.exe", "win32", "cmd"],
  ["unknown shell on Windows falls back to cmd-safe mode", undefined, "win32", "cmd"],
];

for (const [label, shellPath, platform, expected] of detectCases) {
  assert.strictEqual(
    detectShellKind(shellPath, platform),
    expected,
    `${label}: detectShellKind(${JSON.stringify(shellPath)}, ${platform})`
  );
}

const escapeCases: [string, string, string | undefined, NodeJS.Platform, string][] = [
  ["POSIX single quote", "/tmp/it's.geno", "/bin/bash", "linux", "'/tmp/it'\\''s.geno'"],
  [
    "POSIX backticks and dollars stay literal",
    "/tmp/$(whoami)`id`.geno",
    "/bin/zsh",
    "linux",
    "'/tmp/$(whoami)`id`.geno'",
  ],
  [
    "PowerShell single quote",
    "C:\\tmp\\it's.geno",
    "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
    "win32",
    "'C:\\tmp\\it''s.geno'",
  ],
  [
    "PowerShell dollars and backticks stay literal",
    "C:\\tmp\\$(whoami)`id`.geno",
    "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
    "win32",
    "'C:\\tmp\\$(whoami)`id`.geno'",
  ],
];

for (const [label, input, shellPath, platform, expected] of escapeCases) {
  const result = shellEscape(input, shellPath, platform);
  assert.strictEqual(
    result,
    expected,
    `${label}: shellEscape(${JSON.stringify(input)}) = ${JSON.stringify(result)}, expected ${JSON.stringify(expected)}`
  );
}

assert.deepStrictEqual(
  buildRunFileInvocation(
    "geno",
    "/tmp/$(whoami).geno",
    "/bin/bash",
    "linux"
  ),
  {
    kind: "sendText",
    text: "geno run '/tmp/$(whoami).geno'",
  },
  "POSIX shells should keep sendText with POSIX quoting"
);

assert.deepStrictEqual(
  buildRunFileInvocation(
    "/opt/Geno Tools/bin/geno",
    "/tmp/$(whoami).geno",
    "/bin/bash",
    "linux"
  ),
  {
    kind: "sendText",
    text: "'/opt/Geno Tools/bin/geno' run '/tmp/$(whoami).geno'",
  },
  "POSIX shells should quote configured executable paths"
);

assert.deepStrictEqual(
  buildRunFileInvocation(
    "geno",
    "/tmp/bad'; Write-Output GENO_INJECTION; '.geno",
    undefined,
    "linux"
  ),
  {
    kind: "direct",
    shellArgs: ["run", "/tmp/bad'; Write-Output GENO_INJECTION; '.geno"],
    shellPath: "geno",
  },
  "missing shell identity should avoid sendText on non-Windows terminals"
);

assert.deepStrictEqual(
  buildRunFileInvocation(
    "geno",
    "/tmp/bad'; Write-Output GENO_INJECTION; '.geno",
    "/opt/custom-shell",
    "linux"
  ),
  {
    kind: "direct",
    shellArgs: ["run", "/tmp/bad'; Write-Output GENO_INJECTION; '.geno"],
    shellPath: "geno",
  },
  "unrecognized shells should avoid sendText instead of guessing POSIX quoting"
);

assert.deepStrictEqual(
  buildRunFileInvocation(
    "geno",
    "C:\\tmp\\$(whoami).geno",
    "pwsh",
    "win32"
  ),
  {
    kind: "sendText",
    text: "geno run 'C:\\tmp\\$(whoami).geno'",
  },
  "PowerShell should keep sendText with PowerShell quoting"
);

assert.deepStrictEqual(
  buildRunFileInvocation(
    "C:\\Program Files\\Geno\\geno.exe",
    "C:\\tmp\\$(whoami).geno",
    "pwsh",
    "win32"
  ),
  {
    kind: "sendText",
    text: "& 'C:\\Program Files\\Geno\\geno.exe' run 'C:\\tmp\\$(whoami).geno'",
  },
  "PowerShell should invoke quoted configured executable paths with call operator"
);

assert.deepStrictEqual(
  buildRunFileInvocation(
    "geno",
    "C:\\tmp\\unsafe $(whoami).geno",
    "gitbash",
    "win32"
  ),
  {
    kind: "sendText",
    text: "geno run 'C:\\tmp\\unsafe $(whoami).geno'",
  },
  "Git Bash should keep sendText with POSIX quoting"
);

assert.deepStrictEqual(
  buildRunFileInvocation(
    "C:\\Program Files\\Geno\\geno.exe",
    "C:\\tmp\\unsafe $(whoami).geno",
    "C:\\Windows\\System32\\cmd.exe",
    "win32"
  ),
  {
    kind: "direct",
    shellArgs: ["run", "C:\\tmp\\unsafe $(whoami).geno"],
    shellPath: "C:\\Program Files\\Geno\\geno.exe",
  },
  "cmd.exe should avoid sendText and launch the configured Geno executable directly"
);

console.log("All shellEscape tests passed.");
