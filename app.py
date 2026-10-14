import tkinter as tk
import webbrowser
from tkinter import ttk, filedialog, messagebox, simpledialog
import pandas as pd
from pathlib import Path
import re
from datetime import datetime
from urllib.parse import quote
import json
import sys
import ctypes

from matcher import SmartMatcher
from settings_window import SettingsWindow
import app_config


# ====================== ПРИЛОЖЕНИЕ ======================
def _set_windows_app_user_model_id():
    """До создания окна. Иначе панель задач показывает иконку python.exe, а не logo.ico."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Remzona.AutoGroups")
    except Exception:
        pass

class App(tk.Tk):
    HELP_TEXT = (
        "РАБОТА С ТАБЛИЦЕЙ\n\n"
        "• Двойной клик по «Альтернативы» — быстрый выбор одной из топ-3 предложенных групп кнопками.\n"
        "• Двойной клик по «Итоговая группа» — полный поиск по всем группам (набирайте название, "
        "стрелками/Enter выбирайте вариант).\n"
        "• Наведите курсор на любую ячейку и подождите — появится всплывающая подсказка с полным "
        "текстом, если значение не помещается в столбец.\n\n"
        "ВЫДЕЛЕНИЕ НЕСКОЛЬКИХ СТРОК\n\n"
        "• Ctrl+клик / Shift+клик — выделить несколько строк.\n"
        "• Ctrl+A — выделить все видимые строки.\n"
        "• ПКМ по выделенным строкам → «Назначить группу выделенным строкам…» — один выбор группы "
        "сразу для всех.\n"
        "• Ctrl+D (или ПКМ → «Протянуть группу вниз») — скопировать группу из первой выделенной "
        "строки на остальные выделенные, как протягивание уголка ячейки в Excel.\n"
        "• ПКМ → «Назначить эту группу всем строкам с таким же названием» — группа из первой "
        "выделенной строки проставляется ВСЕМ строкам таблицы с тем же очищенным названием "
        "(например, 25 одинаковых «Сальник!»), в том числе скрытым фильтром.\n\n"
        "ПРАВИЛА\n\n"
        "• Настройки → вкладка «Правила»: «фраза → группа» (или регулярка → группа). Правило "
        "срабатывает до скоринга и даёт 100% уверенность. Порядок решения: точное совпадение из "
        "памяти → правила сверху вниз → скоринг.\n"
        "• ПКМ → «Создать правило из строки…» — быстро сделать правило из названия и итоговой "
        "группы выделенной строки (фразу можно отредактировать, например укоротить).\n"
        "• В логе сопоставления столбец «Источник» показывает history / rule / scoring / none.\n\n"
        "УВЕРЕННОСТЬ\n\n"
        "Для скоринга уверенность калибруется по отрыву лучшей группы от второй: если конкуренты "
        "почти равны, уверенность занижается, и строка попадает в фильтр «Низкая уверенность». "
        "100% выдаётся только для истории и правил.\n\n"
        "ГОРЯЧИЕ КЛАВИШИ (работают в любой раскладке — EN и RU)\n\n"
        "• Ctrl+C — копировать содержимое ячейки (той колонки, по которой был последний клик).\n"
        "• Ctrl+V / Ctrl+X — вставить / вырезать текст в полях ввода.\n"
        "• Ctrl+A — выделить всё (в полях ввода — весь текст, в таблице — все строки).\n"
        "• Ctrl+Z — отменить последнее назначение группы (для памяти матчера отмена не действует — "
        "уже выученные слова придётся переучить, назначив группу заново).\n\n"
        "ОБУЧЕНИЕ\n\n"
        "Каждое ручное назначение группы запоминается: и точная фраза, и отдельные слова вместе с "
        "группой, к которой их отнесли. Со временем подсказки становятся точнее без изменения кода."
    )

    # Ctrl+буква определяется по event.keycode (виртуальный код физической клавиши на
    # Windows), поэтому работает одинаково в EN и RU раскладках.
    # Максимальная длина названия группы в столбце «Альтернативы» (длиннее — «…»).
    # Полный текст виден во всплывающей подсказке и при копировании ячейки.
    ALT_NAME_MAX = 14

    CTRL_VK_CODES = {"c": 67, "v": 86, "x": 88, "z": 90, "d": 68, "a": 65,
                     "backspace": 8, "delete": 46}

    def _make_ctrl_dispatcher(self, mapping: dict):
        code_map = {self.CTRL_VK_CODES[letter]: fn for letter, fn in mapping.items()}

        def handler(event):
            fn = code_map.get(event.keycode)
            if fn is None:
                return None
            return fn(event) or "break"

        return handler

    # ---------- копирование/вставка/вырезание/выделение в полях ввода ----------
    def _entry_copy(self, event):
        widget = event.widget
        try:
            text = widget.selection_get() if widget.selection_present() else widget.get()
        except tk.TclError:
            return None
        self.clipboard_clear()
        self.clipboard_append(text)
        return "break"

    def _entry_cut(self, event):
        widget = event.widget
        try:
            if widget.selection_present():
                text = widget.selection_get()
                self.clipboard_clear()
                self.clipboard_append(text)
                widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        return "break"

    def _entry_paste(self, event):
        widget = event.widget
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return "break"
        # Из Excel в буфер приходит завершающий "\r\n" — для однострочного поля
        # берём только первую строку без пробелов по краям.
        try:
            is_single_line = widget.winfo_class() in ("Entry", "TEntry")
        except tk.TclError:
            is_single_line = True
        if is_single_line:
            text = text.splitlines()[0].strip() if text.splitlines() else text.strip()
        try:
            if widget.selection_present():
                widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        widget.insert("insert", text)
        return "break"

    def _entry_select_all(self, event):
        widget = event.widget
        try:
            widget.selection_range(0, "end")
            widget.icursor("end")
        except tk.TclError:
            pass
        return "break"

    def _entry_delete_word_back(self, event):
        """Ctrl+Backspace: удалить слово слева от курсора (или выделенный фрагмент)."""
        widget = event.widget
        try:
            if widget.selection_present():
                widget.delete("sel.first", "sel.last")
                return "break"
            pos = widget.index("insert")
            before = widget.get()[:pos]
            m = re.search(r"(\w+|[^\w\s]+)\s*$|\s+$", before)
            start = m.start() if m else 0
            if start < pos:
                widget.delete(start, pos)
        except tk.TclError:
            pass
        return "break"

    def _entry_delete_word_forward(self, event):
        """Ctrl+Delete: удалить слово справа от курсора (или выделенный фрагмент)."""
        widget = event.widget
        try:
            if widget.selection_present():
                widget.delete("sel.first", "sel.last")
                return "break"
            pos = widget.index("insert")
            after = widget.get()[pos:]
            m = re.match(r"\s*(\w+|[^\w\s]+)?\s*", after)
            end = pos + (m.end() if m else 0)
            if end > pos:
                widget.delete(pos, end)
        except tk.TclError:
            pass
        return "break"

    def setup_universal_clipboard(self):
        entry_dispatch = self._make_ctrl_dispatcher({
            "c": self._entry_copy, "v": self._entry_paste, "x": self._entry_cut,
            "a": self._entry_select_all,
            "backspace": self._entry_delete_word_back,
            "delete": self._entry_delete_word_forward,
        })
        for widget_class in ("TEntry", "Entry"):
            self.bind_class(widget_class, "<Control-Key>", entry_dispatch)
            # Ctrl+Backspace / Ctrl+Delete привязываем отдельно и явно: у Tk есть своя, более
            # специфичная привязка <Key-BackSpace>, она перебивала общий <Control-Key>
            # и удаляла только один символ.
            self.bind_class(widget_class, "<Control-BackSpace>", self._entry_delete_word_back)
            self.bind_class(widget_class, "<Control-Delete>", self._entry_delete_word_forward)

    def select_all_rows(self, event=None):
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)
        return "break"

    def __init__(self):
        _set_windows_app_user_model_id()
        super().__init__()
        self.title("Автораспределение групп Remzona")
        # Не больше экрана (на небольших мониторах 900x760 не помещается с панелью задач).
        win_w = min(900, max(500, self.winfo_screenwidth() - 40))
        win_h = min(760, max(300, self.winfo_screenheight() - 100))
        self.geometry(f"{win_w}x{win_h}")
        self.minsize(500, 300)
        self._sep_drag = False
        self._drag_col = None
        self._col_fit_busy = False
        self._col_fit_width = None
        self._set_window_icon()

        config = app_config.load_config()
        for key in ("col_name", "col_group", "col_brand", "col_article", "groups_file"):
            if isinstance(config.get(key), str):
                config[key] = config[key].strip()

        self.input_file = tk.StringVar()
        self.output_file = tk.StringVar()
        self.groups_file = tk.StringVar(value=config["groups_file"])
        self.col_name = tk.StringVar(value=config["col_name"])
        self.col_group = tk.StringVar(value=config["col_group"])
        self.col_brand = tk.StringVar(value=config["col_brand"])
        self.col_article = tk.StringVar(value=config["col_article"])

        self.generic_groups = list(config["generic_groups"])
        self.generic_margin_var = tk.StringVar(value=str(config["generic_margin"]))
        # Правила «фраза/регулярка -> группа» (редактируются в настройках)
        self.rules = [dict(r) for r in config.get("rules", []) if isinstance(r, dict)]

        self.df = None
        self.groups = []
        self.matcher = None
        self.current_combo = None
        self.match_log = []
        self.top_matches = {}
        self.last_click = {"item": None, "column": "#5"}
        self.undo_stack = []
        self._assign_counter = {}
        self._alt_full = {}

        self.settings_window = None

        self._tooltip_window = None
        self._tooltip_after_id = None
        self._tooltip_cell = None

        # Автосохранение: копии лога и памяти в папке с ротацией (до 10 файлов каждого вида)
        self.autosave_dir = Path("remzona_groups_logs")
        self.autosave_max_files = 10
        self.autosave_interval_var = tk.StringVar(value=str(config["autosave_interval_minutes"]))
        self.autosave_status_var = tk.StringVar(value="Автосохранение: ещё не запускалось")
        self._autosave_after_id = None

        self.create_widgets()
        self.setup_universal_clipboard()
        self.try_load_default_groups()
        self.schedule_autosave()
        self.after_idle(self._fit_columns)
        self.after_idle(self._set_window_icon)


    # ====================== НАСТРОЙКИ ======================
    def save_settings_to_disk(self):
        for var in (self.col_name, self.col_group, self.col_brand, self.col_article, self.groups_file):
            cleaned = var.get().strip()
            if cleaned != var.get():
                var.set(cleaned)

        try:
            generic_margin = float(self.generic_margin_var.get().replace(",", "."))
        except ValueError:
            generic_margin = SmartMatcher.GENERIC_MARGIN
            self.generic_margin_var.set(str(generic_margin))
        try:
            autosave_minutes = float(self.autosave_interval_var.get().replace(",", "."))
        except ValueError:
            autosave_minutes = 30.0
            self.autosave_interval_var.set("30")

        app_config.save_config({
            "col_name": self.col_name.get(),
            "col_group": self.col_group.get(),
            "col_brand": self.col_brand.get(),
            "col_article": self.col_article.get(),
            "groups_file": self.groups_file.get(),
            "generic_groups": list(self.generic_groups),
            "generic_margin": generic_margin,
            "autosave_interval_minutes": autosave_minutes,
            "rules": list(self.rules),
        })

    def open_settings(self, initial_tab=None):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            if initial_tab:
                self.settings_window.select_tab(initial_tab)
            return
        self.settings_window = SettingsWindow(self, initial_tab=initial_tab)

    def open_help(self):
        self.open_settings(initial_tab="Подсказка")

    def _make_matcher(self):
        """Создаёт SmartMatcher с текущими настройками и правилами."""
        try:
            generic_margin = float(self.generic_margin_var.get().replace(",", "."))
        except ValueError:
            generic_margin = SmartMatcher.GENERIC_MARGIN
            self.generic_margin_var.set(str(generic_margin))
        matcher = SmartMatcher(
            self.groups, generic_groups=set(self.generic_groups),
            generic_margin=generic_margin, rules=self.rules,
        )
        if getattr(matcher, "memory_error", None):
            messagebox.showwarning("Память сопоставления", matcher.memory_error)
        return matcher

    def _ensure_matcher(self):
        if self.matcher is None:
            self.matcher = self._make_matcher()
        return self.matcher

    def _set_window_icon(self):
        """Иконка в заголовке окна и в панели задач.
        В собранном exe (PyInstaller) logo.ico не лежит рядом с программой: он вшит в exe как
        иконка файла, а во время работы Tk показывает своё «перо». Поэтому:
        1) ищем logo.ico в папке распаковки (_MEIPASS), рядом с exe, рядом с app.py, в cwd;
        2) если не нашли — берём иконку прямо из самого exe через WinAPI."""
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "logo.ico")
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve().parent / "logo.ico")
        try:
            candidates.append(Path(__file__).resolve().parent / "logo.ico")
        except NameError:
            pass
        candidates.append(Path.cwd() / "logo.ico")
        icon = next((p for p in candidates if p.is_file()), None)
        if icon is not None:
            path = icon.resolve().as_posix()
            try:
                self.iconbitmap(default=path)
                self.iconbitmap(path)
                return
            except tk.TclError:
                pass
        self._set_icon_from_exe()

    def _set_icon_from_exe(self):
        """Запасной вариант для Windows: иконка из ресурсов самого exe (той, что видна у файла)."""
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            return
        try:
            from ctypes import wintypes
            large = wintypes.HICON()
            small = wintypes.HICON()
            n = ctypes.windll.shell32.ExtractIconExW(
                sys.executable, 0, ctypes.byref(large), ctypes.byref(small), 1)
            if n <= 0:
                return
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
            if small.value:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
            if large.value:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, large)
        except Exception:
            pass

    def create_widgets(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        frm_files = ttk.LabelFrame(top, text="Файлы", padding=6)
        frm_files.pack(fill="x", pady=(0, 6))

        ttk.Label(frm_files, text="Excel с запчастями:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_files, textvariable=self.input_file, width=58).grid(row=0, column=1, padx=4)
        ttk.Button(frm_files, text="Обзор…", command=self.browse_excel).grid(row=0, column=2)

        ttk.Label(frm_files, text="Результат:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_files, textvariable=self.output_file, width=58).grid(row=1, column=1, padx=4)
        ttk.Button(frm_files, text="Обзор…", command=self.browse_output).grid(row=1, column=2)

        frm_btn = ttk.Frame(top)
        frm_btn.pack(fill="x", pady=6)

        ttk.Button(frm_btn, text="1. Загрузить и сопоставить", command=self.load_and_match).pack(side="left", padx=(0, 8))
        ttk.Button(frm_btn, text="2. Сохранить результат", command=self.save_result).pack(side="left", padx=(0, 8))
        ttk.Button(frm_btn, text="3. Сохранить лог сопоставления", command=self.save_match_log).pack(side="left", padx=(0, 8))
        ttk.Button(frm_btn, text="⚙ Настройки", command=self.open_settings).pack(side="right")
        ttk.Button(frm_btn, text="❓ Подсказка", command=self.open_help).pack(side="right", padx=(0, 8))

        self.status = ttk.Label(top, text="Готов к работе", foreground="gray")
        self.status.pack(fill="x", pady=(4, 0))

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Bordered.Treeview", rowheight=26, bordercolor="#b0b0b0", borderwidth=1,
            relief="solid", fieldbackground="white",
        )
        style.configure(
            "Bordered.Treeview.Heading", bordercolor="#808080", borderwidth=1,
            relief="solid", font=("Segoe UI", 9, "bold"),
        )
        self._row_stripe_colors = ("#ffffff", "#f2f2f2")

        frm_table = ttk.LabelFrame(
            self, text="Ручная проверка (подробности — кнопка «❓ Подсказка»)", padding=6,
        )
        frm_table.pack(fill="both", expand=True, padx=8, pady=6)

        columns = ("idx", "name", "suggested", "alt", "final", "google", "remzona")
        self.tree = ttk.Treeview(frm_table, columns=columns, show="headings",
                                  selectmode="extended", style="Bordered.Treeview")
        self.tree.tag_configure("even", background=self._row_stripe_colors[0])
        self.tree.tag_configure("odd", background=self._row_stripe_colors[1])

        self.tree.heading("idx", text="№")
        self.tree.heading("name", text="Название запчасти")
        self.tree.heading("suggested", text="Предложение")
        self.tree.heading("alt", text="Альтернативы (топ-3)")
        self.tree.heading("final", text="Итоговая группа")
        self.tree.heading("google", text="Google")
        self.tree.heading("remzona", text="Remzona")

        # Все столбцы масштабируются вместе с окном: свободная ширина делится пропорционально
        # "base" (поэтому главные столбцы — название, предложение, альтернативы — шире всех,
        # а №, Google и Remzona минимальные), но не меньше "min".
        self._column_specs = (
            {"id": "idx", "base": 30, "min": 20, "anchor": "center"},
            {"id": "name", "base": 340, "min": 80, "anchor": "w"},
            {"id": "suggested", "base": 230, "min": 60, "anchor": "w"},
            {"id": "alt", "base": 340, "min": 70, "anchor": "w"},
            {"id": "final", "base": 230, "min": 50, "anchor": "w"},
            {"id": "google", "base": 30, "min": 20, "anchor": "center"},
            {"id": "remzona", "base": 30, "min": 20, "anchor": "center"},
        )
        for spec in self._column_specs:
            self.tree.column(
                spec["id"], width=spec["base"], minwidth=spec["min"],
                anchor=spec["anchor"], stretch=False,
            )

        vsb = ttk.Scrollbar(frm_table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frm_table.grid_rowconfigure(0, weight=1)
        frm_table.grid_columnconfigure(0, weight=1)

        self.tree.bind("<Configure>", self._fit_columns)
        # Ручное растягивание границ столбцов: после отпускания мыши раскладка
        # нормализуется, чтобы столбцы не выходили за край таблицы.
        self.tree.bind("<B1-Motion>", self._on_tree_drag, add="+")
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release, add="+")

        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Button-1>", self.on_tree_click)
        # ВАЖНО: с add="+", иначе привязка <Button-1> выше затёрла бы эту.
        self.tree.bind("<Button-1>", self._on_tree_press, add="+")
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", self._hide_cell_tooltip)
        tree_dispatch = self._make_ctrl_dispatcher({
            "c": self.copy_cell, "d": self.fill_down_selection,
            "a": self.select_all_rows, "z": self.undo_last,
        })
        self.tree.bind("<Control-Key>", tree_dispatch)
        # Раньше Ctrl+Z висел через bind_all и срабатывал даже когда фокус
        # был в поле ввода. Теперь — только на самой таблице.

        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Копировать ячейку (Ctrl+C)", command=self.copy_cell)
        self.context_menu.add_command(label="Копировать строку", command=self.copy_row)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Выделить все строки (Ctrl+A)", command=self.select_all_rows)
        self.context_menu.add_command(label="Очистить группу", command=self.clear_group)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Назначить группу выделенным строкам…", command=self.assign_group_to_selection)
        self.context_menu.add_command(label="Протянуть группу вниз (из первой строки, Ctrl+D)", command=self.fill_down_selection)
        self.context_menu.add_command(label="Назначить эту группу всем строкам с таким же названием", command=self.apply_to_same_name)
        self.context_menu.add_command(label="Создать правило из строки…", command=self.create_rule_from_row)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Отменить последнее действие (Ctrl+Z)", command=self.undo_last)

        frm_filter = ttk.Frame(self)
        frm_filter.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(frm_filter, text="Показать все", command=lambda: self.fill_tree("all")).pack(side="left", padx=2)
        ttk.Button(frm_filter, text="Только с предложением", command=lambda: self.fill_tree("suggested")).pack(side="left", padx=2)
        ttk.Button(frm_filter, text="Только без группы", command=lambda: self.fill_tree("empty")).pack(side="left", padx=2)
        ttk.Button(frm_filter, text="Низкая уверенность", command=lambda: self.fill_tree("low")).pack(side="left", padx=2)


    def _on_tree_press(self, event):
        self._sep_drag = self.tree.identify_region(event.x, event.y) == "separator"
        self._drag_col = None
        if self._sep_drag:
            # Какой столбец тянут: тот, чей правый край ближе всего к указателю.
            edge, best = 0, None
            for i, spec in enumerate(self._column_specs):
                edge += int(self.tree.column(spec["id"], "width"))
                dist = abs(edge - event.x)
                if best is None or dist < best[0]:
                    best = (dist, i)
            self._drag_col = best[1] if best else None

    def _on_tree_drag(self, event):
        """Во время перетаскивания границы: если таблица стала шире окна, столбцы справа
        от тянущегося сжимаются (не ниже минимума), а не уезжают за край."""
        if not self._sep_drag or self._drag_col is None:
            return
        self.after_idle(self._rebalance_after_drag)

    def _rebalance_after_drag(self):
        specs = self._column_specs
        tree = self.tree
        try:
            avail = tree.winfo_width() - 4
            widths = [int(tree.column(s["id"], "width")) for s in specs]
        except tk.TclError:
            return
        overflow = sum(widths) - avail
        if overflow <= 0:
            return
        k = self._drag_col
        # 1) сжимаем столбцы справа пропорционально их запасу над минимумом
        for _ in range(6):
            room = [max(0, widths[i] - specs[i]["min"]) for i in range(k + 1, len(specs))]
            pool = sum(room)
            if overflow <= 0 or pool <= 0:
                break
            for j, r in enumerate(room):
                if r <= 0 or overflow <= 0:
                    continue
                cut = min(r, overflow, max(1, round(overflow * r / pool)))
                widths[k + 1 + j] -= cut
                overflow -= cut
        # 2) если справа сжимать уже некуда — ограничиваем сам тянущийся столбец
        if overflow > 0:
            widths[k] = max(specs[k]["min"], widths[k] - overflow)
        for spec, w in zip(specs, widths):
            tree.column(spec["id"], width=int(w), minwidth=min(spec["min"], int(w)))

    def _on_tree_release(self, event):
        """Пользователь потянул границу столбца: запоминаем его ширины как новые пропорции
        и тут же вписываем все столбцы в видимую ширину таблицы."""
        if not self._sep_drag:
            return
        self._sep_drag = False
        self._drag_col = None
        for spec in self._column_specs:
            spec["base"] = max(spec["min"], int(self.tree.column(spec["id"], "width")))
        self.after_idle(lambda: self._fit_columns(force=True))

    def _fit_columns(self, event=None, force=False):
        """Раскладывает ширину таблицы по столбцам пропорционально их базовой ширине.
        Все столбцы сжимаются и растягиваются вместе с окном; ниже "min" не опускаются.
        Сумма ширин всегда равна ширине таблицы, поэтому столбцы не уходят за её край."""
        specs = getattr(self, "_column_specs", None)
        tree = getattr(self, "tree", None)
        if not specs or tree is None or self._col_fit_busy:
            return
        if event is not None and event.widget is not tree:
            return
        raw = tree.winfo_width() if event is None else event.width
        if raw < 80 or (raw == self._col_fit_width and not force):
            return
        avail = raw - 4

        # Пропорциональное деление с учётом минимумов: столбцы, которым по доле досталось
        # меньше минимума, фиксируются на минимуме, остаток делится между остальными.
        widths = {}
        pending = list(specs)
        free = avail
        while pending:
            total = sum(s["base"] for s in pending)
            clamped = [s for s in pending if free * s["base"] / total < s["min"]]
            if not clamped:
                break
            for s in clamped:
                widths[s["id"]] = s["min"]
                free -= s["min"]
                pending.remove(s)
        if pending:
            total = sum(s["base"] for s in pending)
            used = 0
            for s in pending[:-1]:
                w = int(free * s["base"] / total)
                widths[s["id"]] = w
                used += w
            widths[pending[-1]["id"]] = max(pending[-1]["min"], free - used)

        self._col_fit_busy = True
        self._col_fit_width = raw
        try:
            for spec in specs:
                width = int(widths[spec["id"]])
                tree.column(
                    spec["id"],
                    width=width,
                    minwidth=min(spec["min"], width),
                    anchor=spec["anchor"],
                    stretch=False,
                )
        finally:
            self._col_fit_busy = False

    def browse_excel(self):
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")])
        if path:
            self.input_file.set(path)
            p = Path(path)
            self.output_file.set(str(p.with_name(p.stem + "_проверено.xlsx")))

    def browse_output(self):
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if path:
            self.output_file.set(path)

    def try_load_default_groups(self):
        if Path(self.groups_file.get()).exists():
            self.load_groups_from_file(self.groups_file.get())

    def load_groups_from_file(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                # dict.fromkeys убирает повторы (в списке групп они бывают) и сохраняет порядок
                self.groups = list(dict.fromkeys(line.strip() for line in f if line.strip()))
            # Группы поменялись — старый matcher со стеммами/IDF от прежних групп
            # больше не подходит. Пересоздадим его при следующем обращении.
            self.matcher = None
            self.top_matches = {}
            self.match_log = []
            self.status.config(text=f"Загружено групп: {len(self.groups)}", foreground="blue")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def load_and_match(self):
        if not self.input_file.get() or not Path(self.input_file.get()).exists():
            messagebox.showwarning("Нет файла", "Выберите Excel-файл")
            return
        if not self.groups:
            self.load_groups_from_file(self.groups_file.get())
            if not self.groups:
                messagebox.showwarning("Нет групп", "Укажите файл групп (кнопка «⚙ Настройки»)")
                return
        try:
            self.status.config(text="Читаю Excel…", foreground="blue")
            self.update()
            self.df = pd.read_excel(self.input_file.get())
            col_name = self.col_name.get().strip()
            if col_name not in self.df.columns:
                messagebox.showerror("Ошибка", f"Столбец «{col_name}» не найден")
                return
            self.status.config(text="Сопоставляю (с учётом памяти и правил)…", foreground="blue")
            self.update()

            self.matcher = self._make_matcher()
            suggestions = []
            confidences = []
            self.match_log = []
            self.top_matches = {}
            for i, name in enumerate(self.df[col_name]):
                group, conf, dbg = self.matcher.find_best_group(name, return_debug=True, top_n=3)
                suggestions.append(group)
                confidences.append(conf)
                self.top_matches[i] = dbg["top"]
                top2 = dbg["top"][1] if len(dbg["top"]) > 1 else ("", 0.0)
                top3 = dbg["top"][2] if len(dbg["top"]) > 2 else ("", 0.0)
                self.match_log.append({
                    "№": i + 1,
                    "Название": name,
                    "Очищенное_название": dbg["cleaned"],
                    "Главное_слово": dbg["head_word"],
                    "Предложенная_группа": group or "",
                    "Уверенность": round(conf, 3),
                    "Топ2_группа": top2[0],
                    "Топ2_уверенность": round(top2[1], 3),
                    "Топ3_группа": top3[0],
                    "Топ3_уверенность": round(top3[1], 3),
                    "Источник": dbg["source"] or "",
                    "Правило": dbg.get("rule", ""),
                    "Совпавшие_слова": ", ".join(dbg["matched_words"]),
                    "База_IDF": dbg["base"],
                    "Покрытие_группы": dbg["coverage"],
                    "Бонус_главного_слова": dbg["head_bonus"],
                    "Бонус_из_памяти": dbg["learned_bonus"],
                    "Итоговый_score": dbg["score"],
                    "Отрыв_от_второй": dbg.get("gap", 0.0),
                    "Отдано_обобщённой_группе": dbg["generic_override"],
                    "Уверенность_до_калибровки": dbg.get("confidence_raw", 0.0),
                })
            self.df["_suggested"] = suggestions
            self.df["_confidence"] = confidences
            self.df[self.col_group.get()] = suggestions
            self.fill_tree("all")
            filled = sum(1 for s in suggestions if s)
            self.status.config(text=f"Готово. Предложено: {filled} из {len(self.df)}. Исправления запоминаются.", foreground="green")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def extract_article(self, name: str) -> str:
        """Резервный способ вытащить артикул из названия."""
        if not isinstance(name, str):
            return ""
        left = name.split("!")[0].strip() if "!" in name else name
        candidates = re.findall(r'[A-Z0-9\-_\.]{4,}', left.upper())
        if candidates:
            return max(candidates, key=len)
        return left.strip()[:50]

    def get_col_value(self, idx: int, col_var: tk.StringVar) -> str:
        col = col_var.get().strip()
        if not col or self.df is None or col not in self.df.columns:
            return ""
        value = str(self.df.at[idx, col]).strip()
        if value.lower() in ["nan", "none", ""]:
            return ""
        return value

    def on_tree_click(self, event):
        """Запоминаем ячейку под курсором (для копирования) и обрабатываем клики по Google / Remzona."""
        self._hide_cell_tooltip()
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        column = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item:
            return

        self.last_click = {"item": item, "column": column}
        self.tree.focus_set()

        idx = int(item)
        col_name = self.col_name.get().strip()
        full_name = str(self.df.at[idx, col_name])

        brand = self.get_col_value(idx, self.col_brand)
        article = self.get_col_value(idx, self.col_article)

        if column == "#6":
            if brand and article:
                query = f"{brand} {article}"
            elif article:
                query = article
            elif brand:
                query = brand
            else:
                query = self.extract_article(full_name) or full_name[:80]
            webbrowser.open(f"https://www.google.com/search?q={quote(query)}")

        elif column == "#7":
            webbrowser.open(f"https://www.google.com/search?q={quote('remzona ' + full_name)}")

    def _short_alt_name(self, group: str) -> str:
        """Сокращённое название группы для столбца «Альтернативы»."""
        limit = self.ALT_NAME_MAX
        return group if len(group) <= limit else group[:limit - 1].rstrip() + "…"

    def fill_tree(self, filter_mode="all"):
        self.tree.delete(*self.tree.get_children())
        if self.df is None:
            return

        col_name = self.col_name.get().strip()
        col_group = self.col_group.get()

        for idx, row in self.df.iterrows():
            name = str(row[col_name])[:110]
            suggested = row.get("_suggested") or ""
            final = row.get(col_group) or ""
            conf = row.get("_confidence", 0)

            top = self.top_matches.get(idx, [])
            alt_full = "  |  ".join(f"{g} ({c*100:.0f}%)" for g, c in top[1:3] if g)
            self._alt_full[str(idx)] = alt_full
            alt_text = " | ".join(
                f"{self._short_alt_name(g)} {c*100:.0f}%" for g, c in top[1:3] if g)

            show = (filter_mode == "all" or
                    (filter_mode == "suggested" and suggested) or
                    (filter_mode == "empty" and not final) or
                    (filter_mode == "low" and conf < 0.55))

            if show:
                stripe = "even" if len(self.tree.get_children()) % 2 == 0 else "odd"
                self.tree.insert("", "end", iid=str(idx), tags=(stripe,),
                                 values=(idx + 1, name, suggested, alt_text, final, "🔍 Google", "🔍 Remzona"))

    # ====================== ВСПЛЫВАЮЩАЯ ПОДСКАЗКА ======================
    def _on_tree_motion(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            self._hide_cell_tooltip()
            return

        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item:
            self._hide_cell_tooltip()
            return

        cell_key = (item, column)
        if cell_key == self._tooltip_cell:
            return

        self._hide_cell_tooltip()
        text = self.get_cell_text(item, column)
        if not text:
            return

        self._tooltip_cell = cell_key
        x, y = event.x_root, event.y_root
        self._tooltip_after_id = self.after(400, lambda: self._display_tooltip(x, y, text))

    def _display_tooltip(self, x, y, text):
        self._tooltip_after_id = None
        tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        try:
            tw.attributes("-topmost", True)
        except tk.TclError:
            pass
        tw.wm_geometry(f"+{x + 14}+{y + 18}")
        label = tk.Label(
            tw, text=text, justify="left", background="#ffffe0",
            relief="solid", borderwidth=1, font=("Segoe UI", 9),
            wraplength=650, padx=6, pady=4,
        )
        label.pack()
        self._tooltip_window = tw

    def _hide_cell_tooltip(self, event=None):
        if self._tooltip_after_id is not None:
            try:
                self.after_cancel(self._tooltip_after_id)
            except Exception:
                pass
            self._tooltip_after_id = None
        if self._tooltip_window is not None:
            try:
                self._tooltip_window.destroy()
            except Exception:
                pass
            self._tooltip_window = None
        self._tooltip_cell = None

    # ====================== ВЫБОР ГРУППЫ ======================
    def on_double_click(self, event):
        self._hide_cell_tooltip()
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item:
            return
        if column == "#5":
            self.open_search_popup([item])
        elif column == "#4":
            idx = int(item)
            top = self.top_matches.get(idx, [])
            if not top:
                self.open_search_popup([item])
                return
            self.open_topn_popup(item, top)

    def open_topn_popup(self, item, top_list):
        """Быстрый выбор одной из топ-3 групп кнопками."""
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.title("Быстрый выбор группы")
        popup.transient(self)

        ttk.Label(popup, text="Выберите подходящую группу из предложенных:",
                  font=("Segoe UI", 10)).pack(pady=(10, 6), padx=12, anchor="w")

        def pick(group):
            self._push_undo([item])
            self.tree.set(item, "final", group)
            idx = int(item)
            self.df.at[idx, self.col_group.get()] = group
            if self.matcher:
                original_name = self.df.at[idx, self.col_name.get()]
                self.matcher.remember(original_name, group)
            self.status.config(text=f"Назначена группа «{group}»", foreground="green")
            popup.destroy()

        for group, conf in top_list:
            ttk.Button(
                popup, text=f"{group}   ({conf*100:.0f}%)",
                command=lambda g=group: pick(g),
            ).pack(fill="x", padx=12, pady=3)

        ttk.Separator(popup, orient="horizontal").pack(fill="x", padx=12, pady=(8, 6))
        ttk.Button(
            popup, text="Другой вариант… (поиск по всем группам)",
            command=lambda: (popup.destroy(), self.open_search_popup([item])),
        ).pack(fill="x", padx=12, pady=(0, 10))

        popup.update_idletasks()
        w = 440
        h = 90 + 40 * len(top_list) + 70
        x = (popup.winfo_screenwidth() // 2) - (w // 2)
        y = (popup.winfo_screenheight() // 2) - (h // 2)
        popup.geometry(f"{w}x{h}+{x}+{y}")

        popup.bind("<Escape>", lambda e: popup.destroy())

        popup.deiconify()
        popup.grab_set()
        popup.focus_force()

    def open_search_popup(self, items):
        """Поиск группы (Entry + Listbox). items — id строк treeview, которым назначается группа."""
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.title("Выбор группы" if len(items) == 1 else f"Выбор группы для {len(items)} строк")
        popup.transient(self)

        label_text = "Начните вводить название группы:" if len(items) == 1 else \
            f"Группа будет назначена {len(items)} выделенным строкам. Начните вводить название группы:"
        ttk.Label(popup, text=label_text, font=("Segoe UI", 10)).pack(pady=(10, 5), padx=10, anchor="w")

        search_var = tk.StringVar(value="")
        entry = ttk.Entry(popup, textvariable=search_var, font=("Segoe UI", 11))
        entry.pack(fill="x", padx=10, pady=(0, 8))

        frame_list = ttk.Frame(popup)
        frame_list.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        listbox = tk.Listbox(frame_list, font=("Segoe UI", 10), activestyle="dotbox", selectmode="browse")
        scrollbar = ttk.Scrollbar(frame_list, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)

        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def update_list(*args):
            typed = search_var.get().strip().lower()
            listbox.delete(0, tk.END)
            for g in self.groups:
                if not typed or typed in g.lower():
                    listbox.insert(tk.END, g)
            if listbox.size() > 0:
                listbox.selection_set(0)
                listbox.activate(0)

        search_var.trace_add("write", update_list)
        update_list()

        btn_frame = ttk.Frame(popup)
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        def select_and_close(event=None):
            selection = listbox.curselection()
            value = listbox.get(selection[0]) if selection else search_var.get().strip()

            self._push_undo(items)
            for item in items:
                self.tree.set(item, "final", value)
                idx = int(item)
                self.df.at[idx, self.col_group.get()] = value if value else None

                if value and self.matcher:
                    original_name = self.df.at[idx, self.col_name.get()]
                    self.matcher.remember(original_name, value)

            # Подсказка о создании правила: если одну и ту же пару
            # «очищенное название → группа» назначили уже 3 раза.
            if value and items:
                idx0 = int(items[0])
                first_name = self.df.at[idx0, self.col_name.get()]
                matcher = self._ensure_matcher()
                clean_name = matcher._clean(first_name) or ""
                if not clean_name:
                    clean_name = "<пусто>"
                key = (clean_name, value)
                self._assign_counter[key] = self._assign_counter.get(key, 0) + 1
                if self._assign_counter[key] == 3:
                    self.status.config(
                        text=f"Повтор №3: возможно, стоит создать правило "
                             f"«{clean_name} → {value}» (ПКМ → «Создать правило из строки»).",
                        foreground="orange",
                    )
                else:
                    self.status.config(
                        text=f"Группа «{value}» назначена {len(items)} строкам" if len(items) > 1
                             else f"Назначена группа «{value}»",
                        foreground="green",
                    )

            popup.destroy()

        def on_escape(event=None):
            popup.destroy()

        listbox.bind("<Double-Button-1>", select_and_close)
        listbox.bind("<Return>", select_and_close)
        entry.bind("<Return>", select_and_close)
        entry.bind("<Down>", lambda e: listbox.focus_set())
        popup.bind("<Escape>", on_escape)

        ttk.Button(btn_frame, text="Выбрать", command=select_and_close).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Отмена", command=on_escape).pack(side="left")

        popup.update_idletasks()
        w, h = 520, 420
        x = (popup.winfo_screenwidth() // 2) - (w // 2)
        y = (popup.winfo_screenheight() // 2) - (h // 2)
        popup.geometry(f"{w}x{h}+{x}+{y}")

        popup.deiconify()
        popup.grab_set()
        popup.focus_force()
        entry.focus_set()

    # ====================== МАССОВЫЕ ОПЕРАЦИИ ======================
    def assign_group_to_selection(self):
        sel = list(self.tree.selection())
        if not sel:
            messagebox.showinfo("Нет выделения", "Сначала выделите одну или несколько строк (Ctrl/Shift+клик или Ctrl+A)")
            return
        self.open_search_popup(sel)

    def fill_down_selection(self, event=None):
        """Копирует группу из первой (верхней) выделенной строки на остальные выделенные."""
        sel = set(self.tree.selection())
        if len(sel) < 2:
            messagebox.showinfo("Мало строк", "Выделите минимум 2 строки (первая — с уже заполненной группой)")
            return "break"
        ordered = [i for i in self.tree.get_children() if i in sel]
        source_value = self.tree.set(ordered[0], "final")
        if not source_value:
            messagebox.showinfo("Нет группы", "В первой (верхней) выделенной строке не указана итоговая группа")
            return "break"

        self._push_undo(ordered[1:])
        for item in ordered[1:]:
            self.tree.set(item, "final", source_value)
            idx = int(item)
            self.df.at[idx, self.col_group.get()] = source_value
            if self.matcher:
                original_name = self.df.at[idx, self.col_name.get()]
                self.matcher.remember(original_name, source_value)

        self.status.config(text=f"Группа «{source_value}» протянута на {len(ordered) - 1} строк", foreground="green")
        return "break"

    def _first_selected_with_group(self):
        """Возвращает (item, название, группа) для первой выделенной строки с итоговой
        группой или None (с сообщением пользователю)."""
        if self.df is None:
            return None
        sel = set(self.tree.selection())
        if not sel:
            messagebox.showinfo("Нет выделения", "Сначала выделите строку")
            return None
        item = next(i for i in self.tree.get_children() if i in sel)
        group = self.tree.set(item, "final")
        if not group:
            messagebox.showinfo("Нет группы", "У выделенной строки не указана итоговая группа")
            return None
        name = self.df.at[int(item), self.col_name.get().strip()]
        return item, name, group

    def apply_to_same_name(self):
        """Проставляет группу выделенной строки ВСЕМ строкам таблицы с тем же
        очищенным названием (в том числе скрытым текущим фильтром)."""
        found = self._first_selected_with_group()
        if not found:
            return
        item, name, group = found
        matcher = self._ensure_matcher()
        key = matcher._clean(name)
        if not key:
            return

        col_name = self.col_name.get().strip()
        col_group = self.col_group.get()
        same_idx = [i for i in self.df.index if matcher._clean(self.df.at[i, col_name]) == key]

        visible = [str(i) for i in same_idx if self.tree.exists(str(i))]
        self._push_undo(visible)
        changed = 0
        for i in same_idx:
            if self.df.at[i, col_group] != group:
                changed += 1
            self.df.at[i, col_group] = group
            if self.tree.exists(str(i)):
                self.tree.set(str(i), "final", group)
        matcher.remember(name, group)
        self.status.config(
            text=f"Группа «{group}» проставлена {len(same_idx)} строкам с названием «{key}» (изменено: {changed})",
            foreground="green",
        )

    def create_rule_from_row(self):
        """Создаёт правило «фраза → группа» из выделенной строки; фразу можно отредактировать."""
        found = self._first_selected_with_group()
        if not found:
            return
        item, name, group = found
        matcher = self._ensure_matcher()
        default_phrase = matcher._clean(name)
        phrase = simpledialog.askstring(
            "Новое правило",
            f"Фраза для правила (ищется в очищенном названии по границам слов).\n"
            f"Всё, что содержит эту фразу, будет уходить в группу:\n«{group}»",
            initialvalue=default_phrase, parent=self,
        )
        if phrase is None or not phrase.strip():
            return
        self.rules.append({"pattern": phrase.strip(), "group": group, "regex": False})
        matcher.set_rules(self.rules)
        self.save_settings_to_disk()
        self.status.config(text=f"Правило добавлено: «{phrase.strip()}» → {group}. "
                                f"Сработает при следующем сопоставлении.", foreground="green")

    # ====================== ОТМЕНА (Ctrl+Z) ======================
    def _push_undo(self, items):
        snapshot = [(item, int(item), self.tree.set(item, "final")) for item in items]
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)

    def undo_last(self, event=None):
        """Откатывает последнее назначение группы (память матчера не откатывается)."""
        if not self.undo_stack:
            self.status.config(text="Нет действий для отмены", foreground="gray")
            return "break"
        snapshot = self.undo_stack.pop()
        restored = 0
        for item, idx, old_value in snapshot:
            if not self.tree.exists(item):
                continue
            self.tree.set(item, "final", old_value)
            if self.df is not None:
                self.df.at[idx, self.col_group.get()] = old_value if old_value else None
            restored += 1
        self.status.config(
            text=f"Отменено: восстановлено {restored} строк. "
                 f"(Слова, уже попавшие в память из этого действия, останутся — переучите при необходимости.)",
            foreground="orange",
        )
        return "break"

    def show_context_menu(self, event):
        self._hide_cell_tooltip()
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if item:
            if item not in self.tree.selection():
                self.tree.selection_set(item)
            self.last_click = {"item": item, "column": column}
            self.tree.focus_set()
        self.context_menu.post(event.x_root, event.y_root)

    # ====================== КОПИРОВАНИЕ ЯЧЕЕК ======================
    def get_cell_text(self, item, column) -> str:
        if self.df is None:
            return ""
        idx = int(item)
        col_name = self.col_name.get().strip()
        if column == "#1":
            return str(idx + 1)
        elif column == "#2":
            return str(self.df.at[idx, col_name])
        elif column == "#3":
            return self.tree.set(item, "suggested")
        elif column == "#4":
            return self._alt_full.get(str(item)) or self.tree.set(item, "alt")
        elif column == "#5":
            return self.tree.set(item, "final")
        elif column in ("#6", "#7"):
            return str(self.df.at[idx, col_name])
        return ""

    def copy_cell(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        column = self.last_click.get("column") or "#5"
        values = [self.get_cell_text(item, column) for item in sel]
        values = [v for v in values if v]
        if not values:
            return
        text = "\n".join(values)
        self.clipboard_clear()
        self.clipboard_append(text)
        preview = values[0] if len(values) == 1 else f"{len(values)} значений"
        preview = preview if len(preview) <= 60 else preview[:60] + "…"
        self.status.config(text=f"Скопировано: {preview}", foreground="blue")

    def copy_row(self):
        sel = self.tree.selection()
        if not sel:
            return
        lines = []
        for item in sel:
            values = self.tree.item(item, "values")
            lines.append("\t".join(map(str, values)))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))

    def clear_group(self):
        sel = self.tree.selection()
        if not sel:
            return
        self._push_undo(sel)
        for item in sel:
            self.tree.set(item, "final", "")
            self.df.at[int(item), self.col_group.get()] = None

    def save_result(self):
        if self.df is None:
            messagebox.showwarning("Нет данных", "Сначала загрузите данные")
            return
        path = self.output_file.get()
        if not path:
            messagebox.showwarning("Нет пути", "Укажите файл результата")
            return
        try:
            cols = [c for c in ["_suggested", "_confidence"] if c in self.df.columns]
            self.df.drop(columns=cols).to_excel(path, index=False)
            filled = self.df[self.col_group.get()].notna().sum()
            messagebox.showinfo("Сохранено", f"Файл сохранён\nЗаполнено: {filled}")
            self.status.config(text=f"Сохранено → {Path(path).name}", foreground="green")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    # ====================== АВТОСОХРАНЕНИЕ ======================
    def _get_autosave_interval_ms(self) -> int:
        try:
            minutes = float(self.autosave_interval_var.get().replace(",", "."))
            if minutes <= 0:
                raise ValueError
        except ValueError:
            minutes = 30.0
            self.autosave_interval_var.set("30")
        return max(int(minutes * 60 * 1000), 10_000)

    def schedule_autosave(self):
        if self._autosave_after_id is not None:
            try:
                self.after_cancel(self._autosave_after_id)
            except Exception:
                pass
        self._autosave_after_id = self.after(self._get_autosave_interval_ms(), self.autosave_tick)

    def _prune_old_backups(self, pattern: str):
        try:
            files = sorted(self.autosave_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            return
        for old_file in files[self.autosave_max_files:]:
            try:
                old_file.unlink()
            except Exception:
                pass

    def autosave_tick(self, event=None):
        """Периодически (и по кнопке «Сохранить сейчас») сохраняет копию лога и памяти
        матчера в папку remzona_groups_logs (до 10 файлов каждого вида)."""
        saved_something = False
        try:
            self.autosave_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            if self.matcher is not None:
                mem_path = self.autosave_dir / f"matching_memory_{ts}.json"
                try:
                    with open(mem_path, "w", encoding="utf-8") as f:
                        json.dump(
                            {"history": self.matcher.history, "learned": self.matcher.learned},
                            f, ensure_ascii=False, indent=2,
                        )
                    saved_something = True
                except Exception:
                    pass
                self._prune_old_backups("matching_memory_*.json")

            if self.match_log:
                log_path = self.autosave_dir / f"match_log_{ts}.xlsx"
                try:
                    log_df = pd.DataFrame(self.match_log)
                    if self.df is not None and self.col_group.get() in self.df.columns:
                        log_df["Итоговая_группа_после_правок"] = self.df[self.col_group.get()].fillna("").values
                    log_df.to_excel(log_path, index=False)
                    saved_something = True
                except Exception:
                    pass
                self._prune_old_backups("match_log_*.xlsx")

            now_str = datetime.now().strftime("%H:%M:%S")
            if saved_something:
                self.autosave_status_var.set(f"Автосохранение: {now_str} (папка: {self.autosave_dir}/)")
            else:
                self.autosave_status_var.set(f"Автосохранение: {now_str} (пока нечего сохранять)")
        except Exception as e:
            self.autosave_status_var.set(f"Автосохранение: ошибка ({e})")
        finally:
            self.schedule_autosave()

    def save_match_log(self):
        """Сохраняет подробный лог сопоставления в Excel."""
        if not self.match_log:
            messagebox.showwarning("Нет данных", "Сначала выполните шаг 1 (Загрузить и сопоставить)")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")], initialfile="match_log.xlsx",
        )
        if not path:
            return
        try:
            log_df = pd.DataFrame(self.match_log)
            if self.df is not None and self.col_group.get() in self.df.columns:
                log_df["Итоговая_группа_после_правок"] = self.df[self.col_group.get()].fillna("").values
            log_df.to_excel(path, index=False)
            messagebox.showinfo("Сохранено", f"Лог сопоставления сохранён:\n{path}\n\nМожно отправить этот файл для дальнейшей наладки алгоритма.")
            self.status.config(text=f"Лог сохранён → {Path(path).name}", foreground="green")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def import_corrections(self):
        """Обучает память на Excel-файле с парами «Название — Группа»
        (например, на сохранённом логе с заполненным столбцом «Итоговая_группа_после_правок»)."""
        if not self.groups:
            self.load_groups_from_file(self.groups_file.get())
            if not self.groups:
                messagebox.showwarning("Нет групп", "Сначала укажите и загрузите файл групп")
                return

        path = filedialog.askopenfilename(
            title="Файл с исправлениями (лог или результат)",
            filetypes=[("Excel", "*.xlsx *.xls")],
        )
        if not path:
            return

        try:
            corr_df = pd.read_excel(path)
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
            return

        # Файл аудита памяти (audit_memory.py): «Название (очищенное)» + «Верная_группа».
        name_col_candidates = [self.col_name.get(), "Название", "Название запчасти",
                               "Название (очищенное)"]
        group_col_candidates = [
            "Верная_группа", "Итоговая_группа_после_правок", self.col_group.get(), "Группа",
            "Итоговая группа",
        ]
        name_col = next((c for c in name_col_candidates if c in corr_df.columns), None)
        group_col = next((c for c in group_col_candidates if c in corr_df.columns), None)

        if not name_col or not group_col:
            messagebox.showerror(
                "Не найдены столбцы",
                "Не удалось найти нужные столбцы в файле.\n"
                f"Ожидались столбец названия ({', '.join(name_col_candidates)}) "
                f"и столбец группы ({', '.join(group_col_candidates)}).\n\n"
                f"В файле есть: {', '.join(map(str, corr_df.columns))}"
            )
            return

        matcher = self._ensure_matcher()

        # В логе итоговая группа у НЕисправленных строк просто равна предложенной программой.
        # Если такие строки запомнить, то ошибочные предложения превращаются в «подтверждённые»
        # и портят последующие подсказки. Поэтому по умолчанию учим только исправленные строки
        # (и строки, которые программа не смогла определить).
        skip_unchanged = False
        proposed_col = "Предложенная_группа" if "Предложенная_группа" in corr_df.columns else None
        if proposed_col and group_col == "Итоговая_группа_после_правок":
            answer = messagebox.askyesnocancel(
                "Какие строки запоминать?",
                "В файле есть и предложенная программой группа, и итоговая.\n\n"
                "Да — запомнить только строки, которые вы ИСПРАВИЛИ (рекомендуется: "
                "непроверенные предложения не попадут в память).\n"
                "Нет — запомнить все строки, включая принятые без правок.\n"
                "Отмена — ничего не делать."
            )
            if answer is None:
                return
            skip_unchanged = bool(answer)

        # Для файла аудита: группа вписана вручную, опечатка попала бы в память как «истина»,
        # поэтому принимаем только группы из загруженного списка (без учёта регистра/пробелов).
        audit_mode = group_col == "Верная_группа"
        known_groups = {re.sub(r"\s+", " ", g).strip().lower(): g for g in self.groups}
        unknown = []

        learned_count = 0
        skipped_count = 0
        for _, row in corr_df.iterrows():
            name = row.get(name_col)
            group = row.get(group_col)
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(group, str) or not group.strip():
                continue
            if skip_unchanged:
                proposed = row.get(proposed_col)
                if isinstance(proposed, str) and proposed.strip() == group.strip():
                    skipped_count += 1
                    continue
            group = group.strip()
            if audit_mode:
                canonical = known_groups.get(re.sub(r"\s+", " ", group).lower())
                if canonical is None:
                    unknown.append(group)
                    continue
                group = canonical
            matcher.remember(name, group)
            learned_count += 1

        unknown_note = ""
        if unknown:
            uniq = sorted(set(unknown))
            unknown_note = (f"\nНе найдено в списке групп (строки пропущены, {len(unknown)} шт.): "
                            + ", ".join(uniq[:5]) + (" …" if len(uniq) > 5 else "") + "\n")

        messagebox.showinfo(
            "Готово",
            f"Обучено на {learned_count} исправлениях из файла:\n{Path(path).name}\n"
            f"Пропущено непроверенных строк: {skipped_count}\n{unknown_note}\n"
            f"Память сохранена в {matcher.memory_file} — теперь эти соответствия "
            f"будут узнаваться сразу (со 100% уверенностью), а похожие слова "
            f"будут сильнее тянуть к тем же группам."
        )
        self.status.config(text=f"Память обучена: +{learned_count} исправлений из файла", foreground="green")