#!/usr/bin/env python3
"""CSV Explorer - by Tomas Gonzalez - A CLI tool for exploring CSV files."""

from __future__ import annotations

import argparse
import curses
import os
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

APP_VERSION = "1.3.0"
EXPLORER_ALIASES = {"explore", "explorer", "tui"}
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

try:
    import pandas as pd
except ImportError:
    print("Error: pandas is required. Install with: pip install pandas")
    sys.exit(1)


class CSVExplorer:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path).expanduser()
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        self.df = pd.read_csv(self.file_path)

    def head(self, n: int = 10) -> None:
        """Display first n rows."""
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", None)
        print(self.df.head(n).to_string(index=False))

    def filter_rows(self, column: str, operator: str, value: str) -> None:
        """Filter rows by column value."""
        if column not in self.df.columns:
            print(f"Error: Column '{column}' not found. Available: {', '.join(self.df.columns)}")
            sys.exit(1)

        col = self.df[column]
        try:
            if col.dtype in ["int64", "float64"]:
                value = float(value)
            elif col.dtype == "bool":
                value = value.lower() in ("true", "1", "yes")
        except ValueError:
            pass

        if operator == "==":
            mask = col == value
        elif operator == "!=":
            mask = col != value
        elif operator == ">":
            mask = col > value
        elif operator == "<":
            mask = col < value
        elif operator == ">=":
            mask = col >= value
        elif operator == "<=":
            mask = col <= value
        elif operator == "contains":
            mask = col.astype(str).str.contains(value, case=False, na=False)
        elif operator == "startswith":
            mask = col.astype(str).str.startswith(value, na=False)
        elif operator == "endswith":
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
        numeric = self.df.select_dtypes(include=["number"])
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


@dataclass(frozen=True)
class BrowserEntry:
    kind: str
    path: Path
    label: str
    detail: str = ""


class CSVExplorerTUI:
    def __init__(self, target_path: str):
        self.target_path = Path(target_path).expanduser().resolve()
        if not self.target_path.exists():
            raise FileNotFoundError(f"Path not found: {target_path}")
        if self.target_path.is_file() and self.target_path.suffix.lower() != ".csv":
            raise ValueError(f"Not a CSV file: {self.target_path}")

        self.root_path = self.target_path.parent if self.target_path.is_file() else self.target_path
        self.current_dir = self.root_path
        self.initial_file = self.target_path if self.target_path.is_file() else None
        self.all_csv_files: list[Path] = []
        self.all_csv_set: set[Path] = set()
        self.direct_csv_counts: dict[Path, int] = {}
        self.subtree_csv_counts: dict[Path, int] = {}
        self.entries: list[BrowserEntry] = []
        self.selected_index = 0
        self.file_scroll = 0
        self.mode = "files"
        self.list_mode = "browse"
        self.active_file: Path | None = None
        self.active_explorer: CSVExplorer | None = None
        self.row_offset = 0
        self.col_offset = 0
        self.status_message = ""
        self.title_attr = curses.A_BOLD
        self.footer_attr = curses.A_DIM
        self.status_attr = curses.A_BOLD
        self.selected_attr = curses.A_REVERSE | curses.A_BOLD
        self.active_attr = curses.A_BOLD
        self.directory_attr = curses.A_BOLD
        self.up_attr = curses.A_BOLD
        self.table_header_attr = curses.A_BOLD
        self.table_alt_attr = curses.A_DIM
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

    def start_scan(self, initial_scan: bool = False) -> None:
        previous_entry = self.entries[self.selected_index] if self.entries else None
        self.scan_generation += 1
        self.scan_in_progress = True
        self.scan_complete = False
        self.scan_error = ""
        self.scanned_directories = 0
        self.all_csv_files = []
        self.all_csv_set = set()
        self.direct_csv_counts = {}
        self.subtree_csv_counts = {}

        self._seed_directory_files(self.root_path)
        if self.current_dir != self.root_path:
            self._seed_directory_files(self.current_dir)
        if self.active_file is not None:
            self._apply_discovered_file(self.active_file)
            self.all_csv_files.sort(key=lambda path: self._display_path(path).lower())

        self._ensure_current_dir_is_valid()
        self._refresh_entries(previous_entry)

        if not initial_scan:
            self.status_message = f"Scanning {self._dir_label(self.current_dir)} for CSV files..."

        threading.Thread(
            target=self._scan_worker,
            args=(self.scan_generation,),
            daemon=True,
        ).start()

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
                    if file_path.suffix.lower() == ".csv":
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

        while processed < 500:
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
            self.all_csv_files.sort(key=lambda path: self._display_path(path).lower())

        if refresh_entries:
            self._ensure_current_dir_is_valid()
            self._refresh_entries(previous_entry)

    def _ensure_current_dir_is_valid(self) -> None:
        current = self.current_dir
        while current != self.root_path and (not current.exists() or not current.is_dir()):
            current = current.parent

        if current == self.root_path or self._is_within_root(current):
            self.current_dir = current
        else:
            self.current_dir = self.root_path

    def _apply_discovered_file(self, file_path: object) -> bool:
        if not isinstance(file_path, Path):
            return False
        if file_path in self.all_csv_set:
            return False

        self.all_csv_set.add(file_path)
        self.all_csv_files.append(file_path)
        self.direct_csv_counts[file_path.parent] = self.direct_csv_counts.get(file_path.parent, 0) + 1

        current = file_path.parent
        while True:
            self.subtree_csv_counts[current] = self.subtree_csv_counts.get(current, 0) + 1
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
                if child.is_file() and child.suffix.lower() == ".csv":
                    changed = self._apply_discovered_file(child) or changed
        except PermissionError as exc:
            self.status_message = str(exc)
            return

        if changed:
            self.all_csv_files.sort(key=lambda path: self._display_path(path).lower())

    def _refresh_entries(self, previous_entry: BrowserEntry | None = None) -> None:
        if self.list_mode == "all":
            self.entries = self._build_recursive_entries()
        else:
            self.entries = self._build_browse_entries()

        target_key = None
        if previous_entry is not None:
            target_key = (previous_entry.kind, previous_entry.path)

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
            if child.is_dir():
                if self._is_ignored_dir(child):
                    continue
                entries.append(BrowserEntry("dir", child, f"{child.name}/", self._directory_detail(child)))
            elif child.suffix.lower() == ".csv":
                entries.append(BrowserEntry("file", child, child.name))

        return entries

    def _build_recursive_entries(self) -> list[BrowserEntry]:
        entries: list[BrowserEntry] = []
        if self.current_dir != self.root_path:
            entries.append(BrowserEntry("up", self.current_dir.parent, "../", "parent"))

        for file_path in self.all_csv_files:
            if file_path.parent == self.current_dir or self.current_dir in file_path.parents:
                entries.append(
                    BrowserEntry(
                        "file",
                        file_path,
                        str(file_path.relative_to(self.current_dir)),
                        self._dir_label(file_path.parent),
                    )
                )
        return entries

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
            stdscr.erase()
            if self.mode == "files":
                self._render_file_list(stdscr)
            else:
                self._render_viewer(stdscr)
            stdscr.refresh()

            key = stdscr.getch()
            if key == -1:
                continue

            if self.mode == "files":
                if not self._handle_file_list_key(key, stdscr):
                    break
            elif not self._handle_viewer_key(key, stdscr):
                break

    def _handle_file_list_key(self, key: int, stdscr) -> bool:
        visible_rows = max(stdscr.getmaxyx()[0] - 2, 1)
        self.status_message = ""

        if key in (ord("q"), 27):
            return False
        if key in (curses.KEY_UP, ord("k")) and self.entries:
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
            self._toggle_list_mode()
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

        page_rows = self._viewer_page_rows(stdscr)
        max_row_offset = max(len(self.active_explorer.df) - page_rows, 0)

        if key in (ord("q"), 27):
            return False
        if key == ord("b"):
            self.mode = "files"
            return True
        if key in (curses.KEY_UP, ord("k")):
            self.row_offset = max(self.row_offset - 1, 0)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.row_offset = min(self.row_offset + 1, max_row_offset)
        elif key == curses.KEY_PPAGE:
            self.row_offset = max(self.row_offset - page_rows, 0)
        elif key == curses.KEY_NPAGE:
            self.row_offset = min(self.row_offset + page_rows, max_row_offset)
        elif key in (curses.KEY_HOME, ord("g")):
            self.row_offset = 0
        elif key in (curses.KEY_END, ord("G")):
            self.row_offset = max_row_offset
        elif key in (curses.KEY_LEFT, ord("h")):
            self.col_offset = max(self.col_offset - 4, 0)
        elif key in (curses.KEY_RIGHT, ord("l")):
            self.col_offset += 4
        elif key == ord("0"):
            self.col_offset = 0
        elif key == ord("$"):
            self.col_offset += max(stdscr.getmaxyx()[1] - 1, 20)
        elif key == ord("r"):
            self.mode = "files"
            self.start_scan()
        elif key == curses.KEY_RESIZE:
            pass

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
        self._refresh_entries()
        self.status_message = f"Browsing {self._dir_label(self.current_dir)}."

    def _go_up_directory(self) -> None:
        if self.current_dir == self.root_path:
            self.status_message = "Already at the root folder."
            return
        self._enter_directory(self.current_dir.parent)

    def _toggle_list_mode(self) -> None:
        self.list_mode = "all" if self.list_mode == "browse" else "browse"
        self.selected_index = 0
        self.file_scroll = 0
        self._refresh_entries()
        mode_name = "recursive file list" if self.list_mode == "all" else "folder browser"
        self.status_message = f"Switched to {mode_name}."

    def _open_file(self, file_path: Path) -> None:
        try:
            self.active_explorer = CSVExplorer(str(file_path))
        except Exception as exc:
            self.status_message = f"Error opening {self._display_path(file_path)}: {exc}"
            return

        self.active_file = file_path
        self.mode = "viewer"
        self.row_offset = 0
        self.col_offset = 0
        self.status_message = ""

    def _render_file_list(self, stdscr) -> None:
        height, _ = stdscr.getmaxyx()
        title = (
            f" CSV Explorer | {self.list_mode.upper()} | root: {self.root_path.name or self.root_path} "
            f"| here: {self._dir_label(self.current_dir)} | {self._directory_summary(self.current_dir)} "
            f"| {self._scan_status_text()} "
        )
        self._draw_line(stdscr, 0, title, self.title_attr)

        if not self.entries:
            self._draw_line(
                stdscr,
                1,
                " No entries yet. Press a for recursive view, h to go up, r to rescan, q to quit. ",
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
                " Enter/l open | h/u/backspace up | a toggle browse/all | arrows/jk move "
                f"| PgUp/PgDn page | g/G top/bottom | r rescan | q quit | {self._scan_status_text()} "
            )
            footer_attr = self.footer_attr
        self._draw_line(stdscr, height - 1, footer, footer_attr)

    def _render_viewer(self, stdscr) -> None:
        if self.active_explorer is None or self.active_file is None:
            self.mode = "files"
            return

        height, width = stdscr.getmaxyx()
        body_height = max(height - 2, 1)
        page_rows = self._viewer_page_rows(stdscr)
        dataframe = self.active_explorer.df
        self.row_offset = min(self.row_offset, max(len(dataframe) - page_rows, 0))

        table_lines = self._build_table_lines(dataframe, self.row_offset, page_rows)
        max_line_length = max((len(line) for line in table_lines), default=0)
        self.col_offset = min(self.col_offset, max(max_line_length - max(width - 1, 1), 0))

        if len(dataframe) == 0:
            row_span = "rows 0 of 0"
        else:
            start_row = self.row_offset + 1
            end_row = min(self.row_offset + page_rows, len(dataframe))
            row_span = f"rows {start_row}-{end_row} of {len(dataframe)}"

        title = f" CSV Explorer | VIEW | {self.active_file} | {row_span} | cols {len(dataframe.columns)} | x={self.col_offset} "
        self._draw_line(stdscr, 0, title, self.title_attr)

        for line_number in range(body_height):
            line = table_lines[line_number] if line_number < len(table_lines) else ""
            attr = self.table_header_attr if line_number == 0 else (self.table_alt_attr if line_number % 2 == 0 else 0)
            self._draw_line(stdscr, line_number + 1, line[self.col_offset:], attr)

        if self.status_message:
            footer = f" {self.status_message} "
            footer_attr = self.status_attr
        else:
            footer = (
                " arrows/hjkl move | PgUp/PgDn page | 0/$ left/right edge | "
                f"b back | r files | q quit | {self._scan_status_text()} "
            )
            footer_attr = self.footer_attr
        self._draw_line(stdscr, height - 1, footer, footer_attr)

    def _build_table_lines(self, dataframe, row_offset: int, page_rows: int) -> list[str]:
        preview = dataframe.iloc[row_offset : row_offset + page_rows].copy()
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

        lines = rendered.splitlines() if rendered else []
        if dataframe.empty:
            lines.append("(empty file)")
        return lines or ["(empty file)"]

    def _ensure_selected_visible(self, visible_rows: int) -> None:
        if self.selected_index < self.file_scroll:
            self.file_scroll = self.selected_index
        elif self.selected_index >= self.file_scroll + visible_rows:
            self.file_scroll = self.selected_index - visible_rows + 1

    def _viewer_page_rows(self, stdscr) -> int:
        return max(stdscr.getmaxyx()[0] - 3, 1)

    def _display_path(self, file_path: Path) -> str:
        return str(file_path.relative_to(self.root_path))

    def _dir_label(self, directory: Path) -> str:
        if directory == self.root_path:
            return "."
        return str(directory.relative_to(self.root_path))

    def _directory_detail(self, directory: Path) -> str:
        direct_count = self.direct_csv_counts.get(directory, 0)
        total_count = self.subtree_csv_counts.get(directory, 0)

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
        direct_count = self.direct_csv_counts.get(directory, 0)
        total_count = self.subtree_csv_counts.get(directory, 0)

        if self.scan_complete:
            return f"{direct_count} here | {total_count} total"
        if direct_count or total_count:
            return f"{direct_count} here | {total_count}+ total"
        return "stats loading"

    def _scan_status_text(self) -> str:
        discovered = len(self.all_csv_files)
        if self.scan_error:
            return f"scan error | {discovered} csvs"
        if self.scan_in_progress:
            return f"scanning {self.scanned_directories} dirs | {discovered} csvs"
        if self.scan_complete:
            return f"scan complete | {discovered} csvs"
        return f"{discovered} csvs"

    def _is_within_root(self, path: Path) -> bool:
        return path == self.root_path or self.root_path in path.parents

    @staticmethod
    def _is_ignored_dir(path: Path) -> bool:
        return path.name in IGNORED_DIR_NAMES or path.name.endswith(".egg-info")

    def _draw_line(self, stdscr, y: int, text: str, attr: int = 0) -> None:
        height, width = stdscr.getmaxyx()
        if not 0 <= y < height or width <= 1:
            return
        try:
            stdscr.addnstr(y, 0, text.ljust(width - 1), width - 1, attr)
        except curses.error:
            pass

    def _init_theme(self) -> None:
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            return

        if not curses.has_colors():
            return

        theme_pairs = [
            (1, curses.COLOR_BLACK, curses.COLOR_CYAN),
            (2, curses.COLOR_BLACK, curses.COLOR_MAGENTA),
            (3, curses.COLOR_BLACK, curses.COLOR_YELLOW),
            (4, curses.COLOR_BLACK, curses.COLOR_GREEN),
            (5, curses.COLOR_CYAN, -1),
            (6, curses.COLOR_YELLOW, -1),
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
        self.up_attr = curses.color_pair(6) | curses.A_BOLD
        self.status_attr = curses.color_pair(6) | curses.A_BOLD
        self.table_alt_attr = curses.color_pair(5)


def exit_with_error(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def cmd_head(args) -> None:
    explorer = CSVExplorer(args.file)
    explorer.head(args.rows)


def cmd_filter(args) -> None:
    explorer = CSVExplorer(args.file)
    explorer.filter_rows(args.column, args.operator, args.value)


def cmd_select(args) -> None:
    explorer = CSVExplorer(args.file)
    explorer.select_columns(args.columns)


def cmd_stats(args) -> None:
    explorer = CSVExplorer(args.file)
    explorer.stats()


def cmd_unique(args) -> None:
    explorer = CSVExplorer(args.file)
    explorer.unique_values(args.column)


def cmd_explore(args) -> None:
    try:
        explorer = CSVExplorerTUI(args.path)
        explorer.run()
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        exit_with_error(str(exc))
    except curses.error as exc:
        exit_with_error(f"Unable to launch explorer UI: {exc}")


def build_file_parser(prog_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description="CSV Explorer - by Tomas Gonzalez - A CLI tool for exploring CSV files",
        epilog=(
            f"Interactive defaults:\n"
            f"  {prog_name} file.csv\n"
            f"  {prog_name} explore [path]\n\n"
            f"Static preview:\n"
            f"  {prog_name} file.csv head -n 20"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", help="Path to CSV file or directory", nargs="?", default=None)
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

    return parser


def build_command_parser(prog_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{prog_name} <file>",
        description="Run a non-interactive command against a CSV file.",
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

    return parser


def build_explorer_parser(prog_name: str, command_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{prog_name} {command_name}",
        description="Browse CSV files in a terminal UI.",
    )
    parser.add_argument("path", nargs="?", default=".", help="Directory or CSV file to browse (default: current directory)")
    return parser


def main() -> None:
    prog_name = Path(sys.argv[0]).name or "csvex"
    argv = sys.argv[1:]

    if argv and argv[0] in EXPLORER_ALIASES:
        args = build_explorer_parser(prog_name, argv[0]).parse_args(argv[1:])
        cmd_explore(args)
        return

    if not argv:
        build_file_parser(prog_name).print_help()
        sys.exit(1)
    if argv[0] in {"-h", "--help"}:
        build_file_parser(prog_name).print_help()
        return
    if argv[0] in {"-v", "--version"}:
        print(f"csvex {APP_VERSION} by Tomas Gonzalez")
        return

    file_path = argv[0]

    if len(argv) == 1:
        target_path = Path(file_path).expanduser()
        if sys.stdin.isatty() and sys.stdout.isatty():
            cmd_explore(argparse.Namespace(path=file_path))
            return
        if target_path.exists() and target_path.is_dir():
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
    except (FileNotFoundError, PermissionError, ValueError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        exit_with_error(str(exc))


if __name__ == "__main__":
    main()
