# Build a Todo App with Geno

This tutorial walks you through building a complete todo-list program in Geno.
Along the way you will use algebraic data types, pattern matching, `Result` error
handling, list operations, pipelines, and `example` clauses. By the end you will
have a working program you can type-check, run, and extend into an interactive
app.

## What We Are Building

A small todo manager that can:

- Create new todo items
- Mark a todo as done
- Filter todos by status
- Count todos by status
- Handle errors when an operation is invalid

## Project Setup

Create a directory and two files:

```
todo/
  Todo.geno
  Main.geno
```

`Todo.geno` holds our types and core logic. `Main.geno` imports it and ties
everything together in a `main` function.

## Defining Types

Geno models data with algebraic data types (ADTs). We need a `Status` type that
is either `Pending` or `Done`, and a `Todo` type that pairs a title with a
status.

Add the following to `Todo.geno`:

```geno
type Status = Pending | Done

type Todo = Todo(title: String, status: Status)
```

`Pending` and `Done` are nullary constructors -- they carry no data. `Todo` is a
constructor with two named fields. You create values the same way you declare
them:

```geno
let buy_milk: Todo = Todo("Buy milk", Pending)
let walk_dog: Todo = Todo("Walk the dog", Done)
```

## Core Functions

### Displaying a Status

Pattern match on `Status` to produce a human-readable string:

```geno
func show_status(s: Status) -> String
    example Pending -> "[ ]"
    example Done -> "[x]"

    match s with
        | Pending -> return "[ ]"
        | Done    -> return "[x]"
    end match
end func show_status
```

### Displaying a Todo

Use the `show_status` helper and field access to format a single todo:

```geno
func show_todo(t: Todo) -> String
    example Todo("Buy milk", Pending) -> "[ ] Buy milk"
    example Todo("Walk dog", Done)    -> "[x] Walk dog"

    return show_status(t.status) + " " + t.title
end func show_todo
```

### Adding a Todo

Build a new `Todo` with `Pending` status and append it to the list:

```geno
func add_todo(todos: List[Todo], title: String) -> List[Todo]
    example [], "Buy milk" -> [Todo("Buy milk", Pending)]

    let new_todo: Todo = Todo(title, Pending)
    return append(todos, new_todo)
end func add_todo
```

`append` returns a new list -- lists in Geno are immutable.

### Completing a Todo

Walk the list and flip the matching item to `Done`. If no item matches,
return the list unchanged:

```geno
func complete_todo(todos: List[Todo], title: String) -> List[Todo]
    example [Todo("A", Pending)], "A" -> [Todo("A", Done)]
    example [Todo("A", Pending)], "B" -> [Todo("A", Pending)]

    return map(todos, fn(t: Todo) -> match t.title == title with
        | true -> Todo(t.title, Done)
        | false -> t
    end match)
end func complete_todo
```

### Filtering by Status

Use `filter` with a lambda that pattern-matches the status field:

```geno
func filter_by_status(todos: List[Todo], s: Status) -> List[Todo]
    example [Todo("A", Pending), Todo("B", Done)], Pending -> [Todo("A", Pending)]
    example [Todo("A", Pending), Todo("B", Done)], Done    -> [Todo("B", Done)]

    return filter(todos, fn(t: Todo) -> t.status == s)
end func filter_by_status
```

### Counting by Status

Count is just the length of a filtered list. The pipeline operator `|>` makes
this read left to right:

```geno
func count_by_status(todos: List[Todo], s: Status) -> Int
    example [Todo("A", Pending), Todo("B", Done), Todo("C", Pending)], Pending -> 2
    example [Todo("A", Pending), Todo("B", Done), Todo("C", Pending)], Done    -> 1
    example [], Pending -> 0

    return todos
        |> filter(_, fn(t: Todo) -> t.status == s)
        |> length(_)
end func count_by_status
```

The `_` placeholder marks where the piped value is inserted.

## Using Result Types

What if someone tries to complete a todo that does not exist? Rather than
silently returning the original list, we can use `Result` to signal the error.

```geno
func safe_complete(todos: List[Todo], title: String) -> Result[List[Todo], String]
    example [Todo("A", Pending)], "A" -> Ok([Todo("A", Done)])
    example [Todo("A", Pending)], "Z" -> Err("todo not found: Z")

    let found: List[Todo] = filter(todos, fn(t: Todo) -> t.title == title)
    if length(found) == 0 then
        return Err("todo not found: " + title)
    end if
    return Ok(complete_todo(todos, title))
end func safe_complete
```

Callers can then pattern-match on the result:

```geno
func complete_and_report(todos: List[Todo], title: String) -> String
    example [Todo("A", Pending)], "A" -> "Completed: A"
    example [Todo("A", Pending)], "Z" -> "Error: todo not found: Z"

    let result: Result[List[Todo], String] = safe_complete(todos, title)
    match result with
        | Ok(_)  -> return "Completed: " + title
        | Err(e) -> return "Error: " + e
    end match
end func complete_and_report
```

## Testing with Example Clauses

Every function above already has `example` clauses. Geno verifies these at
runtime -- they act as built-in tests. When you run or type-check a file, the
runtime confirms each example returns the expected value.

Example clauses follow a simple format:

```
example <arg1>, <arg2>, ... -> <expected_result>
```

A few guidelines:

- Cover the happy path **and** the edge case (empty list, missing item).
- Use constructors directly in examples: `Todo("A", Pending)`, `Ok(5)`,
  `Err("bad")`.
- Functions with three or more parameters use named arguments at the call site,
  but example clauses list values positionally.

If an example clause fails, Geno reports the function name, the input, the
expected output, and the actual output, so you can fix the bug immediately.

## Putting It All Together

Create `Main.geno`:

```geno
import Todo

func main() -> List[String]
    var todos: List[Todo] = []

    // Add some items
    todos = add_todo(todos, "Buy milk")
    todos = add_todo(todos, "Write tutorial")
    todos = add_todo(todos, "Walk the dog")

    // Complete one
    todos = complete_todo(todos, "Buy milk")

    // Build a summary
    let pending: Int = count_by_status(todos, Pending)
    let done: Int = count_by_status(todos, Done)

    let pending_todos: List[Todo] = filter_by_status(todos, Pending)
    var lines: List[String] = []

    var i: Int = 0
    while i < length(pending_todos) do
        lines = append(lines, show_todo(pending_todos[i]))
        i = i + 1
    end while

    let header: String = f"{to_string(done)} done, {to_string(pending)} pending"
    return concat([header], lines)
end func main
```

## Running and Checking

### Type-check both files

```bash
geno check Main.geno
# Type check passed: Main.geno
#   2 modules
```

The checker follows the `import Todo` statement and validates `Todo.geno` as
well.

### Run the program

```bash
geno run Main.geno --unsafe
# => ["1 done, 2 pending", "[ ] Write tutorial", "[ ] Walk the dog"]
```

### Verify examples only

Every `example` clause is checked when you run the file. If any example does not
match, Geno prints a clear error message showing the expected versus actual
value.

## Building an Interactive App

Once the core logic works, you can wrap it in an interactive GUI using Geno's
app mode. App mode programs define three special functions -- `init`, `update`,
and `render` -- and compile to static HTML/JS artifacts. Use `--single-file`
when you need one self-contained HTML file.

A minimal interactive todo app would look like this:

```geno
import Todo

type AppState = AppState(todos: Array[String], count: Int, max: Int)

func init() -> AppState
    return AppState(array_new(size: 100, default: ""), 0, 100)
end func init

func update(state: AppState, dt: Float) -> AppState
    // Handle mouse clicks and text input to add/complete todos
    return state
end func update

func render(state: AppState) -> Unit
    clear_screen("#1e1e2e")
    draw_text(text: "My Todos", x: 50, y: 20, size: 24, color: "#cdd6f4")
    // Draw todo items, buttons, etc.
end func render
```

Compile it:

```bash
geno build TodoApp.geno -o todo.html --width 800 --height 600 --title "Todo App"
```

Open `todo.html` in your browser and you have a graphical todo app. See the
`examples/apps/` directory for complete interactive applications.

## Next Steps

- [Language Tour](language-tour.md) -- the full set of Geno features with
  runnable examples
- [Building Your First App](first-app.md) -- another step-by-step walkthrough
  covering JSON and modules
- [Getting Started](getting-started.md) -- installation, the REPL, and
  capabilities
- Explore the [examples/](../../examples/) directory for more programs including
  quicksort, word count, and interactive apps
