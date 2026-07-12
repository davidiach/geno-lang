import * as vscode from "vscode";
import { execFile } from "child_process";
import {
  formatLspStartupFailure,
  lspStartupFailureFromError,
} from "./lspStatus";
import { buildRunFileInvocation } from "./shellEscape";

let diagnosticCollection: vscode.DiagnosticCollection;
let client: any; // LanguageClient (dynamically loaded)
let outputChannel: vscode.OutputChannel | undefined;
let executionFeaturesStarted = false;
const DEFAULT_GENO_SERVER_PATH = "geno";

export function activate(context: vscode.ExtensionContext) {
  outputChannel = vscode.window.createOutputChannel("Geno");
  context.subscriptions.push(outputChannel);

  // Register the "Run Geno File" command
  const runFileCmd = vscode.commands.registerCommand("geno.runFile", () => {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.languageId !== "geno") {
      vscode.window.showErrorMessage("No active Geno file to run.");
      return;
    }
    if (!ensureTrustedWorkspace("Run Geno File")) {
      return;
    }
    const filePath = editor.document.uri.fsPath;
    const runTerminal = vscode.window.terminals.find((t) => t.name === "Geno");
    const terminalShell = runTerminal?.state.shell;
    const genoPath = getGenoServerPath();
    const invocation = buildRunFileInvocation(
      genoPath,
      filePath,
      terminalShell
    );

    if (invocation.kind === "direct") {
      // cmd.exe does not have a robust sendText quoting story for arbitrary
      // filenames, so run Geno directly in a fresh terminal process instead.
      runTerminal?.dispose();
      const directTerminal = vscode.window.createTerminal({
        name: "Geno",
        shellArgs: invocation.shellArgs,
        shellPath: invocation.shellPath,
      });
      directTerminal.show();
      return;
    }

    const terminal = runTerminal ?? vscode.window.createTerminal("Geno");
    terminal.show();
    terminal.sendText(invocation.text);
  });
  context.subscriptions.push(runFileCmd);

  if (!vscode.workspace.isTrusted) {
    outputChannel.appendLine(
      "Workspace is untrusted; Geno execution features are disabled until trust is granted."
    );
    context.subscriptions.push(
      vscode.workspace.onDidGrantWorkspaceTrust(() => {
        startExecutionFeatures(context);
      })
    );
    return;
  }

  startExecutionFeatures(context);
}

function ensureTrustedWorkspace(action: string): boolean {
  if (vscode.workspace.isTrusted) {
    return true;
  }
  void vscode.window.showWarningMessage(
    `${action} is disabled until this workspace is trusted.`
  );
  outputChannel?.appendLine(
    `${action} skipped because the workspace is untrusted.`
  );
  return false;
}

function startExecutionFeatures(context: vscode.ExtensionContext): void {
  if (executionFeaturesStarted) {
    return;
  }
  executionFeaturesStarted = true;

  // Try to start the LSP client first; fall back to execFile-based diagnostics
  tryStartLSP(context).then((started) => {
    if (started) {
      return; // LSP handles everything
    }

    // Fallback: execFile-based diagnostics
    diagnosticCollection =
      vscode.languages.createDiagnosticCollection("geno");
    context.subscriptions.push(diagnosticCollection);

    const onSave = vscode.workspace.onDidSaveTextDocument((document) => {
      if (document.languageId === "geno") {
        checkFile(document);
      }
    });
    context.subscriptions.push(onSave);

    const onOpen = vscode.workspace.onDidOpenTextDocument((document) => {
      if (document.languageId === "geno") {
        checkFile(document);
      }
    });
    context.subscriptions.push(onOpen);

    vscode.workspace.textDocuments.forEach((document) => {
      if (document.languageId === "geno") {
        checkFile(document);
      }
    });
  });
}

export function deactivate(): Thenable<void> | undefined {
  if (diagnosticCollection) {
    diagnosticCollection.dispose();
  }
  if (client) {
    return client.stop();
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// LSP client
// ---------------------------------------------------------------------------

async function tryStartLSP(
  context: vscode.ExtensionContext
): Promise<boolean> {
  try {
    const { LanguageClient, TransportKind } = await import(
      "vscode-languageclient/node"
    );

    const serverOptions = {
      command: getGenoServerPath(),
      args: ["lsp"],
      transport: TransportKind.stdio,
    };

    const clientOptions = {
      documentSelector: [{ scheme: "file", language: "geno" }],
    };

    client = new LanguageClient(
      "genoLanguageServer",
      "Geno Language Server",
      serverOptions,
      clientOptions
    );

    await client.start();
    context.subscriptions.push({ dispose: () => client?.stop() });
    return true;
  } catch (error) {
    const failure = lspStartupFailureFromError(error);
    outputChannel?.appendLine(formatLspStartupFailure(failure));
    return false;
  }
}

// ---------------------------------------------------------------------------
// Fallback: execFile-based diagnostics
// ---------------------------------------------------------------------------

function getGenoServerPath(): string {
  const configuredPath = vscode.workspace
    .getConfiguration("geno")
    .get<string>("serverPath", DEFAULT_GENO_SERVER_PATH)
    .trim();
  return configuredPath || DEFAULT_GENO_SERVER_PATH;
}

function checkFile(document: vscode.TextDocument) {
  const filePath = document.uri.fsPath;

  execFile(
    getGenoServerPath(),
    ["check", filePath],
    { timeout: 10000 },
    (error, _stdout, stderr) => {
      const diagnostics: vscode.Diagnostic[] = [];

      if (error && stderr) {
        const lines = stderr.split("\n");
        for (const line of lines) {
          const diagnostic = parseDiagnosticLine(line, document);
          if (diagnostic) {
            diagnostics.push(diagnostic);
          }
        }

        if (diagnostics.length === 0) {
          diagnostics.push(projectDiagnostic(stderr, document));
        }
      }

      diagnosticCollection.set(document.uri, diagnostics);
    }
  );
}

// Matches: "<file>:<line>:<col>: <ErrorType>: <message>"
// or:      "  <file>:<line>:<col>: <message>" (indented multi-error)
const ERROR_PATTERN =
  /^[\s]*(?:.+?):(\d+):(\d+):\s*(?:Type Error|Parse Error):\s*(.+)$/;

// Matches: "Type Error: <file>:<line>:<col>: <message>"
const ERROR_PATTERN_ALT =
  /^(?:Type|Parse|Lexer) Error:\s*(?:.+?):(\d+):(\d+):\s*(.+)$/;

// Matches indented sub-error lines: "  <file>:<line>:<col>: <message>"
const ERROR_PATTERN_SUB = /^\s+(?:.+?):(\d+):(\d+):\s*(.+)$/;

function projectDiagnostic(
  stderr: string,
  document: vscode.TextDocument
): vscode.Diagnostic {
  const message =
    stderr
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 0) ?? "geno check failed";
  const firstLine = document.lineAt(0).text;
  return new vscode.Diagnostic(
    new vscode.Range(
      new vscode.Position(0, 0),
      new vscode.Position(0, firstLine.length)
    ),
    message,
    vscode.DiagnosticSeverity.Error
  );
}

function parseDiagnosticLine(
  line: string,
  document: vscode.TextDocument
): vscode.Diagnostic | null {
  let match = line.match(ERROR_PATTERN);
  if (!match) {
    match = line.match(ERROR_PATTERN_ALT);
  }
  if (!match) {
    match = line.match(ERROR_PATTERN_SUB);
  }
  if (!match) {
    return null;
  }

  const lineNum = Math.max(0, parseInt(match[1], 10) - 1);
  const colNum = Math.max(0, parseInt(match[2], 10) - 1);
  const message = match[3].trim();

  const range = new vscode.Range(
    new vscode.Position(lineNum, colNum),
    new vscode.Position(
      lineNum,
      document.lineAt(Math.min(lineNum, document.lineCount - 1)).text.length
    )
  );

  return new vscode.Diagnostic(
    range,
    message,
    vscode.DiagnosticSeverity.Error
  );
}
