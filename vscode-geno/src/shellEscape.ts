export type ShellKind = "cmd" | "posix" | "powershell";

export type RunFileInvocation =
  | {
      kind: "direct";
      shellArgs: string[];
      shellPath: string;
    }
  | {
      kind: "sendText";
      text: string;
    };

function normalizeShellIdentifier(shellPath: string): string {
  const normalized = shellPath.replace(/\\/g, "/").trim().toLowerCase();
  return normalized.split("/").pop() ?? normalized;
}

function detectKnownShellKind(shellPath?: string): ShellKind | undefined {
  const shellName = normalizeShellIdentifier(shellPath ?? "");

  if (!shellName) {
    return undefined;
  }

  if (shellName === "pwsh" || shellName === "pwsh.exe") {
    return "powershell";
  }
  if (
    shellName === "powershell" ||
    shellName === "powershell.exe" ||
    shellName === "powershell_ise.exe"
  ) {
    return "powershell";
  }
  if (shellName === "wsl" || shellName === "wsl.exe") {
    return "posix";
  }
  if (
    shellName === "gitbash" ||
    shellName === "bash" ||
    shellName === "bash.exe" ||
    shellName === "csh" ||
    shellName === "csh.exe" ||
    shellName === "sh" ||
    shellName === "sh.exe" ||
    shellName === "ksh" ||
    shellName === "ksh.exe" ||
    shellName === "zsh" ||
    shellName === "zsh.exe" ||
    shellName === "fish" ||
    shellName === "fish.exe"
  ) {
    return "posix";
  }
  if (shellName === "cmd" || shellName === "cmd.exe") {
    return "cmd";
  }
  return undefined;
}

export function detectShellKind(
  shellPath?: string,
  platform: NodeJS.Platform = process.platform
): ShellKind {
  const knownShellKind = detectKnownShellKind(shellPath);
  if (knownShellKind !== undefined) {
    return knownShellKind;
  }
  return platform === "win32" ? "cmd" : "posix";
}

function escapeForPosix(filePath: string): string {
  return `'${filePath.replace(/'/g, "'\\''")}'`;
}

function escapeForPowerShell(filePath: string): string {
  return `'${filePath.replace(/'/g, "''")}'`;
}

export function shellEscape(
  filePath: string,
  shellPath?: string,
  platform: NodeJS.Platform = process.platform
): string {
  switch (detectShellKind(shellPath, platform)) {
    case "powershell":
      return escapeForPowerShell(filePath);
    case "cmd":
      return `"${filePath.replace(/"/g, '""')}"`;
    case "posix":
      return escapeForPosix(filePath);
  }
}

function isShellSafeBareExecutable(executablePath: string): boolean {
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(executablePath);
}

function escapeExecutable(
  executablePath: string,
  shellPath?: string,
  platform: NodeJS.Platform = process.platform
): string {
  if (isShellSafeBareExecutable(executablePath)) {
    return executablePath;
  }

  if (detectShellKind(shellPath, platform) === "powershell") {
    return `& ${escapeForPowerShell(executablePath)}`;
  }
  return shellEscape(executablePath, shellPath, platform);
}

export function buildRunFileInvocation(
  genoPath: string,
  filePath: string,
  shellPath?: string,
  platform: NodeJS.Platform = process.platform
): RunFileInvocation {
  const shellKind = detectKnownShellKind(shellPath);

  if (shellKind === undefined || shellKind === "cmd") {
    return {
      kind: "direct",
      shellArgs: ["run", filePath],
      shellPath: genoPath,
    };
  }

  return {
    kind: "sendText",
    text: `${escapeExecutable(genoPath, shellPath, platform)} run ${shellEscape(
      filePath,
      shellPath,
      platform
    )}`,
  };
}
