# CSV Explorer CLI - Specification

## Project Overview
- **Name**: csvex
- **Type**: Python CLI utility
- **Core functionality**: Easy-to-use command-line tool for exploring and analyzing CSV files
- **Target users**: Data analysts, developers, anyone working with CSV files

## Functionality Specification

### Core Features
1. **Preview (head)** - Display first N rows with column headers
2. **Filter rows** - Filter rows by column values using conditions
3. **Select columns** - Display only specific columns
4. **Statistics** - Show summary statistics (count, min, max, avg) for numeric columns
5. **Unique values** - List unique values in a specified column

### CLI Interface
- Single command: `csvex <csv_file> [options]`
- Subcommands for each feature: `head`, `filter`, `select`, `stats`, `unique`

### Usage Examples
```bash
csvex data.csv                    # Show preview (default 10 rows)
csvex data.csv head -n 20         # Show first 20 rows
csvex data.csv filter age > 30    # Filter rows where age > 30
csvex data.csv select name,email  # Select specific columns
csvex data.csv stats              # Show statistics for all numeric columns
csvex data.csv unique country     # List unique values in country column
```

## Acceptance Criteria
- [ ] Tool loads and displays CSV files correctly
- [ ] All 5 features work as specified
- [ ] Error handling for invalid files and columns
- [ ] Help text available for all commands
- [ ] Clean, readable output formatting
