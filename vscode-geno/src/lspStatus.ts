export type LspStartupFailureKind =
  | "client-module-unavailable"
  | "server-launch-failed"
  | "unknown";

export interface LspStartupFailure {
  kind: LspStartupFailureKind;
  message: string;
  detail?: string;
}

function errorCode(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null || !("code" in error)) {
    return undefined;
  }
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : undefined;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim().length > 0) {
    return error.message.trim();
  }
  if (typeof error === "string" && error.trim().length > 0) {
    return error.trim();
  }
  return "Unknown LSP startup error";
}

export function lspStartupFailureFromError(error: unknown): LspStartupFailure {
  const code = errorCode(error);
  const message = errorMessage(error);

  if (
    code === "MODULE_NOT_FOUND" ||
    message.includes("Cannot find module 'vscode-languageclient")
  ) {
    return {
      kind: "client-module-unavailable",
      message: "vscode-languageclient is unavailable; using fallback diagnostics",
      detail: message,
    };
  }

  if (code === "ENOENT" || message.includes("spawn geno ENOENT")) {
    return {
      kind: "server-launch-failed",
      message: "geno lsp could not be launched; using fallback diagnostics",
      detail: message,
    };
  }

  return {
    kind: "unknown",
    message: "Geno language server startup failed; using fallback diagnostics",
    detail: message,
  };
}

export function formatLspStartupFailure(failure: LspStartupFailure): string {
  const detail = failure.detail ? ` Detail: ${failure.detail}` : "";
  return `[${failure.kind}] ${failure.message}.${detail}`;
}
