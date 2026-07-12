# geno-dash

A live data dashboard in a single HTML file. Zero dependencies.

## One File, Zero Dependencies

```bash
geno build examples/apps/geno-dash -o dashboard.html
# Open dashboard.html in any browser — that's it.
```

The `-o dashboard.html` flag automatically produces a single self-contained
HTML file (the `.html` extension infers `--single-file` mode). No npm, no
bundler, no CDN links. Works offline after the initial build.

## Usage

```bash
# Type check
geno check --target browser examples/apps/geno-dash

# Run self-tests (32 example clauses on pure functions)
geno test examples/apps/geno-dash

# Build to HTML
geno build examples/apps/geno-dash -o dashboard.html
```

## Dashboard widgets

| Widget     | Description                                       |
|-----------|---------------------------------------------------|
| Stat card  | Large number with label and color-coded border    |
| Bar chart  | Horizontal bars comparing 4 metrics               |
| Sparkline  | Mini line chart showing 20-point rolling history  |
| Status dot | Color-coded circle (green/yellow/red) in header   |

## Architecture

- **Data** - Metric types, simulated data source using Result types,
  history buffer with shift-append, aggregation helpers
- **Widgets** - Canvas-based rendering for 3 widget types
- **Layout** - Grid positioning constants
- **Theme** - Color palette and typography sizing
- **Main** - init/update/render lifecycle, Result pattern matching for
  data fetching, state management

## Configuration

The refresh interval is configurable via the `refresh_interval` field in
`DashState` (default: 1.0 seconds). Network errors are displayed as a red
banner at the top of the dashboard, and the last successful values are
preserved so the dashboard remains useful after a fetch failure.

## Data simulation

Metrics are simulated using `random_int` within plausible ranges.
All data fetches return `Result[Metric, String]` and are unwrapped
via pattern matching — the same architecture works for real API data
when async HTTP is available in the browser target.
