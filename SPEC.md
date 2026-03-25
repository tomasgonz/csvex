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
6. **Explorer TUI** - Browse CSV files in a terminal UI and open them interactively

### CLI Interface
- Single command: `csvex <csv_file>` launches the interactive viewer in a terminal
- Subcommands for each feature: `head`, `filter`, `select`, `stats`, `unique`
- Interactive mode: `csvex explore [path]`

### Usage Examples
```bash
csvex data.csv                    # Open the interactive table viewer
csvex data.csv head -n 20         # Show first 20 rows
csvex data.csv filter age > 30    # Filter rows where age > 30
csvex data.csv select name,email  # Select specific columns
csvex data.csv stats              # Show statistics for all numeric columns
csvex data.csv unique country     # List unique values in country column
csvex explore .                   # Browse CSV files in the current directory
```

## Acceptance Criteria
- [ ] Tool loads and displays CSV files correctly
- [ ] All 5 features work as specified
- [ ] Error handling for invalid files and columns
- [ ] Help text available for all commands
- [ ] Clean, readable output formatting
- [ ] Interactive explorer can browse and open CSV files
