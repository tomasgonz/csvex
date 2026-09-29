#!/usr/bin/env python3
"""csvex - a colorful CSV explorer for the terminal."""

from __future__ import annotations

import argparse
import csv
import curses
import io
import json
import os
import queue
import shutil
import sqlite3
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

APP_VERSION = "1.7.0"
EXPLORER_ALIASES = {"explore", "explorer", "tui"}
DEMO_ALIASES = {"demo"}
SUMMARY_ALIASES = {"summary"}
DRIFT_ALIASES = {"drift"}
COMPARE_ALIASES = {"compare"}
SQL_ALIASES = {"sql"}
IGNORED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
TEXT_SUFFIXES = {".csv", ".tsv", ".txt"}
COMPRESSED_SUFFIXES = {".gz", ".bz2", ".xz", ".zip"}
PREVIEW_ROW_LIMIT = 5000
LARGE_FILE_BYTES = 25 * 1024 * 1024
MAX_RECENT_FILES = 30
MAX_SCAN_EVENTS_PER_FRAME = 120
MAX_SCAN_SECONDS_PER_FRAME = 0.006
STATE_FILE = Path.home() / ".csvex-state.json"
APP_ROOT = Path(__file__).resolve().parent
DEMO_ROOT = APP_ROOT / "examples" / "demo"
DEFAULT_DEMO_ENTRY = Path("sales") / "sales_2026.csv"

class LazyPandas:
    def __init__(self):
        self._module = None

    def _load(self):
        if self._module is None:
            try:
                import pandas as pandas_module
            except ImportError:
                print("Error: pandas is required. Install with: pip install pandas")
                sys.exit(1)
            self._module = pandas_module
        return self._module

    def __getattr__(self, name):
        return getattr(self._load(), name)


class EmptyTableView:
    empty = True
    columns: tuple[str, ...] = ()

    def __len__(self) -> int:
        return 0


pd = LazyPandas()
EMPTY_TABLE = EmptyTableView()


BANNER_LINES = (
    "  _____    _____   __      __  ______   __   __",
    " / ____|  / ____|  \\ \\    / / |  ____|  \\ \\ / /",
    "| |      | (___     \\ \\  / /  | |__      \\ V / ",
    "| |       \\___ \\     \\ \\/ /   |  __|      > <  ",
    "| |____   ____) |     \\  /    | |____    / . \\ ",
    " \\_____| |_____/       \\/     |______|  /_/ \\_\\",
)


def supports_color(stream) -> bool:
    return hasattr(stream, "isatty") and stream.isatty() and "NO_COLOR" not in os.environ


def style_text(text: str, *codes: str, stream=sys.stdout) -> str:
    if not codes or not supports_color(stream):
        return text
    joined = ";".join(codes)
    return f"\033[{joined}m{text}\033[0m"


def print_welcome_banner(prog_name: str) -> None:
    terminal_width = shutil.get_terminal_size((88, 24)).columns
    banner_width = max(len(line) for line in BANNER_LINES)
    width = max(min(terminal_width, 88), banner_width)
    separator = "=" * width
    alternate_name = "c" if prog_name != "c" else "csvex"
    examples = [
        (f"{prog_name} data.csv", "open a file in the interactive table view"),
        (f"{prog_name} demo", "launch the built-in showcase dataset"),
        (f"{prog_name} explore .", "browse folders and tabular files with live counts"),
        (f"{prog_name} compare left.csv right.csv --key id", "compare two related files"),
        (f"{prog_name} sql data.csv \"select city, count(*) from data group by city\"", "run SQL on a file"),
        (f"{alternate_name} demo", "same demo flow, shorter alias"),
    ]
    command_width = max(len(command) for command, _ in examples) + 4

    print(style_text(separator, "1", "31"))
    for line in BANNER_LINES:
        print(style_text(line.center(width), "1", "31"))
    print(style_text(separator, "1", "31"))
    print(style_text("Fast CSV exploration for the terminal", "1", "91"))
    print(style_text("Created by Tomas Gonzalez", "2", "31"))
    print(style_text("Explore, compare, inspect, search, and export without leaving the shell.", "31"))
    print()
    print(style_text("Quick start", "1", "91"))
    for command, description in examples:
        command_text = f"  {command:<{command_width}}"
        print(f"{style_text(command_text, '1', '91')}{style_text(description, '2', '31')}")
    print()
    print(style_text(f"Run {prog_name} --help for the full command list.", "1", "91"))
    print(style_text(separator, "1", "31"))


def resolve_demo_target(entry: str | None, browse: bool) -> Path:
    if not DEMO_ROOT.exists():
        raise FileNotFoundError(f"Demo files not found: {DEMO_ROOT}")

    raw_entry = "." if browse and not entry else (entry or str(DEFAULT_DEMO_ENTRY))
    candidate = (DEMO_ROOT / raw_entry).resolve()
    demo_root = DEMO_ROOT.resolve()

    if candidate != demo_root and demo_root not in candidate.parents:
        raise ValueError("Demo entry must stay within the bundled demo directory.")
    if not candidate.exists():
        raise FileNotFoundError(f"Demo entry not found: {candidate}")
    return candidate


def build_demo_overlay_lines(prog_name: str) -> list[str]:
    demo_root = DEMO_ROOT.resolve()
    sales_2025 = demo_root / "sales" / "sales_2025.csv"
    sales_2026 = demo_root / "sales" / "sales_2026.csv"

    return [
        "csvex demo",
        "",
        "This is the built-in showcase workspace for the app.",
        "",
        "Start here:",
        "  / search matching rows",
        "  [ and ] move the active column",
        "  i inspect the active column",
        "  d open quality checks",
        "  s or S sort the table",
        "  e export the current visible view",
        "  b jump back to the demo explorer",
        "",
        "Suggested files in the demo pack:",
        "  sales/sales_2026.csv for a revenue table",
        "  sales/sales_2025.csv for compare mode",
        "  surveys/survey_responses.csv for text-heavy exploration",
        "  inventory/warehouse_status.tsv for TSV detection",
        "",
        "Try outside the TUI:",
        f"  {prog_name} compare {sales_2025} {sales_2026} --key order_id",
        f"  {prog_name} summary {demo_root}",
        f"  {prog_name} drift {demo_root / 'sales'}",
        "",
        "Press q to close this guide.",
    ]


def human_size(size_bytes: int) -> str:
    size = float(size_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def canonical_extension(path: Path) -> str:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return ""
    if suffixes[-1] == ".parquet":
        return ".parquet"
    if len(suffixes) >= 2 and suffixes[-1] in COMPRESSED_SUFFIXES and suffixes[-2] in TEXT_SUFFIXES:
        return f"{suffixes[-2]}{suffixes[-1]}"
    return suffixes[-1]


def is_supported_data_file(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False
    if suffixes[-1] == ".parquet":
        return True
    if suffixes[-1] in TEXT_SUFFIXES:
        return True
    return len(suffixes) >= 2 and suffixes[-1] in COMPRESSED_SUFFIXES and suffixes[-2] in TEXT_SUFFIXES


def is_text_tabular_path(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False
    if suffixes[-1] in TEXT_SUFFIXES:
        return True
    return len(suffixes) >= 2 and suffixes[-1] in COMPRESSED_SUFFIXES and suffixes[-2] in TEXT_SUFFIXES


def list_supported_files(root_path: Path) -> list[Path]:
    discovered: list[Path] = []
    for root, dir_names, file_names in os.walk(root_path):
        dir_names[:] = [
            dir_name
            for dir_name in sorted(dir_names)
            if dir_name not in IGNORED_DIR_NAMES and not dir_name.endswith(".egg-info")
        ]
        for file_name in sorted(file_names):
            file_path = Path(root) / file_name
            if is_supported_data_file(file_path):
                discovered.append(file_path)
    return discovered


def load_state() -> dict[str, list[str]]:
    if not STATE_FILE.exists():
        return {"favorites": [], "recent": []}
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"favorites": [], "recent": []}

    favorites = payload.get("favorites", [])
    recent = payload.get("recent", [])
    if not isinstance(favorites, list) or not isinstance(recent, list):
        return {"favorites": [], "recent": []}
    return {"favorites": [str(item) for item in favorites], "recent": [str(item) for item in recent]}


def save_state(state: dict[str, list[str]]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def sniff_delimiter(text: str, fallback: str = ",") -> str:
    sample = text[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        delimiter_scores = {",": sample.count(","), "\t": sample.count("\t"), ";": sample.count(";"), "|": sample.count("|")}
        best = max(delimiter_scores, key=delimiter_scores.get)
        return best if delimiter_scores[best] else fallback


def read_text_dataframe(source: Path | bytes, *, label: str, preview_rows: int | None = None, usecols=None):
    forced_delimiter = "\t" if label.endswith(".tsv") or ".tsv." in label else None
    encodings = ["utf-8-sig", "utf-8", "utf-16", "latin-1"]
    last_error: Exception | None = None

    if isinstance(source, bytes):
        for encoding in encodings:
            try:
                text = source.decode(encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
                continue

            delimiter = forced_delimiter or sniff_delimiter(text)
            try:
                dataframe = pd.read_csv(
                    io.StringIO(text),
                    sep=delimiter,
                    nrows=preview_rows,
                    usecols=usecols,
                    engine="python",
                )
                return dataframe, {"encoding": encoding, "delimiter": delimiter, "format": "delimited"}
            except (UnicodeDecodeError, pd.errors.ParserError, ValueError) as exc:
                last_error = exc
                continue

        raise ValueError(f"Unable to read text data from {label}: {last_error}")

    for encoding in encodings:
        delimiter = forced_delimiter
        try:
            if delimiter is None:
                dataframe = pd.read_csv(
                    source,
                    sep=None,
                    nrows=preview_rows,
                    usecols=usecols,
                    engine="python",
                    encoding=encoding,
                    compression="infer",
                )
                inferred_delimiter = "auto"
            else:
                dataframe = pd.read_csv(
                    source,
                    sep=delimiter,
                    nrows=preview_rows,
                    usecols=usecols,
                    engine="python",
                    encoding=encoding,
                    compression="infer",
                )
                inferred_delimiter = delimiter
            return dataframe, {"encoding": encoding, "delimiter": inferred_delimiter, "format": "delimited"}
        except (UnicodeDecodeError, pd.errors.ParserError, ValueError) as exc:
            last_error = exc
            continue

    raise ValueError(f"Unable to read text data from {label}: {last_error}")


def load_dataframe(target: str | Path, *, preview_rows: int | None = None, usecols=None):
    if target == "-":
        payload = sys.stdin.buffer.read()
        if not payload:
            raise ValueError("No data received on stdin.")
        dataframe, meta = read_text_dataframe(payload, label="stdin.csv", preview_rows=preview_rows, usecols=usecols)
        meta.update({"source": "stdin", "label": "stdin", "path": None, "preview_rows": preview_rows})
        return dataframe, meta

    path = Path(target).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"Expected a file but got a directory: {path}")

    if path.suffix.lower() == ".parquet":
        try:
            dataframe = pd.read_parquet(path, columns=usecols)
        except Exception as exc:
            raise ValueError(f"Unable to read parquet data from {path}: {exc}") from exc
        if preview_rows is not None:
            dataframe = dataframe.head(preview_rows)
        return dataframe, {
            "source": str(path),
            "label": str(path),
            "path": path,
            "format": "parquet",
            "encoding": "",
            "delimiter": "",
            "preview_rows": preview_rows,
        }

    dataframe, meta = read_text_dataframe(path, label=path.name, preview_rows=preview_rows, usecols=usecols)
    meta.update({"source": str(path), "label": str(path), "path": path, "preview_rows": preview_rows})
    return dataframe, meta


def load_columns_only(path: Path) -> list[str]:
    dataframe, _ = load_dataframe(path, preview_rows=0)
    return list(dataframe.columns)


def write_dataframe(dataframe, output_path: str | Path) -> Path:
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() == ".parquet":
        dataframe.to_parquet(path, index=False)
    else:
        separator = "\t" if canonical_extension(path).startswith(".tsv") else ","
        compression = "infer" if path.suffix.lower() in COMPRESSED_SUFFIXES else None
        dataframe.to_csv(path, index=False, sep=separator, compression=compression)
    return path


def print_dataframe(dataframe) -> None:
    with pd.option_context(
        "display.max_columns",
        None,
        "display.width",
        None,
        "display.expand_frame_repr",
        False,
        "display.max_colwidth",
        60,
    ):
        if dataframe.empty:
            print("(empty result)")
        else:
            print(dataframe.to_string(index=False))


def display_value(value, width: int = 120) -> str:
    if pd.isna(value):
        return "<null>"
    text = str(value)
    if len(text) <= width:
        return text
    return f"{text[:width - 3]}..."


def sparkline_from_counts(counts: list[int]) -> str:
    if not counts:
        return ""
    blocks = " .:-=+*#%@"
    ceiling = max(counts)
    if ceiling <= 0:
        return blocks[0] * len(counts)
    return "".join(blocks[min(int((count / ceiling) * (len(blocks) - 1)), len(blocks) - 1)] for count in counts)


def bar_from_fraction(fraction: float, width: int = 16, filled: str = "#", empty: str = ".") -> str:
    fraction = max(0.0, min(fraction, 1.0))
    filled_count = int(round(fraction * width))
    return f"[{filled * filled_count}{empty * (width - filled_count)}]"


def bar_from_count(count: int, ceiling: int, width: int = 14, filled: str = "#", empty: str = ".") -> str:
    if ceiling <= 0:
        return f"[{empty * width}]"
    return bar_from_fraction(count / ceiling, width=width, filled=filled, empty=empty)


def compact_interval_label(interval) -> str:
    text = str(interval)
    text = text.replace("(", "").replace("]", "").replace("[", "")
    if "," not in text:
        return text
    left, right = [part.strip() for part in text.split(",", 1)]
    return f"{left}..{right}"


def fuzzy_match_score(query: str, text: str) -> int | None:
    normalized_query = "".join(query.lower().split())
    normalized_text = text.lower()
    if not normalized_query:
        return 0
    if normalized_query in normalized_text:
        return normalized_text.index(normalized_query)

    cursor = 0
    score = 0
    for char in normalized_query:
        index = normalized_text.find(char, cursor)
        if index == -1:
            return None
        score += index - cursor
        cursor = index + 1
    return score + len(normalized_text)


def build_column_profile_lines(dataframe, column: str) -> list[str]:
    if column not in dataframe.columns:
        raise ValueError(f"Column not found: {column}")

    series = dataframe[column]
    non_null = series.dropna()
    row_count = len(series)
    null_count = int(series.isna().sum())
    unique_count = int(non_null.nunique(dropna=True))
    completeness = 0.0 if row_count == 0 else 1 - (null_count / row_count)
    cardinality = 0.0 if row_count == 0 else unique_count / row_count
    lines = [
        f"Column: {column}",
        f"dtype: {series.dtype}",
        f"rows: {row_count}",
        f"nulls: {null_count}",
        f"unique: {unique_count}",
        f"completeness: {bar_from_fraction(completeness)} {completeness:.0%}",
        f"cardinality:  {bar_from_fraction(cardinality)} {cardinality:.0%}",
    ]

    non_empty_strings = non_null.astype(str).str.strip()
    empty_string_count = int((non_empty_strings == "").sum()) if not non_null.empty else 0
    if empty_string_count:
        lines.append(f"empty strings: {empty_string_count}")

    if not non_null.empty:
        string_lengths = non_null.astype(str).str.len()
        lines.append(f"string len min/max: {int(string_lengths.min())} / {int(string_lengths.max())}")
        lines.append(f"string len avg: {string_lengths.mean():.1f}")

    numeric = pd.to_numeric(non_null, errors="coerce").dropna()
    if not numeric.empty:
        lines.extend([f"numeric min/max: {numeric.min()} / {numeric.max()}", f"numeric mean/median: {numeric.mean():.3f} / {numeric.median():.3f}"])
        bins = min(max(numeric.nunique(), 1), 10)
        histogram = pd.cut(numeric, bins=bins, include_lowest=True).value_counts(sort=False)
        lines.append(f"distribution: {sparkline_from_counts(histogram.tolist())}")
        lines.append("Histogram:")
        max_bin_count = int(histogram.max()) if not histogram.empty else 0
        for bucket, count in histogram.items():
            label = compact_interval_label(bucket)
            lines.append(f"  {label:<22} {bar_from_count(int(count), max_bin_count)} {int(count)}")

    value_counts = non_null.astype(str).value_counts().head(5)
    if not value_counts.empty:
        lines.append("")
        lines.append("Top values:")
        max_count = int(value_counts.max())
        for value, count in value_counts.items():
            lines.append(f"  {display_value(value, 32):<32} {bar_from_count(int(count), max_count)} {int(count)}")

    return lines


def build_quality_lines(dataframe) -> list[str]:
    duplicate_rows = int(dataframe.duplicated().sum())
    null_counts = dataframe.isna().sum().sort_values(ascending=False)
    mostly_null = [(column, int(count)) for column, count in null_counts.items() if count > 0][:8]
    mixed_columns: list[str] = []
    for column in dataframe.columns:
        series = dataframe[column].dropna()
        if series.empty:
            continue
        numeric_ratio = pd.to_numeric(series, errors="coerce").notna().mean()
        if 0 < numeric_ratio < 1:
            mixed_columns.append(f"{column} ({numeric_ratio:.0%} numeric)")
        if len(mixed_columns) == 5:
            break

    lines = [
        f"rows: {len(dataframe)}",
        f"columns: {len(dataframe.columns)}",
        f"duplicate rows: {duplicate_rows}",
    ]

    if mostly_null:
        lines.append("")
        lines.append("Columns with nulls:")
        for column, count in mostly_null:
            lines.append(f"  {column}: {count}")

    if mixed_columns:
        lines.append("")
        lines.append("Possible mixed-type columns:")
        for item in mixed_columns:
            lines.append(f"  {item}")

    return lines


def build_summary_lines(path: Path, files: list[Path]) -> list[str]:
    if path.is_file():
        size = path.stat().st_size
        dataframe, meta = load_dataframe(path, preview_rows=5)
        lines = [
            f"File: {path}",
            f"format: {meta.get('format', 'delimited')}",
            f"size: {human_size(size)}",
            f"columns: {len(dataframe.columns)}",
            f"preview rows loaded: {len(dataframe)}",
        ]
        if meta.get("encoding"):
            lines.append(f"encoding: {meta['encoding']}")
        if meta.get("delimiter"):
            lines.append(f"delimiter: {meta['delimiter']}")
        lines.append("")
        lines.append("Columns:")
        for column in dataframe.columns:
            lines.append(f"  {column}")
        return lines

    by_directory = Counter()
    by_extension = Counter()
    largest_files = sorted(files, key=lambda item: item.stat().st_size, reverse=True)[:5]
    newest_files = sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)[:5]

    for file_path in files:
        by_extension[canonical_extension(file_path)] += 1
        relative_parent = file_path.parent.relative_to(path)
        top_level = relative_parent.parts[0] if relative_parent.parts else "."
        by_directory[top_level] += 1

    lines = [
        f"Root: {path}",
        f"tabular files: {len(files)}",
        f"folders with files: {len(by_directory)}",
    ]

    if by_extension:
        lines.append("")
        lines.append("Formats:")
        for extension, count in by_extension.most_common():
            lines.append(f"  {extension or '<none>'}: {count}")

    if by_directory:
        lines.append("")
        lines.append("Top folders:")
        for directory, count in by_directory.most_common(8):
            lines.append(f"  {directory}: {count}")

    if largest_files:
        lines.append("")
        lines.append("Largest files:")
        for file_path in largest_files:
            lines.append(f"  {file_path.relative_to(path)} ({human_size(file_path.stat().st_size)})")

    if newest_files:
        lines.append("")
        lines.append("Most recently modified:")
        for file_path in newest_files:
            lines.append(f"  {file_path.relative_to(path)}")

    return lines


def build_schema_drift_lines(files: list[Path], root_path: Path) -> list[str]:
    if not files:
        return ["No tabular files found."]

    column_counter: Counter[str] = Counter()
    schema_counter: Counter[tuple[str, ...]] = Counter()
    failures: list[str] = []

    for file_path in files:
        try:
            columns = load_columns_only(file_path)
        except Exception as exc:
            failures.append(f"{file_path.relative_to(root_path)}: {exc}")
            continue

        schema_key = tuple(columns)
        schema_counter[schema_key] += 1
        for column in columns:
            column_counter[column] += 1

    lines = [
        f"files checked: {len(files)}",
        f"unique schemas: {len(schema_counter)}",
        f"unique columns: {len(column_counter)}",
    ]

    if schema_counter:
        lines.append("")
        lines.append("Most common schemas:")
        for index, (schema, count) in enumerate(schema_counter.most_common(5), start=1):
            preview = ", ".join(schema[:6]) if schema else "<no columns>"
            suffix = " ..." if len(schema) > 6 else ""
            lines.append(f"  {index}. {count} files | {preview}{suffix}")

    partial_columns = [(column, count) for column, count in column_counter.most_common() if count < len(files)]
    if partial_columns:
        lines.append("")
        lines.append("Columns missing from some files:")
        for column, count in partial_columns[:10]:
            lines.append(f"  {column}: {count}/{len(files)} files")

    if failures:
        lines.append("")
        lines.append("Read failures:")
        for failure in failures[:8]:
            lines.append(f"  {failure}")

    return lines


def compare_dataframes(left, right, key: str | None = None) -> list[str]:
    lines = [
        f"left rows/cols: {len(left)} / {len(left.columns)}",
        f"right rows/cols: {len(right)} / {len(right.columns)}",
    ]

    left_only_columns = [column for column in left.columns if column not in right.columns]
    right_only_columns = [column for column in right.columns if column not in left.columns]
    common_columns = [column for column in left.columns if column in right.columns]

    if left_only_columns:
        lines.append(f"left-only columns: {', '.join(left_only_columns[:12])}")
    if right_only_columns:
        lines.append(f"right-only columns: {', '.join(right_only_columns[:12])}")

    if key:
        if key not in left.columns or key not in right.columns:
            raise ValueError(f"Key '{key}' must exist in both files.")
        compare_columns = [column for column in common_columns if column != key]
        left_indexed = left[[key] + compare_columns].drop_duplicates(subset=[key]).set_index(key)
        right_indexed = right[[key] + compare_columns].drop_duplicates(subset=[key]).set_index(key)

        added_keys = list(right_indexed.index.difference(left_indexed.index))
        removed_keys = list(left_indexed.index.difference(right_indexed.index))
        changed_keys: list[tuple[str, list[str]]] = []

        for item_key in left_indexed.index.intersection(right_indexed.index):
            left_row = left_indexed.loc[item_key]
            right_row = right_indexed.loc[item_key]
            changed_columns: list[str] = []
            for column in compare_columns:
                left_value = left_row[column]
                right_value = right_row[column]
                if pd.isna(left_value) and pd.isna(right_value):
                    continue
                if str(left_value) != str(right_value):
                    changed_columns.append(column)
            if changed_columns:
                changed_keys.append((str(item_key), changed_columns))

        lines.extend(
            [
                f"keys added: {len(added_keys)}",
                f"keys removed: {len(removed_keys)}",
                f"keys changed: {len(changed_keys)}",
            ]
        )

        if added_keys:
            lines.append("")
            lines.append("Added keys:")
            for item in added_keys[:8]:
                lines.append(f"  {item}")

        if removed_keys:
            lines.append("")
            lines.append("Removed keys:")
            for item in removed_keys[:8]:
                lines.append(f"  {item}")

        if changed_keys:
            lines.append("")
            lines.append("Changed keys:")
            for item_key, columns in changed_keys[:8]:
                lines.append(f"  {item_key}: {', '.join(columns[:8])}")

        return lines

    if not common_columns:
        lines.append("No shared columns to compare.")
        return lines

    left_unique = left[common_columns].drop_duplicates()
    right_unique = right[common_columns].drop_duplicates()
    added_rows = int(right_unique.merge(left_unique, how="left", indicator=True)["_merge"].eq("left_only").sum())
    removed_rows = int(left_unique.merge(right_unique, how="left", indicator=True)["_merge"].eq("left_only").sum())
    shared_rows = int(left_unique.merge(right_unique).shape[0])

    lines.extend(
        [
            f"shared columns: {len(common_columns)}",
            f"shared unique rows: {shared_rows}",
            f"rows only in left: {removed_rows}",
            f"rows only in right: {added_rows}",
        ]
    )
    return lines


def run_sql_query(dataframe, query: str):
    connection = sqlite3.connect(":memory:")
    try:
        dataframe.to_sql("data", connection, index=False, if_exists="replace")
        result = pd.read_sql_query(query, connection)
    finally:
        connection.close()
    return result


class CSVExplorer:
    def __init__(self, file_path: str, *, preview_rows: int | None = None):
        self.file_path = file_path
        self.preview_rows = preview_rows
        self.df, self.meta = load_dataframe(file_path, preview_rows=preview_rows)

    def head(self, n: int = 10) -> None:
        print_dataframe(self.df.head(n))

    def _validate_column(self, column: str) -> None:
        if column not in self.df.columns:
            raise ValueError(f"Column '{column}' not found. Available: {', '.join(self.df.columns)}")

    def filter_rows(self, column: str, operator: str, value: str) -> None:
        self._validate_column(column)
        series = self.df[column]
        coerced_value = value

        try:
            if pd.api.types.is_numeric_dtype(series):
                coerced_value = float(value)
            elif pd.api.types.is_bool_dtype(series):
                coerced_value = value.lower() in {"true", "1", "yes"}
        except ValueError:
            coerced_value = value

        if operator == "==":
            mask = series == coerced_value
        elif operator == "!=":
            mask = series != coerced_value
        elif operator == ">":
            mask = series > coerced_value
        elif operator == "<":
            mask = series < coerced_value
        elif operator == ">=":
            mask = series >= coerced_value
        elif operator == "<=":
            mask = series <= coerced_value
        elif operator == "contains":
            mask = series.astype(str).str.contains(value, case=False, na=False)
        elif operator == "startswith":
            mask = series.astype(str).str.startswith(value, na=False)
        elif operator == "endswith":
            mask = series.astype(str).str.endswith(value, na=False)
        else:
            raise ValueError(f"Unknown operator '{operator}'")

        print_dataframe(self.df[mask])

    def select_columns(self, columns: list[str]) -> None:
        missing = [column for column in columns if column not in self.df.columns]
        if missing:
            raise ValueError(f"Columns not found: {', '.join(missing)}")
        print_dataframe(self.df[columns])

    def stats(self) -> None:
        numeric = self.df.select_dtypes(include=["number"])
        if numeric.empty:
            print("No numeric columns found.")
            return
        with pd.option_context("display.width", None, "display.max_columns", None):
            print(numeric.describe().to_string())

    def unique_values(self, column: str) -> None:
        self._validate_column(column)
        unique = self.df[column].dropna().unique()
        unique = sorted(unique, key=str)
        print(f"Unique values in '{column}' ({len(unique)} total):")
        for value in unique:
            print(f"  {value}")

    def inspect_column(self, column: str) -> None:
        for line in build_column_profile_lines(self.df, column):
            print(line)

    def quality(self) -> None:
        for line in build_quality_lines(self.df):
            print(line)

    def export(self, output_path: str) -> None:
        path = write_dataframe(self.df, output_path)
        print(f"Exported {len(self.df)} rows to {path}")

    def sql(self, query: str) -> None:
        result = run_sql_query(self.df, query)
        print_dataframe(result)


@dataclass(frozen=True)
class BrowserEntry:
    kind: str
    path: Path
    label: str
    detail: str = ""


class CSVExplorerTUI:
    def __init__(
        self,
        target_path: str | Path,
        *,
        root_path: str | Path | None = None,
        startup_overlay_title: str = "",
        startup_overlay_lines: list[str] | None = None,
    ):
        self.target_path = Path(target_path).expanduser()
        if not self.target_path.exists():
            raise FileNotFoundError(f"Path not found: {self.target_path}")
        if self.target_path.is_file() and not is_supported_data_file(self.target_path):
            raise ValueError(f"Unsupported file type: {self.target_path}")

        resolved_target = self.target_path.resolve()
        if root_path is None:
            self.root_path = (resolved_target.parent if resolved_target.is_file() else resolved_target).resolve()
        else:
            self.root_path = Path(root_path).expanduser().resolve()

        if not self.root_path.exists() or not self.root_path.is_dir():
            raise FileNotFoundError(f"Root path not found: {self.root_path}")
        if resolved_target != self.root_path and self.root_path not in resolved_target.parents:
            raise ValueError(f"Target {resolved_target} is outside the selected root {self.root_path}")

        self.current_dir = self.root_path
        self.initial_file = resolved_target if resolved_target.is_file() else None

        self.state = load_state()
        self.favorites: set[str] = set(self.state.get("favorites", []))
        self.recent_files: list[str] = list(self.state.get("recent", []))

        self.all_files: list[Path] = []
        self.all_file_set: set[Path] = set()
        self.direct_file_counts: dict[Path, int] = {}
        self.subtree_file_counts: dict[Path, int] = {}
        self.entries: list[BrowserEntry] = []
        self.selected_index = 0
        self.file_scroll = 0
        self.list_mode = "browse"
        self.mode = "files"
        self.status_message = ""
        self.explorer_search_term = ""

        self.active_file: Path | None = None
        self.active_explorer: CSVExplorer | None = None
        self.view_df = EMPTY_TABLE
        self.visible_columns: list[str] = []
        self.pinned_columns: list[str] = []
        self.selected_column_name: str | None = None
        self.selected_row = 0
        self.row_offset = 0
        self.col_offset = 0
        self.viewer_page_rows = 1
        self.search_term = ""
        self.search_matches: list[int] = []
        self.search_match_index = -1
        self.filter_column: str | None = None
        self.filter_term = ""
        self.sort_column: str | None = None
        self.sort_reverse = False
        self.watch_mode = False
        self.last_mtime: float | None = None
        self.preview_rows: int | None = None
        self.table_cache_key: tuple[object, ...] | None = None
        self.table_cache_lines: list[str] = []

        self.overlay_title = startup_overlay_title
        self.overlay_lines: list[str] = list(startup_overlay_lines or [])
        self.overlay_scroll = 0

        self.title_attr = curses.A_BOLD
        self.footer_attr = curses.A_DIM
        self.status_attr = curses.A_BOLD
        self.selected_attr = curses.A_REVERSE | curses.A_BOLD
        self.active_attr = curses.A_BOLD
        self.directory_attr = curses.A_BOLD
        self.up_attr = curses.A_BOLD
        self.table_header_attr = curses.A_BOLD
        self.table_alt_attr = curses.A_DIM
        self.favorite_attr = curses.A_BOLD

        self.scan_queue: queue.Queue[tuple[int, str, object]] = queue.Queue()
        self.scan_generation = 0
        self.scan_in_progress = False
        self.scan_complete = False
        self.scan_error = ""
        self.scanned_directories = 0

        self._refresh_entries()
        if self.initial_file is not None:
            self._open_file(self.initial_file)

    def run(self) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("Error: explorer mode requires an interactive terminal.", file=sys.stderr)
            sys.exit(1)
        curses.wrapper(self._run)

    def _run(self, stdscr) -> None:
        stdscr.keypad(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass

        self._init_theme()
        stdscr.timeout(100)
        self.start_scan(initial_scan=True)

        while True:
            self._drain_scan_queue()
            self._poll_watch_reload()
            stdscr.erase()
            if self.mode == "files":
                self._render_file_list(stdscr)
            else:
                self._render_viewer(stdscr)
            if self.overlay_title:
                self._render_overlay(stdscr)
            stdscr.refresh()

            key = stdscr.getch()
            if key == -1:
                continue

            if self.overlay_title:
                if not self._handle_overlay_key(key, stdscr):
                    break
                continue

            if self.mode == "files":
                if not self._handle_file_list_key(key, stdscr):
                    break
            else:
                if not self._handle_viewer_key(key, stdscr):
                    break

    def start_scan(self, initial_scan: bool = False) -> None:
        previous_entry = self.entries[self.selected_index] if self.entries else None
        self.scan_generation += 1
        self.scan_in_progress = True
        self.scan_complete = False
        self.scan_error = ""
        self.scanned_directories = 0
        self.all_files = []
        self.all_file_set = set()
        self.direct_file_counts = {}
        self.subtree_file_counts = {}

        self._seed_directory_files(self.root_path)
        if self.current_dir != self.root_path:
            self._seed_directory_files(self.current_dir)
        if self.active_file is not None:
            self._apply_discovered_file(self.active_file)
            self.all_files.sort(key=lambda path: self._display_path(path).lower())

        self._ensure_current_dir_is_valid()
        self._refresh_entries(previous_entry)

        if not initial_scan:
            self.status_message = f"Scanning {self._dir_label(self.current_dir)} for tabular files..."

        threading.Thread(target=self._scan_worker, args=(self.scan_generation,), daemon=True).start()

    def _scan_worker(self, generation: int) -> None:
        def onerror(error: OSError) -> None:
            self.scan_queue.put((generation, "warning", str(error)))

        try:
            for root, dir_names, file_names in os.walk(self.root_path, onerror=onerror):
                dir_names[:] = [
                    dir_name
                    for dir_name in sorted(dir_names)
                    if dir_name not in IGNORED_DIR_NAMES and not dir_name.endswith(".egg-info")
                ]
                self.scan_queue.put((generation, "dir", Path(root)))
                for file_name in sorted(file_names):
                    file_path = Path(root) / file_name
                    if is_supported_data_file(file_path):
                        self.scan_queue.put((generation, "file", file_path))
        except Exception as exc:
            self.scan_queue.put((generation, "error", str(exc)))
            return

        self.scan_queue.put((generation, "done", None))

    def _drain_scan_queue(self) -> None:
        previous_entry = self.entries[self.selected_index] if self.entries else None
        files_added = False
        refresh_entries = False
        processed = 0
        started_at = time.perf_counter()

        while processed < MAX_SCAN_EVENTS_PER_FRAME and (time.perf_counter() - started_at) < MAX_SCAN_SECONDS_PER_FRAME:
            try:
                generation, kind, payload = self.scan_queue.get_nowait()
            except queue.Empty:
                break

            processed += 1
            if generation != self.scan_generation:
                continue

            if kind == "file":
                if self._apply_discovered_file(payload):
                    files_added = True
                    refresh_entries = True
            elif kind == "dir":
                self.scanned_directories += 1
            elif kind == "warning":
                self.status_message = str(payload)
            elif kind == "error":
                self.scan_in_progress = False
                self.scan_complete = False
                self.scan_error = str(payload)
                refresh_entries = True
            elif kind == "done":
                self.scan_in_progress = False
                self.scan_complete = True
                refresh_entries = True

        if files_added:
            self.all_files.sort(key=lambda path: self._display_path(path).lower())

        if refresh_entries:
            self._ensure_current_dir_is_valid()
            self._refresh_entries(previous_entry)

    def _apply_discovered_file(self, file_path: object) -> bool:
        if not isinstance(file_path, Path):
            return False
        file_path = file_path.resolve()
        if file_path in self.all_file_set:
            return False

        self.all_file_set.add(file_path)
        self.all_files.append(file_path)
        self.direct_file_counts[file_path.parent] = self.direct_file_counts.get(file_path.parent, 0) + 1

        current = file_path.parent
        while True:
            self.subtree_file_counts[current] = self.subtree_file_counts.get(current, 0) + 1
            if current == self.root_path:
                break
            current = current.parent

        return True

    def _seed_directory_files(self, directory: Path) -> None:
        if not directory.exists() or not directory.is_dir():
            return
        if not self._is_within_root(directory):
            return

        changed = False
        try:
            for child in directory.iterdir():
                if child.is_file() and is_supported_data_file(child):
                    changed = self._apply_discovered_file(child.resolve()) or changed
        except PermissionError as exc:
            self.status_message = str(exc)
            return

        if changed:
            self.all_files.sort(key=lambda path: self._display_path(path).lower())

    def _ensure_current_dir_is_valid(self) -> None:
        current = self.current_dir
        while current != self.root_path and (not current.exists() or not current.is_dir()):
            current = current.parent
        self.current_dir = current if self._is_within_root(current) else self.root_path

    def _refresh_entries(self, previous_entry: BrowserEntry | None = None) -> None:
        if self.list_mode == "all":
            entries = self._build_recursive_entries()
        elif self.list_mode == "favorites":
            entries = self._build_special_entries(self.favorites, "favorites")
        elif self.list_mode == "recent":
            entries = self._build_special_entries(self.recent_files, "recent")
        else:
            entries = self._build_browse_entries()

        self.entries = self._filter_entries_for_search(entries)

        target_key = (previous_entry.kind, previous_entry.path) if previous_entry is not None else None
        self.selected_index = 0
        if target_key is not None:
            for index, entry in enumerate(self.entries):
                if (entry.kind, entry.path) == target_key:
                    self.selected_index = index
                    break
        self.file_scroll = min(self.file_scroll, self.selected_index)

    def _build_browse_entries(self) -> list[BrowserEntry]:
        entries: list[BrowserEntry] = []
        if self.current_dir != self.root_path:
            entries.append(BrowserEntry("up", self.current_dir.parent, "../", "parent"))

        try:
            children = sorted(self.current_dir.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
        except PermissionError as exc:
            self.status_message = str(exc)
            return entries

        for child in children:
            child = child.resolve()
            if child.is_dir():
                if self._is_ignored_dir(child):
                    continue
                label = f"{child.name}/"
                if str(child) in self.favorites:
                    label = f"* {label}"
                entries.append(BrowserEntry("dir", child, label, self._directory_detail(child)))
            elif is_supported_data_file(child):
                label = child.name
                if str(child) in self.favorites:
                    label = f"* {label}"
                entries.append(BrowserEntry("file", child, label, canonical_extension(child)))

        return entries

    def _build_recursive_entries(self) -> list[BrowserEntry]:
        entries: list[BrowserEntry] = []
        if self.current_dir != self.root_path:
            entries.append(BrowserEntry("up", self.current_dir.parent, "../", "parent"))

        for file_path in self.all_files:
            if file_path.parent == self.current_dir or self.current_dir in file_path.parents:
                label = str(file_path.relative_to(self.current_dir))
                if str(file_path) in self.favorites:
                    label = f"* {label}"
                entries.append(BrowserEntry("file", file_path, label, self._dir_label(file_path.parent)))
        return entries

    def _build_special_entries(self, items: set[str] | list[str], mode_name: str) -> list[BrowserEntry]:
        entries: list[BrowserEntry] = []
        if mode_name in {"favorites", "recent"} and self.current_dir != self.root_path:
            entries.append(BrowserEntry("up", self.current_dir.parent, "../", "parent"))

        for raw_path in items:
            path = Path(raw_path).expanduser()
            if not path.exists() or not self._is_within_root(path.resolve() if path.exists() else path):
                continue
            path = path.resolve()
            if path.is_dir():
                entries.append(BrowserEntry("dir", path, f"{path.relative_to(self.root_path)}/", "favorite"))
            elif is_supported_data_file(path):
                entries.append(BrowserEntry("file", path, str(path.relative_to(self.root_path)), canonical_extension(path)))
        return entries

    def _filter_entries_for_search(self, entries: list[BrowserEntry]) -> list[BrowserEntry]:
        if not self.explorer_search_term:
            return entries

        up_entries = [entry for entry in entries if entry.kind == "up"]
        scored: list[tuple[int, BrowserEntry]] = []
        for entry in entries:
            if entry.kind == "up":
                continue
            haystack = f"{entry.label} {entry.detail} {self._display_path(entry.path) if self._is_within_root(entry.path) else entry.path}"
            score = fuzzy_match_score(self.explorer_search_term, haystack)
            if score is not None:
                scored.append((score, entry))

        scored.sort(key=lambda item: (item[0], item[1].kind != "dir", item[1].label.lower()))
        return up_entries + [entry for _, entry in scored]

    def _poll_watch_reload(self) -> None:
        if not self.watch_mode or self.active_file is None or self.mode != "viewer":
            return
        if not self.active_file.exists():
            self.watch_mode = False
            self.status_message = "Watch disabled because the file disappeared."
            return
        current_mtime = self.active_file.stat().st_mtime
        if self.last_mtime is not None and current_mtime > self.last_mtime:
            self._reload_active_file(preserve_view=True)
            self.status_message = f"Reloaded {self._display_path(self.active_file)} after it changed."
        self.last_mtime = current_mtime

    def _touch_recent(self, file_path: Path) -> None:
        raw = str(file_path)
        self.recent_files = [item for item in self.recent_files if item != raw]
        self.recent_files.insert(0, raw)
        self.recent_files = self.recent_files[:MAX_RECENT_FILES]
        self._save_state()

    def _toggle_favorite(self, path: Path) -> None:
        raw = str(path)
        if raw in self.favorites:
            self.favorites.remove(raw)
            self.status_message = f"Removed favorite: {self._display_path(path) if self._is_within_root(path) else path}"
        else:
            self.favorites.add(raw)
            self.status_message = f"Added favorite: {self._display_path(path) if self._is_within_root(path) else path}"
        self._save_state()
        self._refresh_entries()

    def _save_state(self) -> None:
        save_state({"favorites": sorted(self.favorites), "recent": self.recent_files})

    def _open_file(self, file_path: Path) -> None:
        preserve_view = file_path == self.active_file
        previous_view_state = self._capture_view_state() if preserve_view else None

        preview_rows = None
        if is_text_tabular_path(file_path) and file_path.exists() and file_path.stat().st_size >= LARGE_FILE_BYTES:
            preview_rows = PREVIEW_ROW_LIMIT

        try:
            explorer = CSVExplorer(str(file_path), preview_rows=preview_rows)
        except Exception as exc:
            self.status_message = f"Error opening {self._display_path(file_path)}: {exc}"
            return

        self.active_explorer = explorer
        self.active_file = file_path
        self.preview_rows = preview_rows
        self.mode = "viewer"
        self.watch_mode = False
        self._touch_recent(file_path)
        self.last_mtime = file_path.stat().st_mtime if file_path.exists() else None
        self._restore_view_state(previous_view_state)

        if preview_rows is not None:
            self.status_message = f"Large file preview mode: showing the first {preview_rows} rows."
        else:
            self.status_message = ""

    def _reload_active_file(self, preserve_view: bool = True) -> None:
        if self.active_file is None:
            return
        previous_state = self._capture_view_state() if preserve_view else None
        try:
            explorer = CSVExplorer(str(self.active_file), preview_rows=self.preview_rows)
        except Exception as exc:
            self.status_message = f"Reload failed: {exc}"
            return
        self.active_explorer = explorer
        self._restore_view_state(previous_state)
        self.last_mtime = self.active_file.stat().st_mtime if self.active_file.exists() else None

    def _capture_view_state(self) -> dict[str, object] | None:
        if self.active_explorer is None:
            return None
        return {
            "visible_columns": list(self.visible_columns),
            "pinned_columns": list(self.pinned_columns),
            "selected_column_name": self.selected_column_name,
            "selected_row": self.selected_row,
            "row_offset": self.row_offset,
            "col_offset": self.col_offset,
            "search_term": self.search_term,
            "filter_column": self.filter_column,
            "filter_term": self.filter_term,
            "sort_column": self.sort_column,
            "sort_reverse": self.sort_reverse,
        }

    def _restore_view_state(self, state: dict[str, object] | None) -> None:
        if self.active_explorer is None:
            return

        columns = list(self.active_explorer.df.columns)
        if state is None:
            self.visible_columns = columns
            self.pinned_columns = []
            self.selected_column_name = columns[0] if columns else None
            self.search_term = ""
            self.filter_column = None
            self.filter_term = ""
            self.sort_column = None
            self.sort_reverse = False
            self.selected_row = 0
            self.row_offset = 0
            self.col_offset = 0
        else:
            self.visible_columns = [column for column in state["visible_columns"] if column in columns]
            if not self.visible_columns:
                self.visible_columns = columns
            self.pinned_columns = [column for column in state["pinned_columns"] if column in self.visible_columns]
            self.selected_column_name = state["selected_column_name"] if state["selected_column_name"] in self.visible_columns else (self.visible_columns[0] if self.visible_columns else None)
            self.search_term = str(state["search_term"])
            self.filter_column = state["filter_column"] if state["filter_column"] in columns else None
            self.filter_term = str(state["filter_term"]) if self.filter_column else ""
            self.sort_column = state["sort_column"] if state["sort_column"] in columns else None
            self.sort_reverse = bool(state["sort_reverse"])
            self.selected_row = int(state["selected_row"])
            self.row_offset = int(state["row_offset"])
            self.col_offset = int(state["col_offset"])

        self._rebuild_view()

    def _current_display_columns(self) -> list[str]:
        columns = [column for column in self.visible_columns if column in self.pinned_columns]
        columns.extend(column for column in self.visible_columns if column not in columns)
        return columns

    def _current_column_name(self) -> str | None:
        display_columns = self._current_display_columns()
        if not display_columns:
            return None
        if self.selected_column_name in display_columns:
            return self.selected_column_name
        self.selected_column_name = display_columns[0]
        return self.selected_column_name

    def _move_selected_column(self, direction: int) -> None:
        display_columns = self._current_display_columns()
        if not display_columns:
            return
        current = self._current_column_name()
        index = display_columns.index(current) if current in display_columns else 0
        self.selected_column_name = display_columns[(index + direction) % len(display_columns)]
        self.status_message = f"Current column: {self.selected_column_name}"

    def _invalidate_table_cache(self) -> None:
        self.table_cache_key = None
        self.table_cache_lines = []

    def _rebuild_view(self) -> None:
        if self.active_explorer is None:
            self.view_df = EMPTY_TABLE
            self.search_matches = []
            self._invalidate_table_cache()
            return

        dataframe = self.active_explorer.df
        if self.filter_column and self.filter_term and self.filter_column in dataframe.columns:
            mask = dataframe[self.filter_column].astype(str).str.contains(self.filter_term, case=False, na=False)
            dataframe = dataframe[mask]

        if self.sort_column and self.sort_column in dataframe.columns:
            dataframe = dataframe.sort_values(by=self.sort_column, ascending=not self.sort_reverse, kind="mergesort", na_position="last")

        self.view_df = dataframe
        self._invalidate_table_cache()
        self.visible_columns = [column for column in self.visible_columns if column in dataframe.columns]
        if not self.visible_columns:
            self.visible_columns = list(dataframe.columns)
        self.pinned_columns = [column for column in self.pinned_columns if column in self.visible_columns]

        display_columns = self._current_display_columns()
        if display_columns and self.selected_column_name not in display_columns:
            self.selected_column_name = display_columns[0]

        if self.view_df.empty:
            self.selected_row = 0
            self.row_offset = 0
            self.search_matches = []
            self.search_match_index = -1
            return

        self.selected_row = min(max(self.selected_row, 0), len(self.view_df) - 1)
        self._refresh_search_matches()

    def _refresh_search_matches(self) -> None:
        self.search_matches = []
        self.search_match_index = -1
        if self.view_df.empty or not self.search_term:
            return

        display_columns = self._current_display_columns() or list(self.view_df.columns)
        mask = self.view_df[display_columns].astype(str).apply(
            lambda column: column.str.contains(self.search_term, case=False, na=False)
        ).any(axis=1)
        self.search_matches = [index for index, matched in enumerate(mask.tolist()) if matched]
        if self.search_matches:
            current = self.selected_row if self.selected_row in self.search_matches else self.search_matches[0]
            self.selected_row = current
            self.search_match_index = self.search_matches.index(current)

    def _jump_search_match(self, direction: int) -> None:
        if not self.search_matches:
            self.status_message = "No search matches."
            return

        if self.search_match_index == -1:
            self.search_match_index = 0
        else:
            self.search_match_index = (self.search_match_index + direction) % len(self.search_matches)
        self.selected_row = self.search_matches[self.search_match_index]
        self._ensure_selected_row_visible()
        self.status_message = f"Search {self.search_match_index + 1}/{len(self.search_matches)} for '{self.search_term}'."

    def _ensure_selected_row_visible(self) -> None:
        if self.active_explorer is None:
            return
        page_rows = max(self.viewer_page_rows, 1)
        if self.selected_row < self.row_offset:
            self.row_offset = self.selected_row
        elif self.selected_row >= self.row_offset + page_rows:
            self.row_offset = self.selected_row - page_rows + 1

    def _prompt(self, stdscr, label: str) -> str | None:
        height, width = stdscr.getmaxyx()
        prompt = f" {label}: "
        self._draw_line(stdscr, height - 1, prompt, self.status_attr)
        stdscr.refresh()

        curses.echo()
        try:
            curses.curs_set(1)
        except curses.error:
            pass

        try:
            text = stdscr.getstr(height - 1, min(len(prompt), max(width - 2, 1)), max(width - len(prompt) - 1, 1))
        except curses.error:
            text = b""
        finally:
            curses.noecho()
            try:
                curses.curs_set(0)
            except curses.error:
                pass

        value = text.decode(errors="ignore").strip()
        return value or None

    def _open_overlay(self, title: str, lines: list[str]) -> None:
        self.overlay_title = title
        self.overlay_lines = lines or ["(empty)"]
        self.overlay_scroll = 0

    def _clear_overlay(self) -> None:
        self.overlay_title = ""
        self.overlay_lines = []
        self.overlay_scroll = 0

    def _handle_overlay_key(self, key: int, stdscr) -> bool:
        max_scroll = max(len(self.overlay_lines) - max(stdscr.getmaxyx()[0] - 8, 1), 0)
        if key in (ord("q"), 27, 10, 13, ord(" ")):
            self._clear_overlay()
            return True
        if key in (curses.KEY_UP, ord("k")):
            self.overlay_scroll = max(self.overlay_scroll - 1, 0)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.overlay_scroll = min(self.overlay_scroll + 1, max_scroll)
        elif key == curses.KEY_PPAGE:
            self.overlay_scroll = max(self.overlay_scroll - 10, 0)
        elif key == curses.KEY_NPAGE:
            self.overlay_scroll = min(self.overlay_scroll + 10, max_scroll)
        elif key in (curses.KEY_HOME, ord("g")):
            self.overlay_scroll = 0
        elif key in (curses.KEY_END, ord("G")):
            self.overlay_scroll = max_scroll
        return True

    def _handle_file_list_key(self, key: int, stdscr) -> bool:
        visible_rows = max(stdscr.getmaxyx()[0] - 2, 1)
        self.status_message = ""

        if key in (ord("q"), 27):
            return False
        if key in (ord("?"),):
            self._open_overlay("Explorer Help", self._explorer_help_lines())
        elif key == ord("/"):
            query = self._prompt(stdscr, "Find files and folders")
            if query:
                previous_entry = self.entries[self.selected_index] if self.entries else None
                self.explorer_search_term = query
                self._refresh_entries(previous_entry)
                self.status_message = f"Explorer filter: {query}"
                if not self.entries:
                    self.status_message = f"No explorer matches for '{query}'."
            else:
                self.status_message = "Explorer search cancelled."
        elif key in (curses.KEY_UP, ord("k")) and self.entries:
            self.selected_index = max(self.selected_index - 1, 0)
        elif key in (curses.KEY_DOWN, ord("j")) and self.entries:
            self.selected_index = min(self.selected_index + 1, len(self.entries) - 1)
        elif key == curses.KEY_PPAGE and self.entries:
            self.selected_index = max(self.selected_index - visible_rows, 0)
        elif key == curses.KEY_NPAGE and self.entries:
            self.selected_index = min(self.selected_index + visible_rows, len(self.entries) - 1)
        elif key in (curses.KEY_HOME, ord("g")) and self.entries:
            self.selected_index = 0
        elif key in (curses.KEY_END, ord("G")) and self.entries:
            self.selected_index = len(self.entries) - 1
        elif key in (10, 13, curses.KEY_ENTER, ord("o"), curses.KEY_RIGHT, ord("l")):
            self._activate_selected_entry()
        elif key in (curses.KEY_LEFT, ord("h"), ord("u"), curses.KEY_BACKSPACE, 127):
            self._go_up_directory()
        elif key == ord("a"):
            self.list_mode = "all" if self.list_mode == "browse" else "browse"
            self.selected_index = 0
            self.file_scroll = 0
            self._refresh_entries()
            self.status_message = f"Switched to {self.list_mode} mode."
        elif key == ord("F"):
            self.list_mode = "browse" if self.list_mode == "favorites" else "favorites"
            self.selected_index = 0
            self.file_scroll = 0
            self._refresh_entries()
            self.status_message = f"Switched to {self.list_mode} mode."
        elif key == ord("R"):
            self.list_mode = "browse" if self.list_mode == "recent" else "recent"
            self.selected_index = 0
            self.file_scroll = 0
            self._refresh_entries()
            self.status_message = f"Switched to {self.list_mode} mode."
        elif key == ord("*") and self.entries:
            self._toggle_favorite(self.entries[self.selected_index].path)
        elif key == ord("m"):
            target = self.current_dir if self.list_mode == "browse" else self.root_path
            files = [path for path in self.all_files if path.parent == target or target in path.parents] if target.is_dir() else [target]
            self._open_overlay("Folder Summary", build_summary_lines(target, files))
        elif key == ord("D"):
            target = self.current_dir if self.list_mode == "browse" else self.root_path
            files = [path for path in self.all_files if path.parent == target or target in path.parents]
            self._open_overlay("Schema Drift", build_schema_drift_lines(files, target))
        elif key == ord("C"):
            if self.explorer_search_term:
                previous_entry = self.entries[self.selected_index] if self.entries else None
                self.explorer_search_term = ""
                self._refresh_entries(previous_entry)
                self.status_message = "Cleared explorer filter."
            else:
                self.status_message = "Explorer filter is already clear."
        elif key == ord("r"):
            self.start_scan()
        elif key == curses.KEY_RESIZE:
            pass

        self._ensure_selected_visible(visible_rows)
        return True

    def _handle_viewer_key(self, key: int, stdscr) -> bool:
        self.status_message = ""
        if self.active_explorer is None:
            self.mode = "files"
            return True

        page_rows = max(stdscr.getmaxyx()[0] - 3, 1)
        max_row = max(len(self.view_df) - 1, 0)

        if key in (ord("q"), 27):
            return False
        if key == ord("b"):
            self.mode = "files"
            return True
        if key in (ord("?"),):
            self._open_overlay("Viewer Help", self._viewer_help_lines())
        elif key in (curses.KEY_UP, ord("k")) and not self.view_df.empty:
            self.selected_row = max(self.selected_row - 1, 0)
        elif key in (curses.KEY_DOWN, ord("j")) and not self.view_df.empty:
            self.selected_row = min(self.selected_row + 1, max_row)
        elif key == curses.KEY_PPAGE and not self.view_df.empty:
            self.selected_row = max(self.selected_row - page_rows, 0)
        elif key == curses.KEY_NPAGE and not self.view_df.empty:
            self.selected_row = min(self.selected_row + page_rows, max_row)
        elif key in (curses.KEY_HOME, ord("g")) and not self.view_df.empty:
            self.selected_row = 0
        elif key in (curses.KEY_END, ord("G")) and not self.view_df.empty:
            self.selected_row = max_row
        elif key in (curses.KEY_LEFT, ord("h")):
            self.col_offset = max(self.col_offset - 4, 0)
        elif key in (curses.KEY_RIGHT, ord("l")):
            self.col_offset += 4
        elif key == ord("0"):
            self.col_offset = 0
        elif key == ord("$"):
            self.col_offset += max(stdscr.getmaxyx()[1] - 1, 20)
        elif key == ord("["):
            self._move_selected_column(-1)
        elif key == ord("]"):
            self._move_selected_column(1)
        elif key in (10, 13, curses.KEY_ENTER):
            self._open_overlay("Row Detail", self._selected_row_lines())
        elif key == ord("i"):
            current_column = self._current_column_name()
            if current_column is None:
                self.status_message = "No column selected."
            else:
                self._open_overlay("Column Inspector", build_column_profile_lines(self.view_df, current_column))
        elif key == ord("d"):
            self._open_overlay("Data Quality", build_quality_lines(self.active_explorer.df))
        elif key == ord("/"):
            query = self._prompt(stdscr, "Search rows")
            if query:
                self.search_term = query
                self._rebuild_view()
                self._jump_search_match(0)
            else:
                self.status_message = "Search cancelled."
        elif key == ord("n"):
            self._jump_search_match(1)
        elif key == ord("N"):
            self._jump_search_match(-1)
        elif key == ord("f"):
            current_column = self._current_column_name()
            if current_column is None:
                self.status_message = "No column selected."
            else:
                value = self._prompt(stdscr, f"Filter {current_column} contains")
                if value:
                    self.filter_column = current_column
                    self.filter_term = value
                    self.selected_row = 0
                    self.row_offset = 0
                    self._rebuild_view()
                    self.status_message = f"Filtered {current_column} by '{value}'."
                else:
                    self.status_message = "Filter cancelled."
        elif key == ord("C"):
            self.filter_column = None
            self.filter_term = ""
            self.search_term = ""
            self._rebuild_view()
            self.status_message = "Cleared search and filter state."
        elif key == ord("s"):
            current_column = self._current_column_name()
            if current_column is not None:
                self.sort_column = current_column
                self.sort_reverse = False
                self._rebuild_view()
                self.status_message = f"Sorted by {current_column} ascending."
        elif key == ord("S"):
            current_column = self._current_column_name()
            if current_column is not None:
                self.sort_column = current_column
                self.sort_reverse = True
                self._rebuild_view()
                self.status_message = f"Sorted by {current_column} descending."
        elif key == ord("x"):
            current_column = self._current_column_name()
            if current_column is None:
                self.status_message = "No column selected."
            elif len(self.visible_columns) == 1:
                self.status_message = "Cannot hide the last visible column."
            else:
                self.visible_columns = [column for column in self.visible_columns if column != current_column]
                self.pinned_columns = [column for column in self.pinned_columns if column != current_column]
                self.selected_column_name = self._current_display_columns()[0]
                self._rebuild_view()
                self.status_message = f"Hid column {current_column}."
        elif key == ord("X"):
            self.visible_columns = list(self.active_explorer.df.columns)
            self.pinned_columns = []
            self.selected_column_name = self.visible_columns[0] if self.visible_columns else None
            self._rebuild_view()
            self.status_message = "Restored all columns."
        elif key == ord("p"):
            current_column = self._current_column_name()
            if current_column is None:
                self.status_message = "No column selected."
            elif current_column in self.pinned_columns:
                self.pinned_columns = [column for column in self.pinned_columns if column != current_column]
                self.status_message = f"Unpinned {current_column}."
            else:
                self.pinned_columns.append(current_column)
                self.status_message = f"Pinned {current_column}."
            self._rebuild_view()
        elif key == ord("e"):
            output_path = self._prompt(stdscr, "Export visible view to")
            if output_path:
                exported = self.view_df[self._current_display_columns()] if self._current_display_columns() else self.view_df
                path = write_dataframe(exported.reset_index(drop=False).rename(columns={"index": "#"}), output_path)
                self.status_message = f"Exported {len(exported)} rows to {path}."
            else:
                self.status_message = "Export cancelled."
        elif key == ord("w"):
            if self.active_file is None:
                self.status_message = "Watch mode needs a file on disk."
            else:
                self.watch_mode = not self.watch_mode
                self.status_message = f"Watch mode {'enabled' if self.watch_mode else 'disabled'}."
        elif key == ord("R"):
            self._reload_active_file(preserve_view=True)
            self.status_message = f"Reloaded {self._display_path(self.active_file)}."
        elif key == ord("r"):
            self.mode = "files"
            self.start_scan()
        elif key == curses.KEY_RESIZE:
            pass

        self._ensure_selected_row_visible()
        return True

    def _activate_selected_entry(self) -> None:
        if not self.entries:
            self.status_message = "No entries to open."
            return
        entry = self.entries[self.selected_index]
        if entry.kind == "file":
            self._open_file(entry.path)
        elif entry.kind in {"dir", "up"}:
            self._enter_directory(entry.path)

    def _enter_directory(self, directory: Path) -> None:
        if not directory.is_dir():
            self.status_message = f"Not a directory: {directory}"
            return
        if not self._is_within_root(directory):
            self.status_message = "Cannot leave the selected root."
            return
        self.current_dir = directory
        self.selected_index = 0
        self.file_scroll = 0
        self._seed_directory_files(directory)
        if self.list_mode not in {"browse", "all"}:
            self.list_mode = "browse"
        self._refresh_entries()
        self.status_message = f"Browsing {self._dir_label(self.current_dir)}."

    def _go_up_directory(self) -> None:
        if self.current_dir == self.root_path:
            self.status_message = "Already at the root folder."
            return
        self._enter_directory(self.current_dir.parent)

    def _render_file_list(self, stdscr) -> None:
        height, _ = stdscr.getmaxyx()
        title = (
            f" CSV Explorer | {self.list_mode.upper()} | root: {self.root_path.name or self.root_path} "
            f"| here: {self._dir_label(self.current_dir)} | {self._directory_summary(self.current_dir)} "
            f"| {self._scan_status_text()} "
        )
        if self.explorer_search_term:
            title += f"| find {self.explorer_search_term} "
        self._draw_line(stdscr, 0, title, self.title_attr)

        if not self.entries:
            self._draw_line(
                stdscr,
                1,
                " No entries yet. Press / to search, C to clear, a for recursive view, F for favorites, R for recents, r to rescan, q to quit. ",
                self.table_alt_attr,
            )
        else:
            visible_rows = max(height - 2, 1)
            self._ensure_selected_visible(visible_rows)
            end_index = min(self.file_scroll + visible_rows, len(self.entries))
            for line_number, index in enumerate(range(self.file_scroll, end_index), start=1):
                entry = self.entries[index]
                prefix = ">> " if index == self.selected_index else "   "
                suffix = " [open]" if entry.kind == "file" and entry.path == self.active_file else ""
                detail = f"  [{entry.detail}]" if entry.detail else ""

                if index == self.selected_index:
                    attr = self.selected_attr
                elif entry.kind == "file" and entry.path == self.active_file:
                    attr = self.active_attr
                elif entry.label.startswith("* "):
                    attr = self.favorite_attr
                elif entry.kind == "dir":
                    attr = self.directory_attr
                elif entry.kind == "up":
                    attr = self.up_attr
                else:
                    attr = self.table_alt_attr if line_number % 2 == 0 else 0

                self._draw_line(stdscr, line_number, f"{prefix}{entry.label}{detail}{suffix}", attr)

        if self.status_message:
            footer = f" {self.status_message} "
            footer_attr = self.status_attr
        else:
            footer = (
                " Enter/l open | / find | C clear | h/u/backspace up | a all | F favorites | R recents | * star "
                f"| m summary | D drift | r rescan | ? help | q quit | {self._scan_status_text()} "
            )
            footer_attr = self.footer_attr
        self._draw_line(stdscr, height - 1, footer, footer_attr)

    def _render_viewer(self, stdscr) -> None:
        if self.active_explorer is None or self.active_file is None:
            self.mode = "files"
            return

        height, width = stdscr.getmaxyx()
        body_height = max(height - 2, 1)
        page_rows = max(body_height - 1, 1)
        self.viewer_page_rows = page_rows
        dataframe = self.view_df

        if dataframe.empty:
            row_span = "rows 0 of 0"
        else:
            start_row = self.row_offset + 1
            end_row = min(self.row_offset + page_rows, len(dataframe))
            row_span = f"rows {start_row}-{end_row} of {len(dataframe)}"

        current_column = self._current_column_name() or "-"
        state_bits: list[str] = []
        if self.filter_column and self.filter_term:
            state_bits.append(f"filter {self.filter_column}~{self.filter_term}")
        if self.search_term:
            state_bits.append(f"search {self.search_term}")
        if self.sort_column:
            state_bits.append(f"sort {self.sort_column}{' desc' if self.sort_reverse else ' asc'}")
        if self.pinned_columns:
            state_bits.append(f"pinned {len(self.pinned_columns)}")
        if self.watch_mode:
            state_bits.append("watch")
        if self.preview_rows is not None:
            state_bits.append(f"preview {self.preview_rows}")

        title = (
            f" CSV Explorer | VIEW | {self.active_file} | {row_span} | visible cols {len(self._current_display_columns())} "
            f"| current {current_column} | x={self.col_offset}"
        )
        if state_bits:
            title += " | " + " | ".join(state_bits)
        self._draw_line(stdscr, 0, title, self.title_attr)

        table_lines = self._build_table_lines(page_rows)
        selected_line = 1 + (self.selected_row - self.row_offset)
        for line_number in range(body_height):
            line = table_lines[line_number] if line_number < len(table_lines) else ""
            if line_number == 0:
                attr = self.table_header_attr
            elif line_number == selected_line and not self.view_df.empty:
                attr = self.selected_attr
            else:
                attr = self.table_alt_attr if line_number % 2 == 0 else 0
            self._draw_line(stdscr, line_number + 1, line[self.col_offset:], attr)

        if self.status_message:
            footer = f" {self.status_message} "
            footer_attr = self.status_attr
        else:
            footer = (
                " arrows/hjkl move | [ ] column | / search | f filter | s/S sort | x/X columns | "
                "p pin | Enter detail | i inspect | d quality | e export | w watch | R reload | "
                f"b back | r files | ? help | {self._scan_status_text()} "
            )
            footer_attr = self.footer_attr
        self._draw_line(stdscr, height - 1, footer, footer_attr)

    def _build_table_lines(self, page_rows: int) -> list[str]:
        if self.view_df.empty:
            return ["(empty result)"]

        display_columns = self._current_display_columns()
        if not display_columns:
            return ["(no visible columns)"]

        cache_key = (id(self.view_df), self.row_offset, page_rows, tuple(display_columns))
        if cache_key == self.table_cache_key:
            return self.table_cache_lines

        preview = self.view_df.iloc[self.row_offset : self.row_offset + page_rows][display_columns].copy()
        preview.insert(0, "#", preview.index)

        with pd.option_context(
            "display.max_columns",
            None,
            "display.width",
            None,
            "display.expand_frame_repr",
            False,
            "display.max_colwidth",
            40,
        ):
            rendered = preview.to_string(index=False, na_rep="")
        self.table_cache_key = cache_key
        self.table_cache_lines = rendered.splitlines() if rendered else ["(empty result)"]
        return self.table_cache_lines

    def _selected_row_lines(self) -> list[str]:
        if self.view_df.empty:
            return ["No row selected."]
        row = self.view_df.iloc[self.selected_row]
        lines = [f"Selected row {self.selected_row + 1} | source index {row.name}", ""]
        for column in self.active_explorer.df.columns:
            lines.append(f"{column}: {display_value(row[column], 400)}")
        return lines

    def _directory_detail(self, directory: Path) -> str:
        direct_count = self.direct_file_counts.get(directory, 0)
        total_count = self.subtree_file_counts.get(directory, 0)

        if self.scan_complete:
            if direct_count and total_count > direct_count:
                return f"{direct_count} here | {total_count} total"
            return f"{total_count} total"

        if direct_count or total_count:
            if direct_count and total_count > direct_count:
                return f"{direct_count} here | {total_count}+ total"
            return f"{max(direct_count, total_count)}+ total"

        return "scanning..."

    def _directory_summary(self, directory: Path) -> str:
        direct_count = self.direct_file_counts.get(directory, 0)
        total_count = self.subtree_file_counts.get(directory, 0)
        if self.scan_complete:
            return f"{direct_count} here | {total_count} total"
        if direct_count or total_count:
            return f"{direct_count} here | {total_count}+ total"
        return "stats loading"

    def _scan_status_text(self) -> str:
        discovered = len(self.all_files)
        if self.scan_error:
            return f"scan error | {discovered} files"
        if self.scan_in_progress:
            return f"scanning {self.scanned_directories} dirs | {discovered} files"
        if self.scan_complete:
            return f"scan complete | {discovered} files"
        return f"{discovered} files"

    def _display_path(self, file_path: Path) -> str:
        return str(file_path.relative_to(self.root_path))

    def _dir_label(self, directory: Path) -> str:
        if directory == self.root_path:
            return "."
        return str(directory.relative_to(self.root_path))

    def _is_within_root(self, path: Path) -> bool:
        resolved = path.resolve()
        return resolved == self.root_path or self.root_path in resolved.parents

    @staticmethod
    def _is_ignored_dir(path: Path) -> bool:
        return path.name in IGNORED_DIR_NAMES or path.name.endswith(".egg-info")

    def _ensure_selected_visible(self, visible_rows: int) -> None:
        if self.selected_index < self.file_scroll:
            self.file_scroll = self.selected_index
        elif self.selected_index >= self.file_scroll + visible_rows:
            self.file_scroll = self.selected_index - visible_rows + 1

    def _draw_line(self, stdscr, y: int, text: str, attr: int = 0) -> None:
        height, width = stdscr.getmaxyx()
        if not 0 <= y < height or width <= 1:
            return
        try:
            stdscr.addnstr(y, 0, text.ljust(width - 1), width - 1, attr)
        except curses.error:
            pass

    def _render_overlay(self, stdscr) -> None:
        height, width = stdscr.getmaxyx()
        box_height = min(height - 4, max(8, len(self.overlay_lines) + 4))
        box_width = min(width - 4, max(60, len(self.overlay_title) + 6))
        top = max((height - box_height) // 2, 1)
        left = max((width - box_width) // 2, 1)
        bottom = min(top + box_height - 1, height - 2)
        right = min(left + box_width - 1, width - 2)

        border = "+" + "-" * max(right - left - 1, 1) + "+"
        self._draw_line(stdscr, top, " " * (right - left + 1), self.title_attr)
        self._draw_line(stdscr, top, border[: right - left + 1], self.title_attr)
        self._draw_line(stdscr, top + 1, f"| {self.overlay_title}"[: right - left] + "|", self.title_attr)

        visible_body_rows = max(bottom - top - 3, 1)
        for offset in range(visible_body_rows):
            line_index = self.overlay_scroll + offset
            content = self.overlay_lines[line_index] if line_index < len(self.overlay_lines) else ""
            content = content[: max(right - left - 3, 1)]
            self._draw_line(stdscr, top + 2 + offset, f"| {content}".ljust(right - left) + "|", self.table_alt_attr)

        footer = f" {self.overlay_scroll + 1}-{min(self.overlay_scroll + visible_body_rows, len(self.overlay_lines))} / {len(self.overlay_lines)} | q close "
        self._draw_line(stdscr, bottom, border[: right - left + 1], self.footer_attr)
        self._draw_line(stdscr, bottom, footer[: right - left + 1], self.footer_attr)

    def _explorer_help_lines(self) -> list[str]:
        return [
            "Explorer keys",
            "",
            "Enter or l: open selected file or folder",
            "h, u, Backspace: go up one folder",
            "a: toggle folder browser and recursive all-files view",
            "/: fuzzy-find files and folders in the current list mode",
            "C: clear the explorer search filter",
            "F: toggle favorites view",
            "R: toggle recents view",
            "*: add or remove the selected item from favorites",
            "m: show a folder summary overlay",
            "D: show schema drift for the current folder or root",
            "r: rescan files",
            "?: show this help",
            "q: quit",
        ]

    def _viewer_help_lines(self) -> list[str]:
        return [
            "Viewer keys",
            "",
            "Up/Down, j/k: move the selected row",
            "Left/Right, h/l: scroll horizontally",
            "PgUp/PgDn, g/G: move faster through rows",
            "[ and ]: move the active column",
            "/, n, N: search rows and cycle through matches",
            "f: filter the active column with a contains match",
            "C: clear search and filter state",
            "s / S: sort ascending or descending by the active column",
            "x / X: hide the active column or restore all columns",
            "p: pin or unpin the active column at the left edge of the table order",
            "Enter: open the selected row detail view",
            "i: inspect the active column",
            "d: show file-wide data quality checks",
            "e: export the current visible view",
            "w: watch the file for changes and auto-reload it",
            "R: reload the file now",
            "b: return to the file list",
            "r: return to the file list and rescan",
            "?: show this help",
            "q: quit",
        ]

    def _init_theme(self) -> None:
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            return

        if not curses.has_colors():
            return

        title_bg = curses.COLOR_RED
        footer_bg = curses.COLOR_RED
        selected_bg = curses.COLOR_RED
        header_bg = curses.COLOR_RED
        accent_fg = curses.COLOR_RED
        status_fg = curses.COLOR_RED

        if curses.can_change_color() and getattr(curses, "COLORS", 0) >= 24:
            try:
                curses.init_color(20, 150, 20, 35)
                curses.init_color(21, 310, 55, 75)
                curses.init_color(22, 560, 110, 130)
                curses.init_color(23, 820, 210, 235)
                title_bg = 23
                footer_bg = 22
                selected_bg = 21
                header_bg = 20
                accent_fg = 22
                status_fg = curses.COLOR_RED
            except curses.error:
                title_bg = curses.COLOR_RED
                footer_bg = curses.COLOR_RED
                selected_bg = curses.COLOR_RED
                header_bg = curses.COLOR_RED
                accent_fg = curses.COLOR_RED
                status_fg = curses.COLOR_RED

        theme_pairs = [
            (1, curses.COLOR_BLACK, title_bg),
            (2, curses.COLOR_BLACK, footer_bg),
            (3, curses.COLOR_WHITE, selected_bg),
            (4, curses.COLOR_WHITE, header_bg),
            (5, accent_fg, -1),
            (6, status_fg, -1),
        ]

        for pair_number, foreground, background in theme_pairs:
            try:
                curses.init_pair(pair_number, foreground, background)
            except curses.error:
                return

        self.title_attr = curses.color_pair(1) | curses.A_BOLD
        self.footer_attr = curses.color_pair(2) | curses.A_BOLD
        self.selected_attr = curses.color_pair(3) | curses.A_BOLD
        self.table_header_attr = curses.color_pair(4) | curses.A_BOLD
        self.active_attr = curses.color_pair(4) | curses.A_BOLD
        self.directory_attr = curses.color_pair(5) | curses.A_BOLD
        self.favorite_attr = curses.color_pair(5) | curses.A_BOLD
        self.up_attr = curses.color_pair(6) | curses.A_BOLD
        self.status_attr = curses.color_pair(6) | curses.A_BOLD
        self.table_alt_attr = curses.color_pair(5)


def exit_with_error(message: str) -> None:
    prefix = style_text("Error:", "1", "31", stream=sys.stderr)
    print(f"{prefix} {message}", file=sys.stderr)
    sys.exit(1)


def cmd_head(args) -> None:
    CSVExplorer(args.file).head(args.rows)


def cmd_filter(args) -> None:
    CSVExplorer(args.file).filter_rows(args.column, args.operator, args.value)


def cmd_select(args) -> None:
    CSVExplorer(args.file).select_columns(args.columns)


def cmd_stats(args) -> None:
    CSVExplorer(args.file).stats()


def cmd_unique(args) -> None:
    CSVExplorer(args.file).unique_values(args.column)


def cmd_inspect(args) -> None:
    CSVExplorer(args.file).inspect_column(args.column)


def cmd_quality(args) -> None:
    CSVExplorer(args.file).quality()


def cmd_export(args) -> None:
    CSVExplorer(args.file).export(args.output)


def cmd_explore(args) -> None:
    try:
        CSVExplorerTUI(args.path).run()
    except (FileNotFoundError, PermissionError, ValueError, IsADirectoryError) as exc:
        exit_with_error(str(exc))
    except curses.error as exc:
        exit_with_error(f"Unable to launch explorer UI: {exc}")


def cmd_summary(args) -> None:
    path = Path(args.path).expanduser()
    if not path.exists():
        exit_with_error(f"Path not found: {path}")
    files = [path] if path.is_file() else list_supported_files(path)
    for line in build_summary_lines(path, files):
        print(line)


def cmd_drift(args) -> None:
    path = Path(args.path).expanduser()
    if not path.exists():
        exit_with_error(f"Path not found: {path}")
    files = [path] if path.is_file() else list_supported_files(path)
    for line in build_schema_drift_lines(files, path if path.is_dir() else path.parent):
        print(line)


def cmd_compare(args) -> None:
    left_df, _ = load_dataframe(args.left)
    right_df, _ = load_dataframe(args.right)
    for line in compare_dataframes(left_df, right_df, key=args.key):
        print(line)


def cmd_sql_alias(args) -> None:
    explorer = CSVExplorer(args.file)
    explorer.sql(args.query)


def cmd_demo(args, prog_name: str) -> None:
    try:
        target = resolve_demo_target(args.entry, args.browse)
        CSVExplorerTUI(
            target,
            root_path=DEMO_ROOT,
            startup_overlay_title="Demo Guide",
            startup_overlay_lines=build_demo_overlay_lines(prog_name),
        ).run()
    except (FileNotFoundError, PermissionError, ValueError, IsADirectoryError) as exc:
        exit_with_error(str(exc))
    except curses.error as exc:
        exit_with_error(f"Unable to launch demo UI: {exc}")


def build_file_parser(prog_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description="csvex - a colorful CSV explorer for the terminal",
        epilog=(
            f"Interactive defaults:\n"
            f"  {prog_name} file.csv\n"
            f"  {prog_name} demo\n"
            f"  {prog_name} explore [path]\n\n"
            f"Power commands:\n"
            f"  {prog_name} compare left.csv right.csv --key id\n"
            f"  {prog_name} sql file.csv \"select city, count(*) from data group by city\"\n"
            f"  {prog_name} summary [path]\n"
            f"  {prog_name} drift [path]"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", help="Path to a tabular file or directory", nargs="?", default=None)
    parser.add_argument("-v", "--version", action="version", version=f"csvex {APP_VERSION} by Tomas Gonzalez")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    p_head = subparsers.add_parser("head", help="Display first N rows")
    p_head.add_argument("-n", "--rows", type=int, default=10, help="Number of rows to display (default: 10)")

    p_filter = subparsers.add_parser("filter", help="Filter rows by column value")
    p_filter.add_argument("column", help="Column name to filter on")
    p_filter.add_argument("operator", help="Operator: ==, !=, >, <, >=, <=, contains, startswith, endswith")
    p_filter.add_argument("value", help="Value to filter by")

    p_select = subparsers.add_parser("select", help="Select columns to display")
    p_select.add_argument("columns", nargs="+", help="Column names (space-separated)")

    subparsers.add_parser("stats", help="Show statistics for numeric columns")

    p_unique = subparsers.add_parser("unique", help="List unique values in a column")
    p_unique.add_argument("column", help="Column name")

    p_inspect = subparsers.add_parser("inspect", help="Inspect one column in depth")
    p_inspect.add_argument("column", help="Column name")

    subparsers.add_parser("quality", help="Run lightweight data quality checks")

    p_export = subparsers.add_parser("export", help="Export the current file to another path")
    p_export.add_argument("output", help="Output path (.csv, .tsv, .csv.gz, .parquet)")

    return parser


def build_command_parser(prog_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{prog_name} <file>",
        description="Run a non-interactive command against a tabular file.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    p_head = subparsers.add_parser("head", help="Display first N rows")
    p_head.add_argument("-n", "--rows", type=int, default=10, help="Number of rows to display (default: 10)")

    p_filter = subparsers.add_parser("filter", help="Filter rows by column value")
    p_filter.add_argument("column", help="Column name to filter on")
    p_filter.add_argument("operator", help="Operator: ==, !=, >, <, >=, <=, contains, startswith, endswith")
    p_filter.add_argument("value", help="Value to filter by")

    p_select = subparsers.add_parser("select", help="Select columns to display")
    p_select.add_argument("columns", nargs="+", help="Column names (space-separated)")

    subparsers.add_parser("stats", help="Show statistics for numeric columns")

    p_unique = subparsers.add_parser("unique", help="List unique values in a column")
    p_unique.add_argument("column", help="Column name")

    p_inspect = subparsers.add_parser("inspect", help="Inspect one column in depth")
    p_inspect.add_argument("column", help="Column name")

    subparsers.add_parser("quality", help="Run lightweight data quality checks")

    p_export = subparsers.add_parser("export", help="Export the current file to another path")
    p_export.add_argument("output", help="Output path (.csv, .tsv, .csv.gz, .parquet)")

    return parser


def build_explorer_parser(prog_name: str, command_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{prog_name} {command_name}", description="Browse tabular files in a terminal UI.")
    parser.add_argument("path", nargs="?", default=".", help="Directory or file to browse (default: current directory)")
    return parser


def build_demo_parser(prog_name: str, command_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{prog_name} {command_name}", description="Launch the bundled csvex demo workspace.")
    parser.add_argument("entry", nargs="?", default=None, help="Optional demo file or folder relative to the built-in demo pack")
    parser.add_argument("--browse", action="store_true", help="Start in the demo folder browser instead of opening the default demo file")
    return parser


def build_path_parser(prog_name: str, command_name: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{prog_name} {command_name}", description=description)
    parser.add_argument("path", nargs="?", default=".", help="Directory or file to analyze (default: current directory)")
    return parser


def build_compare_parser(prog_name: str, command_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{prog_name} {command_name}", description="Compare two tabular files.")
    parser.add_argument("left", help="Left file")
    parser.add_argument("right", help="Right file")
    parser.add_argument("--key", help="Optional key column to align rows before diffing")
    return parser


def build_sql_parser(prog_name: str, command_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{prog_name} {command_name}", description="Run an in-memory SQL query against one file.")
    parser.add_argument("file", help="File to query")
    parser.add_argument("query", help='SQL query using table name "data"')
    return parser


def main() -> None:
    prog_name = Path(sys.argv[0]).name or "csvex"
    argv = sys.argv[1:]

    if argv and argv[0] in EXPLORER_ALIASES:
        args = build_explorer_parser(prog_name, argv[0]).parse_args(argv[1:])
        cmd_explore(args)
        return
    if argv and argv[0] in DEMO_ALIASES:
        args = build_demo_parser(prog_name, argv[0]).parse_args(argv[1:])
        cmd_demo(args, prog_name)
        return
    if argv and argv[0] in SUMMARY_ALIASES:
        args = build_path_parser(prog_name, argv[0], "Summarize the tabular files under a path.").parse_args(argv[1:])
        cmd_summary(args)
        return
    if argv and argv[0] in DRIFT_ALIASES:
        args = build_path_parser(prog_name, argv[0], "Show schema drift across the tabular files under a path.").parse_args(argv[1:])
        cmd_drift(args)
        return
    if argv and argv[0] in COMPARE_ALIASES:
        args = build_compare_parser(prog_name, argv[0]).parse_args(argv[1:])
        cmd_compare(args)
        return
    if argv and argv[0] in SQL_ALIASES:
        args = build_sql_parser(prog_name, argv[0]).parse_args(argv[1:])
        cmd_sql_alias(args)
        return

    if not argv:
        print_welcome_banner(prog_name)
        return
    if argv[0] in {"-h", "--help"}:
        build_file_parser(prog_name).print_help()
        return
    if argv[0] in {"-v", "--version"}:
        print(f"csvex {APP_VERSION} by Tomas Gonzalez")
        return

    file_path = argv[0]

    if len(argv) == 1:
        target_path = Path(file_path).expanduser()
        if sys.stdin.isatty() and sys.stdout.isatty() and file_path != "-":
            cmd_explore(argparse.Namespace(path=file_path))
            return
        if file_path != "-" and target_path.exists() and target_path.is_dir():
            exit_with_error("Directory browsing requires an interactive terminal.")
        args = argparse.Namespace(file=file_path, command="head", rows=10)
    else:
        args = build_command_parser(prog_name).parse_args(argv[1:])
        args.file = file_path

    try:
        if args.command == "head":
            cmd_head(args)
        elif args.command == "filter":
            cmd_filter(args)
        elif args.command == "select":
            cmd_select(args)
        elif args.command == "stats":
            cmd_stats(args)
        elif args.command == "unique":
            cmd_unique(args)
        elif args.command == "inspect":
            cmd_inspect(args)
        elif args.command == "quality":
            cmd_quality(args)
        elif args.command == "export":
            cmd_export(args)
    except (
        FileNotFoundError,
        PermissionError,
        ValueError,
        IsADirectoryError,
        pd.errors.EmptyDataError,
        pd.errors.ParserError,
    ) as exc:
        exit_with_error(str(exc))


if __name__ == "__main__":
    main()
