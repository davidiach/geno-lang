# Building Your First App

This walkthrough builds a small command-line tool that reads a JSON config file and processes it.

## Step 1: Define the Data

Create `Config.geno`:

```geno
func parse_config(json_text: String) -> Result[String, String]
    example "{\"name\": \"myapp\"}" -> Ok("myapp")

    let parsed: Result[JsonValue, String] = json_parse(text: json_text)
    match parsed with
        | Ok(val) ->
            match val with
                | JsonObject(entries) ->
                    let found: Option[String] = find_entry(entries, "name")
                    match found with
                        | Some(name) -> return Ok(name)
                        | None -> return Err("missing 'name' field")
                    end match
                | _ -> return Err("expected JSON object")
            end match
        | Err(e) -> return Err(e)
    end match
end func

func find_entry(entries: List[(String, JsonValue)], key: String) -> Option[String]
    example [("name", JsonString("test"))], "name" -> Some("test")
    example [], "name" -> None

    if length(entries) == 0 then
        return None
    end if
    let (k, v): (String, JsonValue) = head(entries)
    if k == key then
        match v with
            | JsonString(s) -> return Some(s)
            | _ -> return None
        end match
    end if
    return find_entry(tail(entries), key)
end func
```

## Step 2: Write the Main Program

Create `Main.geno`:

```geno
import Config

func main() -> String
    let json_text: String = "{\"name\": \"my-geno-app\", \"version\": \"1.0\"}"
    let result: Result[String, String] = parse_config(json_text)
    match result with
        | Ok(name) -> return f"App name: {name}"
        | Err(e) -> return f"Error: {e}"
    end match
end func
```

## Step 3: Run It

```bash
geno run Main.geno --unsafe
# => "App name: my-geno-app"
```

## Step 4: Type Check

```bash
geno check Main.geno
# Type check passed: Main.geno
#   1 definitions, 1 modules
```

## Key Takeaways

1. **Specs drive correctness**: Every function has `example` clauses that are verified at runtime
2. **Types catch errors early**: The type checker validates all code before execution
3. **Pattern matching is exhaustive**: You must handle all cases
4. **Modules keep code organized**: `import ModuleName` loads `ModuleName.geno` from the same directory
5. **JSON is built in**: `json_parse` and `json_stringify` work without any imports or capabilities

## Next Steps

- Read the [Language Specification](../spec/v0.2.md) for the full reference
- Explore the [examples/](../../examples/) directory for more programs
- Try building an interactive app with `geno build` (see README for app mode details)
