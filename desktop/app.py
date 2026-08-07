"""
app.py
------
The window.

The whole interface is painted: each frame is composited as a single image and
blitted to one canvas, with real Tk entries floated over the fields that need a
caret. That is what allows the frosted panels, gradient marks and display type
- none of which ttk can express - while the data layer underneath stays plain
Python.

Rendering is layered so this stays responsive: the plane and its panels are
composited once per size and theme, charts once per data change, and only the
lightweight content layer is redrawn on hover.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

import charts
import paint
import theme
import ui
from models import (
    CATEGORIES,
    Bill,
    Income,
    ValidationError,
    leftover,
    next_due,
    next_payday,
    total,
)
from storage import BillRepository, IncomeRepository
from theme import RADIUS, SPACE, TYPE
from ui import Rect

WINDOW_SIZE = (1440, 916)
MIN_SIZE = (1180, 812)
ROW_HEIGHT = 35
FIELD_HEIGHT = 38
FIELD_PITCH = FIELD_HEIGHT + 16   # one editor row, caption included
CHIPS_WIDTH = 508


# --------------------------------------------------------------- ledger spec


@dataclass(frozen=True)
class Column:
    key: str
    width: float
    align: str
    role: str
    sort: Callable
    render: Callable
    stretch: bool = False


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"          # text | choice
    default: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class LedgerSpec:
    tab: str
    noun: str
    add_label: str
    empty_headline: str
    empty_detail: str
    columns: tuple[Column, ...]
    fields: tuple[Field, ...]
    build: Callable
    to_values: Callable
    #: Everything a search should match. Declared separately from the columns
    #: because a record carries more than the table shows - a bill's category
    #: is a coloured dot here, but people still expect to search for it.
    search_text: Callable
    default_sort: str
    export_name: str


@dataclass
class LedgerState:
    """Everything mutable about one tab."""

    spec: LedgerSpec
    repository: object
    records: list = field(default_factory=list)
    editing_key: str | None = None
    sort_key: str = ""
    sort_reverse: bool = False
    scroll: int = 0
    error: str = ""
    variables: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.sort_key:
            self.sort_key = self.spec.default_sort

    def visible(self, query: str) -> list:
        rows = self.records
        query = query.strip().casefold()
        if query:
            rows = [record for record in rows
                    if query in self.spec.search_text(record).casefold()]
        column = next(c for c in self.spec.columns if c.key == self.sort_key)
        return sorted(rows, key=column.sort, reverse=self.sort_reverse)

    def selected(self):
        if self.editing_key is None:
            return None
        return next((r for r in self.records if r.key == self.editing_key), None)

    def values(self) -> dict[str, str]:
        return {key: variable.get() for key, variable in self.variables.items()}

    def reset(self) -> None:
        self.editing_key = None
        self.error = ""
        for spec_field in self.spec.fields:
            self.variables[spec_field.key].set(spec_field.default)

    def load_into_editor(self, record) -> None:
        self.editing_key = record.key
        self.error = ""
        for key, value in self.spec.to_values(record).items():
            self.variables[key].set(value)


# ---------------------------------------------------------------------- app


class App:
    """Owns the data, the painted frame, and the event routing."""

    def __init__(self, root: tk.Tk, bills_repo=None, income_repo=None):
        self.root = root
        self.mode = "dark"
        self.palette = theme.palette_for(self.mode)

        self.canvas = tk.Canvas(root, highlightthickness=0, bd=0, takefocus=1)
        self.canvas.pack(fill="both", expand=True)

        self.ledgers = {
            "Bills": LedgerState(_bills_spec(), bills_repo or BillRepository()),
            "Income": LedgerState(_income_spec(), income_repo or IncomeRepository()),
        }
        for state in self.ledgers.values():
            for spec_field in state.spec.fields:
                variable = tk.StringVar(value=spec_field.default)
                variable.trace_add("write", lambda *_: self.invalidate(content=True))
                state.variables[spec_field.key] = variable

        self.active = "Bills"
        # The search box deliberately has no textvariable: its placeholder is
        # written into the widget, and a bound variable would carry that hint
        # straight into the filter as if the user had typed it.
        self.search_hint_shown = True

        self.hover: str | None = None
        self.open_dropdown: str | None = None
        self.pending: tuple[str, Callable] | None = None
        self.toast: tuple[str, str, str] | None = None
        self._toast_job = None

        self.entries: dict[str, tk.Entry] = {}
        self.hits = ui.Hits()
        self.layout: dict[str, Rect] = {}
        self._chrome: tuple | None = None
        self._chart_cache: dict = {}
        self._photo = None
        self._size = (0, 0)
        self._repaint_job = None

        self._bind()
        self.refresh()

    # ------------------------------------------------------------- plumbing

    def _bind(self) -> None:
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(None))
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._scroll(-3))
        self.canvas.bind("<Button-5>", lambda e: self._scroll(3))
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<Return>", lambda _e: self._save())

    def _has_focus(self, key: str) -> bool:
        """Whether the entry for `key` currently holds the caret.

        The entry may not exist yet on the first render, and `focus_get()` is
        None when the window is unfocused - comparing them directly would make
        every field look focused at once.
        """
        entry = self.entries.get(key)
        return entry is not None and self.root.focus_get() is entry

    def entry_for(self, key: str) -> tk.Entry:
        """A real input widget, created once and reused across layouts."""
        if key in self.entries:
            return self.entries[key]

        if key == "search":
            entry = tk.Entry(self.canvas, bd=0, highlightthickness=0, relief="flat",
                             font=(theme.TK_SANS, 11))
            entry.insert(0, self._search_hint())
            entry.bind("<FocusIn>", self._search_focus_in)
            entry.bind("<FocusOut>", self._search_focus_out)
            entry.bind("<KeyRelease>", lambda _e: self._on_search())
        else:
            variable = self.ledgers[self.active].variables[key.split(":", 1)[1]]
            entry = tk.Entry(self.canvas, textvariable=variable, bd=0,
                             highlightthickness=0, relief="flat",
                             font=(theme.TK_SANS, 11))
            entry.bind("<Return>", lambda _e: self._save())
            entry.bind("<FocusIn>", lambda _e: self.invalidate(content=True))
            entry.bind("<FocusOut>", lambda _e: self.invalidate(content=True))
        self.entries[key] = entry
        return entry

    def _search_hint(self) -> str:
        return f"Search {self.active.lower()}…"

    def search_query(self) -> str:
        """What the user actually typed - empty while the placeholder shows."""
        entry = self.entries.get("search")
        if entry is None or self.search_hint_shown:
            return ""
        return entry.get()

    def _search_focus_in(self, _event=None) -> None:
        if self.search_hint_shown:
            self.entries["search"].delete(0, "end")
            self.search_hint_shown = False
        self.invalidate(content=True)

    def _search_focus_out(self, _event=None) -> None:
        entry = self.entries["search"]
        if not entry.get():
            self.search_hint_shown = True
            entry.delete(0, "end")
            entry.insert(0, self._search_hint())
        self.invalidate(content=True)

    def _clear_search(self) -> None:
        """Reset the box to its placeholder, e.g. when switching tabs."""
        entry = self.entries.get("search")
        self.search_hint_shown = True
        if entry is not None:
            entry.delete(0, "end")
            entry.insert(0, self._search_hint())

    # -------------------------------------------------------------- state

    def refresh(self) -> None:
        """Reload both ledgers from disk and repaint everything."""
        for state in self.ledgers.values():
            state.records = state.repository.load()
        self._chart_cache.clear()
        self.invalidate(content=True)

    @property
    def bills(self) -> list[Bill]:
        return self.ledgers["Bills"].records

    @property
    def incomes(self) -> list[Income]:
        return self.ledgers["Income"].records

    @property
    def ledger(self) -> LedgerState:
        return self.ledgers[self.active]

    def invalidate(self, chrome: bool = False, content: bool = False) -> None:
        if chrome:
            self._chrome = None
            self._chart_cache.clear()
        if self._repaint_job is None:
            self._repaint_job = self.root.after_idle(self._repaint)

    def notify(self, title: str, message: str, tone: str = "accent") -> None:
        self.toast = (title, message, tone)
        if self._toast_job is not None:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(2600, self._clear_toast)
        self.invalidate(content=True)

    def _clear_toast(self) -> None:
        self.toast = None
        self._toast_job = None
        self.invalidate(content=True)

    def ask(self, message: str, on_yes: Callable) -> None:
        """Raise the painted confirmation modal."""
        self.pending = (message, on_yes)
        self.invalidate(content=True)

    def toggle_theme(self) -> None:
        self.mode = "light" if self.mode == "dark" else "dark"
        self.palette = theme.palette_for(self.mode)
        self.invalidate(chrome=True)

    # ------------------------------------------------------------- actions

    def _save(self) -> None:
        state = self.ledger
        try:
            record = state.spec.build(state.values())
        except ValidationError as error:
            state.error = str(error)
            self.invalidate(content=True)
            return

        renamed = state.editing_key is not None and state.editing_key != record.key
        clashes = any(r.key == record.key for r in state.records)
        if state.editing_key is None and clashes:
            self.ask(
                f"A {state.spec.noun} named “{record.name}” already exists. Replace it?",
                lambda: self._commit(state, record, False),
            )
            return
        self._commit(state, record, renamed)

    def _commit(self, state: LedgerState, record, renamed: bool) -> None:
        if renamed:
            state.repository.remove(state.editing_key)
        replaced = state.repository.upsert(record)
        state.reset()
        self.refresh()
        self.notify(
            f"{state.spec.noun.capitalize()} {'updated' if replaced or renamed else 'added'}",
            f"{record.name} · ${record.amount:,.2f} on the {_ordinal(record.day)}",
        )

    def _delete(self) -> None:
        state = self.ledger
        record = state.selected()
        if record is None:
            self.notify("Nothing selected",
                        f"Pick a {state.spec.noun} in the table first.", "critical")
            return

        def go():
            state.repository.remove(record.name)
            state.reset()
            self.refresh()
            self.notify(f"{state.spec.noun.capitalize()} deleted", record.name)

        self.ask(f"Delete “{record.name}”? This cannot be undone.", go)

    def _export(self) -> None:
        from tkinter import filedialog

        state = self.ledger
        path = filedialog.asksaveasfilename(
            parent=self.root, title=f"Export {state.spec.tab.lower()}",
            defaultextension=".csv", initialfile=state.spec.export_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            state.repository.export_to(path)
        except OSError as error:
            self.notify("Export failed", str(error), "critical")
            return
        self.notify("Exported", path)

    def _sort_by(self, key: str) -> None:
        state = self.ledger
        if state.sort_key == key:
            state.sort_reverse = not state.sort_reverse
        else:
            state.sort_key = key
            state.sort_reverse = key == "amount"
        self.invalidate(content=True)

    def _select_row(self, index: int) -> None:
        state = self.ledger
        rows = state.visible(self.search_query())
        if 0 <= index < len(rows):
            state.load_into_editor(rows[index])
            self.invalidate(content=True)

    def _on_search(self) -> None:
        self.ledger.scroll = 0
        self.invalidate(content=True)

    def _scroll(self, delta: int) -> None:
        state = self.ledger
        rows = len(state.visible(self.search_query()))
        capacity = self._row_capacity()
        state.scroll = max(0, min(state.scroll + delta, max(0, rows - capacity)))
        self.invalidate(content=True)

    def _row_capacity(self) -> int:
        area = self.layout.get("rows")
        return max(1, int(area.h // ROW_HEIGHT)) if area else 8

    # -------------------------------------------------------------- events

    def _on_resize(self, event) -> None:
        if (event.width, event.height) != self._size:
            self._size = (event.width, event.height)
            self.invalidate(chrome=True)

    def _set_hover(self, target: str | None) -> None:
        if target != self.hover:
            self.hover = target
            self.invalidate(content=True)

    def _on_motion(self, event) -> None:
        self._set_hover(self.hits.at(event.x, event.y))

    def _on_escape(self, _event=None) -> None:
        if self.pending:
            self.pending = None
        elif self.open_dropdown:
            self.open_dropdown = None
        else:
            self.ledger.reset()
        self.invalidate(content=True)

    def _on_wheel(self, event) -> None:
        self._scroll(-3 if event.delta > 0 else 3)

    def _on_click(self, event) -> None:
        target = self.hits.at(event.x, event.y)
        self.canvas.focus_set()

        if self.pending:
            if target == "confirm:yes":
                _, action = self.pending
                self.pending = None
                action()
            elif target == "confirm:no":
                self.pending = None
            self.invalidate(content=True)
            return

        if self.open_dropdown:
            prefix = self.open_dropdown
            if target and target.startswith(f"{prefix}:"):
                self.ledger.variables[prefix].set(target.split(":", 1)[1])
            self.open_dropdown = None
            self.invalidate(content=True)
            return

        if target is None:
            return
        if target.startswith("tab:"):
            self.active = list(self.ledgers)[int(target.split(":")[1])]
            self._clear_search()
            self.invalidate(content=True)
        elif target.startswith("sort:"):
            self._sort_by(target.split(":", 1)[1])
        elif target.startswith("row:"):
            self._select_row(int(target.split(":")[1]))
        elif target.startswith("open:"):
            self.open_dropdown = target.split(":", 1)[1]
            self.invalidate(content=True)
        elif target == "theme":
            self.toggle_theme()
        elif target == "save":
            self._save()
        elif target == "clear":
            self.ledger.reset()
            self.invalidate(content=True)
        elif target == "delete":
            self._delete()
        elif target == "export":
            self._export()

    # -------------------------------------------------------------- layout

    def compute_layout(self, size: tuple[int, int]) -> dict[str, Rect]:
        """Pure geometry for the whole window."""
        width, height = size
        pad = SPACE["page"]
        gap = SPACE["gap"]
        out: dict[str, Rect] = {}

        page = Rect(pad, pad, width - pad * 2, height - pad * 2)
        header, rest = page.cut_top(50, gap)
        hero, body = rest.cut_top(184, gap)
        left_width = round(body.w * 0.455)
        ledger, charts_panel = body.cut_left(left_width, gap)

        out["page"], out["header"] = page, header
        out["hero"], out["ledger"], out["charts"] = hero, ledger, charts_panel

        # Header.
        out["tabs"] = Rect(header.centre[0] - 116, header.y + 7, 232, 36)
        out["theme"] = Rect(header.right - 44, header.y + 7, 44, 36)

        # Hero: figure and meter on the left, three chips on the right.
        inner = hero.inset(26, 22)
        out["hero_text"] = Rect(inner.x, inner.y, inner.w - CHIPS_WIDTH - 24, 96)
        chip_gap = 12
        chip_width = (CHIPS_WIDTH - chip_gap * 2) / 3
        for index, key in enumerate(("chip_income", "chip_bills", "chip_next")):
            out[key] = Rect(inner.right - CHIPS_WIDTH + index * (chip_width + chip_gap),
                            inner.y, chip_width, 96)
        out["meter"] = Rect(inner.x, inner.bottom - 34, inner.w, 14)
        out["meter_caption"] = Rect(inner.x, inner.bottom - 14, inner.w, 14)

        # Ledger panel.
        pane = ledger.inset(22, 20)
        title_row, pane = pane.cut_top(34, 14)
        out["ledger_title"] = title_row
        out["search"] = Rect(title_row.right - 200, title_row.y + 1, 200, 32)

        # The editor's height follows from how many fields the tab declares, so
        # the table takes whatever is left rather than the two fighting over a
        # guessed constant and overflowing the panel. The terms mirror the
        # offsets used below, plus a little slack at the foot.
        state = self.ledgers[self.active]
        field_rows = (len(state.spec.fields) + 1) // 2
        editor_height = (16 + 20 + 12 + field_rows * FIELD_PITCH + 16 + 20 + 38 + 10)
        actions_height = 38
        table_height = pane.h - editor_height - actions_height - 28

        head, pane = pane.cut_top(26, 6)
        out["table_head"] = head
        rows_area, pane = pane.cut_top(max(ROW_HEIGHT, table_height), 12)
        out["rows"] = rows_area
        actions, pane = pane.cut_top(actions_height, 14)
        out["delete"] = Rect(actions.x, actions.y, 92, 36)
        out["export"] = Rect(actions.x + 100, actions.y, 108, 36)
        out["divider"] = Rect(pane.x, pane.y, pane.w, 1)
        out["editor"] = Rect(pane.x, pane.y + 16, pane.w, pane.h - 16)

        # Charts panel.
        board = charts_panel.inset(24, 22)
        bars_title, board = board.cut_top(16, 12)
        out["bars_title"] = bars_title
        ribbon_block = 34 + 18 + 22 * 3 + 18
        bars_area, board = board.cut_top(board.h - ribbon_block - 34, 26)
        out["bars"] = bars_area
        ribbon_title, board = board.cut_top(16, 12)
        out["ribbon_title"] = ribbon_title
        out["ribbon"] = board

        # Editor fields, two to a row.
        editor = out["editor"]
        label_row, grid = editor.cut_top(20, 12)
        out["editor_title"] = label_row
        column_width = (grid.w - 14) / 2
        for index, spec_field in enumerate(state.spec.fields):
            row, column = divmod(index, 2)
            out[f"field:{spec_field.key}"] = Rect(
                grid.x + column * (column_width + 14),
                grid.y + row * FIELD_PITCH + 16,
                column_width, FIELD_HEIGHT,
            )
        base = grid.y + field_rows * FIELD_PITCH + 16
        out["error"] = Rect(grid.x, base + 2, grid.w, 16)
        out["save"] = Rect(grid.x, base + 24, 128, 38)
        out["clear"] = Rect(grid.x + 138, base + 24, 88, 38)
        return out

    # -------------------------------------------------------------- render

    def _repaint(self) -> None:
        self._repaint_job = None
        width, height = self._size
        if width < 40 or height < 40:
            return

        size = (width, height)
        self.layout = self.compute_layout(size)
        self.hits = ui.Hits()

        frame = self._chrome_layer(size).copy()
        self._draw_content(frame, size)
        self._place_entries(frame)

        self._photo = paint.to_photo(frame.convert("RGB"))
        self.canvas.delete("frame")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw", tags="frame")
        self.canvas.tag_lower("frame")

    def _chrome_layer(self, size: tuple[int, int]) -> Image.Image:
        """Plane plus frosted panels. Rebuilt only on resize or theme change."""
        signature = (size, self.mode, self.active)
        if self._chrome and self._chrome[0] == signature:
            return self._chrome[1]

        image = paint.plane(size, self.mode).copy()
        for key in ("hero", "ledger", "charts"):
            ui.panel(image, size, self.layout[key], self.palette)
        self._chrome = (signature, image)
        return image

    def _chart(self, name: str, size: tuple[int, int], build) -> Image.Image:
        """Charts are rebuilt only when their data, size or theme changes."""
        key = (name, size, self.mode, self._data_signature())
        if key not in self._chart_cache:
            self._chart_cache[key] = build()
        return self._chart_cache[key]

    def _data_signature(self) -> tuple:
        return (
            tuple((b.name, b.amount, b.category) for b in self.bills),
            tuple((i.name, i.amount) for i in self.incomes),
        )

    def _draw_content(self, frame: Image.Image, size: tuple[int, int]) -> None:
        palette = self.palette
        layout = self.layout
        hits = self.hits

        ui.wordmark(frame, layout["header"].x, layout["header"].y + 6, palette)
        ui.tab_switcher(frame, layout["tabs"], list(self.ledgers),
                        list(self.ledgers).index(self.active), palette, hits, self.hover)
        ui.theme_button(frame, layout["theme"], palette, hits, "theme",
                        self.hover == "theme")

        self._draw_hero(frame, size)
        self._draw_ledger(frame)
        self._draw_charts(frame)

        if self.open_dropdown:
            self._draw_dropdown(frame, size)
        if self.toast:
            title, message, tone = self.toast
            width = 330
            rect = Rect(size[0] - width - SPACE["page"], size[1] - 84 - SPACE["page"],
                        width, 66)
            ui.toast(frame, size, rect, title, message, palette, tone)
        if self.pending:
            message, _ = self.pending
            rect = Rect(size[0] / 2 - 230, size[1] / 2 - 90, 460, 180)
            ui.modal(frame, size, size, rect, message, palette, hits, self.hover)

    def _draw_hero(self, frame: Image.Image, size: tuple[int, int]) -> None:
        palette, layout = self.palette, self.layout
        incoming, outgoing = total(self.incomes), total(self.bills)
        spare = leftover(self.bills, self.incomes)

        text = layout["hero_text"]
        ui.eyebrow(frame, text.x, text.y, "left over this month", palette)
        if self.incomes:
            ui.hero_figure(frame, text.x - 3, text.y + 16, _money(spare), palette,
                           "critical" if spare < 0 else "normal")
        else:
            ui.hero_figure(frame, text.x - 3, text.y + 16, "—", palette, "empty")

        chips = (
            ("chip_income", "monthly income",
             _money(incoming) if self.incomes else "—",
             f"{len(self.incomes)} source{'' if len(self.incomes) == 1 else 's'}"
             if self.incomes else "none yet", None),
            ("chip_bills", "monthly bills",
             _money(outgoing) if self.bills else "—",
             f"{len(self.bills)} bill{'' if len(self.bills) == 1 else 's'}"
             if self.bills else "none yet", None),
        )
        for key, label, value, note, accent in chips:
            ui.stat_chip(frame, layout[key], label, value, note, palette, accent)

        upcoming = next_due(self.bills)
        payday = next_payday(self.incomes)
        # The urgency is the headline here, not the date - "Tomorrow" reads
        # faster than "8th", and the name fits the note line without clipping.
        if upcoming:
            days = upcoming.days_until_due()
            ui.stat_chip(frame, layout["chip_next"], "next bill due",
                         _when(days).capitalize(),
                         f"{upcoming.name} · {_ordinal(upcoming.due_day)}", palette,
                         palette.warning if days <= 2 else None)
        elif payday:
            ui.stat_chip(frame, layout["chip_next"], "next payday",
                         _when(payday.days_until_paid()).capitalize(),
                         f"{payday.name} · {_ordinal(payday.pay_day)}", palette)
        else:
            ui.stat_chip(frame, layout["chip_next"], "next due", "—", "nothing scheduled",
                         palette)

        meter = layout["meter"]
        image = self._chart("meter", meter.size,
                            lambda: charts.income_meter(meter.size, self.bills,
                                                        self.incomes, palette))
        frame.alpha_composite(image, (int(meter.x), int(meter.y)))

        caption, tone = charts.meter_caption(self.bills, self.incomes)
        colour = {"critical": palette.critical, "muted": palette.ink_muted}.get(
            tone, palette.ink_secondary)
        paint.draw_text(frame, (meter.x, layout["meter_caption"].y), caption,
                        TYPE["small"], colour)

    def _draw_ledger(self, frame: Image.Image) -> None:
        palette, layout, hits = self.palette, self.layout, self.hits
        state = self.ledger
        spec = state.spec

        title = layout["ledger_title"]
        paint.draw_text(frame, (title.x, title.y + 2), spec.tab, TYPE["title"],
                        palette.ink)
        subtotal = total(state.records)
        paint.draw_text(frame, (title.x, title.y + 24),
                        f"{_money(subtotal)} per month" if state.records
                        else spec.empty_headline, TYPE["small"], palette.ink_muted)
        ui.well(frame, layout["search"], palette, focused=self._has_focus("search"))

        rows = state.visible(self.search_query())
        columns = [(c.key, c.width, c.align) for c in spec.columns]
        widths = self._column_widths(spec, layout["rows"].w)
        ui.table_header(frame, layout["table_head"],
                        [(c.key, w, c.align) for c, w in zip(spec.columns, widths)],
                        state.sort_key, state.sort_reverse, palette, hits, self.hover)

        area = layout["rows"]
        if not rows:
            ui.empty_state(frame, area,
                           spec.empty_headline if not state.records else "No matches",
                           spec.empty_detail if not state.records
                           else "Try a different search", palette)
        else:
            capacity = self._row_capacity()
            state.scroll = max(0, min(state.scroll, max(0, len(rows) - capacity)))
            window = rows[state.scroll:state.scroll + capacity]
            largest = max((r.amount for r in rows), default=1) or 1
            for offset, record in enumerate(window):
                index = state.scroll + offset
                rect = Rect(area.x, area.y + offset * ROW_HEIGHT, area.w, ROW_HEIGHT - 3)
                cells = [
                    (str(column.render(record)), width, column.align, column.role)
                    for column, width in zip(spec.columns, widths)
                ]
                accent = self._category_colour(record)
                ui.table_row(frame, rect, cells, palette, accent=accent,
                             selected=record.key == state.editing_key,
                             hovered=self.hover == f"row:{index}")
                hits.add(rect, f"row:{index}")

            if len(rows) > capacity:
                self._draw_scrollbar(frame, area, len(rows), capacity, state.scroll)

        ui.button(frame, layout["delete"], "Delete", palette, hits, "delete",
                  "danger", self.hover == "delete")
        ui.button(frame, layout["export"], "Export CSV", palette, hits, "export",
                  "ghost", self.hover == "export")
        ui.divider(frame, layout["divider"], palette)

        record = state.selected()
        heading = f"Editing {record.name}" if record else spec.add_label
        paint.draw_text(frame, (layout["editor_title"].x, layout["editor_title"].y),
                        heading, TYPE["body_strong"], palette.ink)

        for spec_field in spec.fields:
            rect = layout[f"field:{spec_field.key}"]
            paint.draw_text(frame, (rect.x + 2, rect.y - 17), spec_field.label,
                            TYPE["eyebrow"], palette.ink_muted)
            if spec_field.kind == "choice":
                ui.dropdown(frame, rect, state.variables[spec_field.key].get(),
                            palette, hits, f"open:{spec_field.key}",
                            open_=self.open_dropdown == spec_field.key,
                            hovered=self.hover == f"open:{spec_field.key}")
            else:
                ui.well(frame, rect, palette,
                        focused=self._has_focus(f"{self.active}:{spec_field.key}"))

        if state.error:
            paint.draw_text(frame, (layout["error"].x, layout["error"].y),
                            state.error, TYPE["small"], palette.critical)

        ui.button(frame, layout["save"],
                  "Save changes" if record else spec.add_label, palette, hits,
                  "save", "primary", self.hover == "save")
        ui.button(frame, layout["clear"], "Clear", palette, hits, "clear", "ghost",
                  self.hover == "clear")

    def _column_widths(self, spec: LedgerSpec, available: float) -> list[float]:
        """Give fixed columns their width and hand the remainder to the stretcher."""
        fixed = sum(c.width for c in spec.columns if not c.stretch)
        stretchers = [c for c in spec.columns if c.stretch]
        spare = max(80.0, available - fixed - 12)
        return [
            (spare / len(stretchers)) if column.stretch else column.width
            for column in spec.columns
        ]

    def _category_colour(self, record) -> str | None:
        """The colour a bill's category carries in the ribbon, reused in the table."""
        if not hasattr(record, "category"):
            return self.palette.accent
        order = list(charts._folded(self.bills))
        if record.category in order:
            return self.palette.series[order.index(record.category) % len(self.palette.series)]
        return self.palette.ink_muted

    def _draw_scrollbar(
        self, frame: Image.Image, area: Rect, count: int, capacity: int, scroll: int
    ) -> None:
        track = Rect(area.right - 4, area.y, 3, area.h)
        paint.rounded_rect(frame, track.box, 2, fill=self.palette.inset)
        span = max(24, area.h * capacity / count)
        travel = (area.h - span) * (scroll / max(1, count - capacity))
        paint.rounded_rect(frame, (track.x, area.y + travel, track.right,
                                   area.y + travel + span), 2,
                           fill=paint.rgba(self.palette.ink_muted, 150))

    def _draw_charts(self, frame: Image.Image) -> None:
        palette, layout = self.palette, self.layout
        bars = layout["bars"]
        # Say so when the chart is showing only the top of the list, rather
        # than letting a truncated view pass for the whole picture.
        capacity = charts.bar_capacity(int(bars.h))
        caption = "BILLS BY AMOUNT"
        if len(self.bills) > capacity:
            caption += f"   ·   TOP {capacity} OF {len(self.bills)}"
        paint.draw_text(frame, (layout["bars_title"].x, layout["bars_title"].y),
                        caption, TYPE["eyebrow"], palette.ink_muted)
        image = self._chart("bars", bars.size,
                            lambda: charts.bills_by_amount(bars.size, self.bills, palette))
        frame.alpha_composite(image, (int(bars.x), int(bars.y)))

        paint.draw_text(frame, (layout["ribbon_title"].x, layout["ribbon_title"].y),
                        "WHERE IT GOES", TYPE["eyebrow"], palette.ink_muted)
        ribbon = layout["ribbon"]
        if self.bills:
            image = self._chart("ribbon", ribbon.size,
                                lambda: charts.category_ribbon(ribbon.size, self.bills,
                                                               palette))
            frame.alpha_composite(image, (int(ribbon.x), int(ribbon.y)))
        else:
            paint.draw_text(frame, (ribbon.x, ribbon.y + 6),
                            "Categories appear once you add bills", TYPE["small"],
                            palette.ink_muted)

    def _draw_dropdown(self, frame: Image.Image, size: tuple[int, int]) -> None:
        key = self.open_dropdown
        spec_field = next((f for f in self.ledger.spec.fields if f.key == key), None)
        if spec_field is None:
            return
        anchor = self.layout[f"field:{key}"]
        height = len(spec_field.options) * 30 + 12
        top = max(SPACE["page"], anchor.y - height - 6)
        rect = Rect(anchor.x, top, anchor.w, height)
        ui.dropdown_list(frame, size, rect, list(spec_field.options),
                         self.ledger.variables[key].get(), self.palette, self.hits,
                         key, self.hover)

    def _place_entries(self, frame: Image.Image) -> None:
        """Float the real inputs over their painted wells.

        Each entry's background is sampled from the finished frame so the caret
        sits in a field that matches the paint exactly.
        """
        # Entries are real widgets stacked above the canvas, so nothing painted
        # can cover them. While an overlay is up they are withdrawn entirely -
        # otherwise they punch through the scrim and the open dropdown.
        if self.open_dropdown or self.pending:
            for entry in self.entries.values():
                entry.place_forget()
            return

        wanted = {"search": self.layout["search"]}
        for spec_field in self.ledger.spec.fields:
            if spec_field.kind != "choice":
                wanted[f"{self.active}:{spec_field.key}"] = \
                    self.layout[f"field:{spec_field.key}"]

        for key, entry in self.entries.items():
            if key not in wanted:
                entry.place_forget()

        for key, rect in wanted.items():
            entry = self.entry_for(key)
            sample = frame.getpixel((int(rect.x + 12), int(rect.centre[1])))[:3]
            # The hint lives in the widget itself, so grey it out while shown -
            # and refresh it here, so it always names the tab on screen however
            # the tab was changed.
            placeholder = key == "search" and self.search_hint_shown
            if placeholder and entry.get() != self._search_hint():
                entry.delete(0, "end")
                entry.insert(0, self._search_hint())
            entry.configure(
                bg="#%02x%02x%02x" % sample,
                fg=self.palette.ink_muted if placeholder else self.palette.ink,
                insertbackground=self.palette.accent,
                selectbackground=self.palette.accent,
                selectforeground=self.palette.button_ink,
            )
            entry.place(x=int(rect.x) + 12, y=int(rect.y) + 9,
                        width=int(rect.w) - 24, height=int(rect.h) - 18)


# ------------------------------------------------------------------- specs


def _bills_spec() -> LedgerSpec:
    return LedgerSpec(
        tab="Bills",
        noun="bill",
        add_label="Add a bill",
        empty_headline="No bills yet",
        empty_detail="Add your first one below",
        export_name="bills.csv",
        default_sort="due",
        columns=(
            Column("bill", 0, "l", "dot", lambda b: b.name.casefold(),
                   lambda b: b.name, stretch=True),
            Column("due", 74, "c", "muted", lambda b: b.due_day,
                   lambda b: _ordinal(b.due_day)),
            Column("amount", 116, "r", "figure", lambda b: b.amount,
                   lambda b: _money(b.amount)),
        ),
        fields=(
            Field("name", "Name"),
            Field("amount", "Amount ($)"),
            Field("category", "Category", "choice", CATEGORIES[-1], CATEGORIES),
            Field("due_day", "Due day", default="1"),
        ),
        search_text=lambda b: f"{b.name} {b.category} {_ordinal(b.due_day)}",
        build=lambda v: Bill.parse(v["name"], v["amount"], v["category"], v["due_day"]),
        to_values=lambda b: {"name": b.name, "amount": f"{b.amount:.2f}",
                             "category": b.category, "due_day": str(b.due_day)},
    )


def _income_spec() -> LedgerSpec:
    return LedgerSpec(
        tab="Income",
        noun="income source",
        add_label="Add income",
        empty_headline="No income yet",
        empty_detail="Add a source to see what's left over",
        export_name="income.csv",
        default_sort="paid",
        columns=(
            Column("source", 0, "l", "dot", lambda i: i.name.casefold(),
                   lambda i: i.name, stretch=True),
            Column("paid", 74, "c", "muted", lambda i: i.pay_day,
                   lambda i: _ordinal(i.pay_day)),
            Column("amount", 116, "r", "figure", lambda i: i.amount,
                   lambda i: _money(i.amount)),
        ),
        fields=(
            Field("name", "Source"),
            Field("amount", "Amount ($)"),
            Field("pay_day", "Paid on", default="1"),
        ),
        search_text=lambda i: f"{i.name} {_ordinal(i.pay_day)}",
        build=lambda v: Income.parse(v["name"], v["amount"], v["pay_day"]),
        to_values=lambda i: {"name": i.name, "amount": f"{i.amount:.2f}",
                             "pay_day": str(i.pay_day)},
    )


# ----------------------------------------------------------------- helpers


def _money(value: float) -> str:
    """Currency with the sign outside the symbol: -$920.73, not $-920.73."""
    return f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"


def _ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


def _when(days: int) -> str:
    return "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"


def create_app(bills_repo=None, income_repo=None) -> tuple[tk.Tk, App]:
    """Build the window and return it with the app, ready for `mainloop()`."""
    root = tk.Tk()
    root.title("Ledger · Monthly Budget")
    root.geometry(f"{WINDOW_SIZE[0]}x{WINDOW_SIZE[1]}")
    root.minsize(*MIN_SIZE)
    root.configure(bg=theme.DARK.plane_top)

    app = App(root, bills_repo, income_repo)

    root.update_idletasks()
    x = (root.winfo_screenwidth() - WINDOW_SIZE[0]) // 2
    y = max(0, (root.winfo_screenheight() - WINDOW_SIZE[1]) // 2)
    root.geometry(f"{WINDOW_SIZE[0]}x{WINDOW_SIZE[1]}+{x}+{y}")
    return root, app
