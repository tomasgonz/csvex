#!/usr/bin/env python3
"""CSV Explorer - A CLI tool for exploring CSV files."""

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional

try:
    import pandas as pd
except ImportError:
    print("Error: pandas is required. Install with: pip install pandas")
    sys.exit(1)


class CSVExplorer:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        self.df = pd.read_csv(self.file_path)

    def head(self, n: int = 10) -> None:
        """Display first n rows."""
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', None)
        print(self.df.head(n).to_string(index=False))

    def filter_rows(self, column: str, operator: str, value: str) -> None:
        """Filter rows by column value."""
        if column not in self.df.columns:
            print(f"Error: Column '{column}' not found. Available: {', '.join(self.df.columns)}")
            sys.exit(1)

        col = self.df[column]
        try:
            if col.dtype in ['int64', 'float64']:
                value = float(value)
            elif col.dtype == 'bool':
                value = value.lower() in ('true', '1', 'yes')
        except ValueError:
            pass

        if operator == '==':
            mask = col == value
        elif operator == '!=':
            mask = col != value
        elif operator == '>':
            mask = col > value
        elif operator == '<':
            mask = col < value
        elif operator == '>=':
            mask = col >= value
        elif operator == '<=':
            mask = col <= value
        elif operator == 'contains':
            mask = col.astype(str).str.contains(value, case=False, na=False)
        elif operator == 'startswith':
            mask = col.astype(str).str.startswith(value, na=False)
        elif operator == 'endswith':
            mask = col.astype(str).str.endswith(value, na=False)
        else:
            print(f"Error: Unknown operator '{operator}'")
            sys.exit(1)

        result = self.df[mask]
        if result.empty:
            print("No matching rows found.")
        else:
            print(result.to_string(index=False))

    def select_columns(self, columns: list[str]) -> None:
        """Display only specified columns."""
        invalid = [c for c in columns if c not in self.df.columns]
        if invalid:
            print(f"Error: Columns not found: {', '.join(invalid)}")
            print(f"Available: {', '.join(self.df.columns)}")
            sys.exit(1)
        print(self.df[columns].to_string(index=False))

    def stats(self) -> None:
        """Show statistics for numeric columns."""
        numeric = self.df.select_dtypes(include=['number'])
        if numeric.empty:
            print("No numeric columns found.")
            return
        print(numeric.describe().to_string())

    def unique_values(self, column: str) -> None:
        """List unique values in a column."""
        if column not in self.df.columns:
            print(f"Error: Column '{column}' not found. Available: {', '.join(self.df.columns)}")
            sys.exit(1)
        unique = self.df[column].dropna().unique()
        unique = sorted(unique, key=str)
        print(f"Unique values in '{column}' ({len(unique)} total):")
        for val in unique:
            print(f"  {val}")


def cmd_head(args):
    explorer = CSVExplorer(args.file)
    explorer.head(args.rows)


def cmd_filter(args):
    explorer = CSVExplorer(args.file)
    explorer.filter_rows(args.column, args.operator, args.value)


def cmd_select(args):
    explorer = CSVExplorer(args.file)
    explorer.select_columns(args.columns)


def cmd_stats(args):
    explorer = CSVExplorer(args.file)
    explorer.stats()


def cmd_unique(args):
    explorer = CSVExplorer(args.file)
    explorer.unique_values(args.column)


def main():
    parser = argparse.ArgumentParser(
        prog='csvex',
        description='CSV Explorer - A CLI tool for exploring CSV files'
    )
    parser.add_argument('file', help='Path to CSV file', nargs='?', default=None)
    parser.add_argument('-v', '--version', action='version', version='csvex 1.0.0')

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # head command
    p_head = subparsers.add_parser('head', help='Display first N rows')
    p_head.add_argument('-n', '--rows', type=int, default=10, help='Number of rows to display (default: 10)')

    # filter command
    p_filter = subparsers.add_parser('filter', help='Filter rows by column value')
    p_filter.add_argument('column', help='Column name to filter on')
    p_filter.add_argument('operator', help='Operator: ==, !=, >, <, >=, <=, contains, startswith, endswith')
    p_filter.add_argument('value', help='Value to filter by')

    # select command
    p_select = subparsers.add_parser('select', help='Select columns to display')
    p_select.add_argument('columns', nargs='+', help='Column names (comma-separated or space-separated)')

    # stats command
    subparsers.add_parser('stats', help='Show statistics for numeric columns')

    # unique command
    p_unique = subparsers.add_parser('unique', help='List unique values in a column')
    p_unique.add_argument('column', help='Column name')

    args = parser.parse_args()

    if args.file is None:
        parser.print_help()
        sys.exit(1)

    if args.command is None:
        args.command = 'head'
        args.rows = 10

    # Dispatch to command handler
    if args.command == 'head':
        cmd_head(args)
    elif args.command == 'filter':
        cmd_filter(args)
    elif args.command == 'select':
        cmd_select(args)
    elif args.command == 'stats':
        cmd_stats(args)
    elif args.command == 'unique':
        cmd_unique(args)


if __name__ == '__main__':
    main()
