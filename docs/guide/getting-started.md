# Getting Started with Geno

## Installation

For a published release:

```bash
pip install geno-lang
```

For an unpublished checkout or development:

```bash
git clone https://github.com/davidiach/geno-lang.git
cd geno-lang
pip install -e ".[dev]"
```

## Your First Program

Create a file called `hello.geno`:

```geno
func greet(name: String) -> String
    example "Alice" -> "Hello, Alice!"
    return "Hello, " + name + "!"
end func

func main() -> String
    return greet("World")
end func
```

Run it:

```bash
geno run hello.geno
# => "Hello, World!"
```

## The REPL

Start an interactive session:

```bash
geno repl
```

Try evaluating expressions:

```
geno> 2 + 3
=> 5
geno> let x: Int = 10
geno> x * 2
=> 20
```

## Type Checking

Check a file for type errors without running it:

```bash
geno check hello.geno
# Type check passed: hello.geno
#   2 definitions
```

When an error has an unambiguous correction, such as a misspelled name, the
message says so (`fix: Change 'lenght' to 'length'`). `geno check --fix
hello.geno` applies every such fix and then checks again; editors offer them
as quick fixes.

## Formatting

`geno fmt` puts every file under a path in the one canonical layout:

```bash
geno fmt .            # rewrite files in place
geno fmt --check .    # exit 1 if any file would change (for CI)
geno fmt --diff .     # show the changes without writing them
```

The layout is four-space indentation per block, with:

- a match arm's body, when it is written below `| pattern ->`, indented under
  the arm;
- a line that starts with `.method(...)` or `|>`, or follows a line ending in
  an operator, indented one step further;
- one space around binary operators and `->` and after `,` and `:`, and none
  inside brackets or before a call's `(`;
- at most one blank line in a row.

Line breaks, comments and string contents are kept as written. Formatting
never changes what a file means: a file whose formatted text would parse to a
different program is left unchanged and reported. Every Geno program in the
Geno repository is kept in this form. Editors format through the language
server (`geno lsp`).

## Compiling

### To Python

```bash
geno compile hello.geno -o hello.py
python3 hello.py
```

### To JavaScript

```bash
geno compile --target js hello.geno -o hello.js
node hello.js
```

## Multi-File Projects

Geno supports importing modules from the filesystem. Given this structure:

```
project/
  Main.geno
  Utils.geno
```

`Utils.geno`:
```geno
func double(x: Int) -> Int
    example 3 -> 6
    return x * 2
end func
```

`Main.geno`:
```geno
import Utils

func main() -> Unit
    print(double(21))
    return ()
end func
```

```bash
geno run Main.geno --unsafe
# 42
```

In the 0.5 series `main`'s result is the process's exit status rather than
something the CLI displays, so a program that wants to show a value prints it.
Had `main` been declared `-> Int` here, `geno run` would have exited 42 and
printed nothing. See
[Entrypoint results and imports](../reference/runtime-semantics.md#entrypoint-results-and-imports).

Import resolution looks for `ModuleName.geno` in the same directory as the importing file.

## Capabilities

Some builtins require explicit capabilities for security. Plain `geno run`
grants `print`, `clock` and `random`. `--cap` grants exactly the capabilities
it lists, for examples and `main`, and the program still runs in the
process sandbox with its time, memory and process limits:

```bash
# Print output and regular expressions
geno run myfile.geno --cap print,regex

# File I/O (and print)
geno run myfile.geno --cap fs,print

# HTTP requests (Linux and macOS)
geno run myfile.geno --cap http,print

# Program arguments with cli_args()
geno run myfile.geno --cap env,print -- input.csv

# Environment variables: allow each name the program reads
geno run myfile.geno --cap env,print --allow-env HOME

# Multiple capabilities (comma-separated)
geno run myfile.geno --cap print,fs,env -- input.csv
```

The sandbox never grants `process` or `serve`, and does not grant `stdin` (or
`http` on Windows) yet. For trusted code only, `geno run --unsafe --cap ...`
runs the program in the interpreter without process isolation, where they
work. See the
[Capability Reference](../reference/capabilities.md) for every capability and
lane.

## What's Next?

- [Language Tour](language-tour.md) -- learn Geno's types, functions, and features
- [Building Your First App](first-app.md) -- build a complete program step by step
