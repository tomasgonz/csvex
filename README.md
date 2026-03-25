# csvex

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white" alt="Python 3.8+" />
  <img src="https://img.shields.io/badge/interface-terminal%20ui-0F172A" alt="Terminal UI" />
  <img src="https://img.shields.io/badge/dependency-pandas-150458?logo=pandas&logoColor=white" alt="pandas" />
  <img src="https://img.shields.io/badge/license-MIT-059669" alt="MIT License" />
</p>

<p align="center">
  <strong>Explore CSV files from the terminal without falling back to a spreadsheet.</strong>
</p>

<p align="center">
  Open any CSV into a colorful interactive table, browse project folders with live CSV counts,
  and keep plain CLI commands for shell-friendly workflows.
</p>

<p align="center">
  <img src="assets/viewer-screenshot.svg" alt="csvex interactive table viewer" width="49%" />
  <img src="assets/explorer-screenshot.svg" alt="csvex folder explorer" width="49%" />
</p>

## Why Use It

- `csvex data.csv` opens an interactive table immediately in a real terminal
- `csvex explore` gives you a folder browser with progressive scanning and live folder stats
- `c` works as a short alias for `csvex`
- plain commands like `head`, `filter`, `select`, `stats`, and `unique` still work well in scripts
- the codebase is small enough to understand and extend without much ceremony

## Quick Start

### Editable install with global commands

This is the best setup if you want `csvex` and `c` available system-wide and you want code
changes in this repo to take effect immediately.

```bash
./install-dev-global.sh
```

The script creates `.venv`, installs the package in editable mode, and links both commands into
`/usr/local/bin`.

### User install

```bash
python3 -m pip install --user .
```

This installs both `csvex` and `c` in your user environment.

## Core Flows

### Open a CSV directly

```bash
csvex data.csv
c data.csv
```

If you're in an interactive terminal, this opens the table viewer. In a non-interactive context,
it falls back to plain `head` output so it still behaves well in scripts and pipes.

### Browse a folder of CSVs

```bash
csvex explore
csvex explore data/
c explore .
```

The explorer renders immediately, then scans in the background and fills in recursive CSV totals
as it discovers more files.

### Use the non-interactive commands

```bash
csvex data.csv head -n 20
csvex data.csv filter age '>' 30
csvex data.csv filter city == "New York"
csvex data.csv select name email salary
csvex data.csv stats
csvex data.csv unique country
```

## Commands

| Command | Description |
| --- | --- |
| `head [-n N]` | Display the first `N` rows |
| `filter <col> <op> <val>` | Filter rows by value |
| `select <cols...>` | Show only selected columns |
| `stats` | Show numeric summary statistics |
| `unique <col>` | List unique values in a column |
| `explore [path]` | Open the interactive explorer |

## TUI Controls

### Explorer

- `Enter` or `l`: open the selected folder or file
- `h`, `u`, or `Backspace`: go up one folder
- `a`: toggle between folder browser mode and recursive all-files mode
- `Up/Down` or `j/k`: move selection
- `PgUp/PgDn`: page through entries
- `g` / `G`: jump to top or bottom
- `r`: rescan
- `q`: quit

### Viewer

- `Up/Down` or `j/k`: move through rows
- `Left/Right` or `h/l`: scroll horizontally
- `PgUp/PgDn`: page through rows
- `g` / `G`: jump to top or bottom
- `0` / `$`: jump to the left or right edge
- `b`: go back to the file list
- `r`: jump back to files and rescan
- `q`: quit

## Filter Operators

- comparison: `==`, `!=`, `>`, `<`, `>=`, `<=`
- string: `contains`, `startswith`, `endswith`

## Why It Feels Fast

- the explorer draws before the recursive scan finishes
- immediate folder contents are seeded first, so nearby files appear quickly
- directory counts update progressively instead of blocking startup on a full tree walk
- ignored folders like `.git`, `.venv`, `build`, `dist`, and `node_modules` stay out of the way

## Contributing

`csvex` is intentionally small and direct. Most of the behavior lives in `csvex.py`, so new work
is easy to trace.

Areas that would make strong contributions:

- better support for very large CSV files
- search within the TUI
- delimiter detection and TSV support
- richer schema inspection and column summaries
- tests for parsing edge cases and terminal behavior
- performance tuning for very deep directory trees

Basic development loop:

```bash
./install-dev-global.sh
python3 -m py_compile csvex.py
c test_data.csv
c explore .
```

## License

MIT
