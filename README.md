# csvex - CSV Explorer CLI

A simple CLI tool for exploring CSV files.

## Installation

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install pandas

# Run
.venv/bin/python csvex.py <file> [command]
```

Or make it executable and add to PATH:
```bash
chmod +x csvex.py
ln -s $(pwd)/csvex.py /usr/local/bin/csvex
```

## Usage

```bash
# Preview (default shows first 10 rows)
csvex data.csv
csvex data.csv head -n 20

# Filter rows
csvex data.csv filter age > 30
csvex data.csv filter city == "New York"
csvex data.csv filter name contains "John"

# Select columns
csvex data.csv select name email
csvex data.csv select name,age,salary

# Statistics
csvex data.csv stats

# Unique values
csvex data.csv unique country
```

## Commands

| Command | Description |
|---------|-------------|
| `head [-n N]` | Display first N rows (default: 10) |
| `filter <col> <op> <val>` | Filter by column value |
| `select <cols...>` | Select specific columns |
| `stats` | Show statistics for numeric columns |
| `unique <col>` | List unique values in a column |

## Filter Operators

- Comparison: `==`, `!=`, `>`, `<`, `>=`, `<=`
- String: `contains`, `startswith`, `endswith`
