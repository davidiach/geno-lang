# geno-snap

A single-command API mock server written in Geno. Example clauses define mock
responses and double as self-tests via `geno test`.

## Usage

```bash
# Type check
geno check examples/apps/geno-snap

# Run self-tests (exercises all example clauses)
geno test examples/apps/geno-snap

# Start the mock server on port 8080
geno run --cap serve,env examples/apps/geno-snap

# Start on a custom port
GENO_PORT=3000 geno run --cap serve,env examples/apps/geno-snap
```

## Endpoints

| Method | Path         | Description                              |
|--------|-------------|------------------------------------------|
| GET    | /api/users  | Returns a list of mock users             |
| GET    | /api/user   | Returns a user by `id` query parameter   |
| GET    | /api/items  | Returns a list of mock items             |
| POST   | /api/echo   | Echoes back the request body             |
| OPTIONS| /api/*      | CORS preflight (all endpoints)           |

## Validation

The `/api/user` endpoint validates that:
- The `id` query parameter is present (returns 400 if missing)
- The `id` is a positive integer (returns 400 if invalid)
- The user exists (returns 404 if not found)

The `/api/echo` endpoint validates that:
- The request body is non-empty (returns 400 if empty)

## Modules

- **Models** - Domain types and JSON envelope builders
- **Validate** - Query parameter parsing and validation
- **Responses** - Mock data and response constructors with CORS headers
- **Routes** - Route handler functions
- **Main** - Route registration and server startup

## Example requests

```bash
# List all users
curl http://localhost:8080/api/users

# Get user by ID
curl http://localhost:8080/api/user?id=1

# Get items
curl http://localhost:8080/api/items

# Echo a body
curl -X POST -d '{"hello":"world"}' http://localhost:8080/api/echo

# Missing parameter → 400
curl http://localhost:8080/api/user

# Invalid parameter → 400
curl http://localhost:8080/api/user?id=abc
```
