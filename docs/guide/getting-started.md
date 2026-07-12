# Getting Started with Geno

## Installation

```bash
pip install geno-lang
```

For development:

```bash
git clone https://github.com/davidiach/geno-lang.git
cd geno
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

func main() -> Int
    return double(21)
end func
```

```bash
geno run Main.geno --unsafe
# => 42
```

Import resolution looks for `ModuleName.geno` in the same directory as the importing file.

## Capabilities

Some builtins require explicit capabilities for security. `geno run --cap`
must be paired with `--unsafe` for direct interpreter execution, or with
`--json` for embedding API execution:

```bash
# Print output
geno run myfile.geno --unsafe --cap print

# File I/O
geno run myfile.geno --unsafe --cap fs

# HTTP requests
geno run myfile.geno --unsafe --cap http

# Environment variables
geno run myfile.geno --unsafe --cap env

# Multiple capabilities (comma-separated)
geno run myfile.geno --unsafe --cap print,fs,env
```

## What's Next?

- [Language Tour](language-tour.md) -- learn Geno's types, functions, and features
- [Building Your First App](first-app.md) -- build a complete program step by step
