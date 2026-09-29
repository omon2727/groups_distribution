"""
Окно "Настройки": столбцы Excel, путь к файлу групп, обобщённые группы,
ПРАВИЛА сопоставления, автосохранение и обучение по файлу исправлений.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


class SettingsWindow(tk.Toplevel):
    def __init__(self, app, initial_tab=None):
        super().__init__(app)
        self.app = app
        self.withdraw()
        self.title("Настройки")
        self.transient(app)
        self.resizable(True, True)

        # Рабочая копия правил: применяется к приложению только при закрытии окна
        self.rules_data = [dict(r) for r in app.rules]

        self._build_widgets()
        if initial_tab:
            self.select_tab(initial_tab)

        self.update_idletasks()
        w, h = 700, 620
        x = (self.winfo_screenwidth() // 2) - (w // 2)
        y = (self.winfo_screenheight() // 2) - (h // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(600, 500)

        self.deiconify()
        self.grab_set()
        self.focus_force()
        self.protocol("WM_DELETE_WINDOW", self._save_and_close)

    def select_tab(self, tab_name: str):
        for tab_id in self.notebook.tabs():
            if self.notebook.tab(tab_id, "text") == tab_name:
                self.notebook.select(tab_id)
                return

    def _build_widgets(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        self.notebook = nb

        self._build_columns_tab(nb)
        self._build_generic_groups_tab(nb)
        self._build_rules_tab(nb)
        self._build_autosave_tab(nb)
        self._build_help_tab(nb)

        btn_bottom = ttk.Frame(self)
        btn_bottom.pack(fill="x", padx=10, pady=10)
        ttk.Button(btn_bottom, text="Сохранить и закрыть", command=self._save_and_close).pack(side="right")

    # ------------------------------------------------------------------
    def _build_columns_tab(self, nb):
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="Столбцы и файл групп")

        ttk.Label(tab, text="Названия столбцов в Excel:", font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        for row, (label, var) in enumerate([
            ("Столбец названий:", self.app.col_name),
            ("Столбец группы:", self.app.col_group),
            ("Столбец бренда:", self.app.col_brand),
            ("Столбец артикула:", self.app.col_article),
        ], start=1):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(tab, textvariable=var, width=32).grid(row=row, column=1, sticky="we", pady=3)

        ttk.Separator(tab, orient="horizontal").grid(row=5, column=0, columnspan=3, sticky="we", pady=12)

        ttk.Label(tab, text="Файл групп (TXT):", font=("Segoe UI", 9, "bold")).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Entry(tab, textvariable=self.app.groups_file, width=32).grid(row=7, column=1, sticky="we", pady=3)
        ttk.Button(tab, text="Обзор…", command=self._browse_groups).grid(row=7, column=2, padx=(6, 0))
        ttk.Label(tab, text="Загружается автоматически при запуске программы.",
                  foreground="gray").grid(row=8, column=1, columnspan=2, sticky="w", pady=(4, 0))

        tab.grid_columnconfigure(1, weight=1)

    def _browse_groups(self):
        path = filedialog.askopenfilename(filetypes=[("Text", "*.txt")])
        if path:
            self.app.groups_file.set(path)
            self.app.load_groups_from_file(path)

    # ------------------------------------------------------------------
    def _build_generic_groups_tab(self, nb):
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="Обобщённые группы")

        ttk.Label(
            tab,
            text=("Эти группы побеждают при близком счёте для мелких унифицированных\n"
                  "запчастей (болты, втулки, кольца и т.п.) — настраивается один раз."),
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        frame_list = ttk.Frame(tab)
        frame_list.pack(fill="both", expand=True)

        self.generic_listbox = tk.Listbox(frame_list, font=("Segoe UI", 10), activestyle="dotbox")
        scrollbar = ttk.Scrollbar(frame_list, orient="vertical", command=self.generic_listbox.yview)
        self.generic_listbox.configure(yscrollcommand=scrollbar.set)
        self.generic_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for g in self.app.generic_groups:
            self.generic_listbox.insert(tk.END, g)

        btns = ttk.Frame(tab)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Добавить…", command=self._add_generic_group).pack(side="left")
        ttk.Button(btns, text="Удалить выбранную", command=self._remove_generic_group).pack(side="left", padx=(8, 0))

        margin_frame = ttk.Frame(tab)
        margin_frame.pack(fill="x", pady=(14, 0))
        ttk.Label(margin_frame,
                  text="Порог отрыва (больше — обобщённые группы побеждают реже):").pack(side="left")
        ttk.Entry(margin_frame, textvariable=self.app.generic_margin_var, width=6).pack(side="left", padx=6)

    def _add_generic_group(self):
        if not self.app.groups:
            messagebox.showinfo("Список групп пуст",
                                "Сначала укажите и загрузите файл групп на вкладке «Столбцы и файл групп»")
            return
        existing = set(self.generic_listbox.get(0, tk.END))
        self._pick_group_popup(lambda g: self.generic_listbox.insert(tk.END, g), exclude=existing)

    def _remove_generic_group(self):
        sel = self.generic_listbox.curselection()
        if sel:
            self.generic_listbox.delete(sel[0])

    # ------------------------------------------------------------------
    def _build_rules_tab(self, nb):
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="Правила")

        ttk.Label(
            tab,
            text=("Правило «фраза → группа» срабатывает ДО скоринга и даёт 100% уверенность.\n"
                  "Порядок: точное совпадение из памяти → правила (сверху вниз) → скоринг.\n"
                  "Фраза ищется в очищенном названии по границам слов; режим «регулярка» — "
                  "регулярное выражение по очищенному названию (нижний регистр, без цифр и знаков)."),
            justify="left", wraplength=640,
        ).pack(anchor="w", pady=(0, 8))

        frame_list = ttk.Frame(tab)
        frame_list.pack(fill="both", expand=True)
        self.rules_listbox = tk.Listbox(frame_list, font=("Segoe UI", 10), activestyle="dotbox",
                                        selectmode="browse")
        sb = ttk.Scrollbar(frame_list, orient="vertical", command=self.rules_listbox.yview)
        self.rules_listbox.configure(yscrollcommand=sb.set)
        self.rules_listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.rules_listbox.bind("<Double-Button-1>", lambda e: self._edit_rule())
        self._refresh_rules_list()

        btns = ttk.Frame(tab)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Добавить…", command=self._add_rule).pack(side="left")
        ttk.Button(btns, text="Изменить…", command=self._edit_rule).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Удалить", command=self._remove_rule).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Вверх", command=lambda: self._move_rule(-1)).pack(side="left", padx=(16, 0))
        ttk.Button(btns, text="Вниз", command=lambda: self._move_rule(1)).pack(side="left", padx=(8, 0))

        test = ttk.LabelFrame(tab, text="Проверка правил", padding=6)
        test.pack(fill="x", pady=(10, 0))
        self.rule_test_var = tk.StringVar()
        self.rule_test_result = tk.StringVar(value="")
        row = ttk.Frame(test)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.rule_test_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Проверить название", command=self._test_rules).pack(side="left", padx=(6, 0))
        ttk.Label(test, textvariable=self.rule_test_result, foreground="gray",
                  wraplength=620, justify="left").pack(anchor="w", pady=(4, 0))

    def _rule_label(self, r):
        mode = "regex" if r.get("regex") else "фраза"
        return f"[{mode}]  {r.get('pattern', '')}   →   {r.get('group', '')}"

    def _refresh_rules_list(self, select=None):
        self.rules_listbox.delete(0, tk.END)
        for r in self.rules_data:
            self.rules_listbox.insert(tk.END, self._rule_label(r))
        if select is not None and 0 <= select < len(self.rules_data):
            self.rules_listbox.selection_set(select)
            self.rules_listbox.see(select)

    def _add_rule(self):
        self._rule_dialog(None)

    def _edit_rule(self):
        sel = self.rules_listbox.curselection()
        if sel:
            self._rule_dialog(sel[0])

    def _remove_rule(self):
        sel = self.rules_listbox.curselection()
        if not sel:
            return
        del self.rules_data[sel[0]]
        self._refresh_rules_list(min(sel[0], len(self.rules_data) - 1))

    def _move_rule(self, delta):
        sel = self.rules_listbox.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + delta
        if 0 <= j < len(self.rules_data):
            self.rules_data[i], self.rules_data[j] = self.rules_data[j], self.rules_data[i]
            self._refresh_rules_list(j)

    def _rule_dialog(self, index):
        """Диалог добавления/изменения правила."""
        import re
        rule = dict(self.rules_data[index]) if index is not None else {"pattern": "", "group": "", "regex": False}

        dlg = tk.Toplevel(self)
        dlg.withdraw()
        dlg.title("Правило")
        dlg.transient(self)

        pat_var = tk.StringVar(value=rule.get("pattern", ""))
        grp_var = tk.StringVar(value=rule.get("group", ""))
        rx_var = tk.BooleanVar(value=bool(rule.get("regex")))

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Фраза или регулярка:").grid(row=0, column=0, sticky="w")
        ttk.Entry(body, textvariable=pat_var, width=48).grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 8))
        ttk.Checkbutton(body, text="Регулярное выражение", variable=rx_var).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="Группа:").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(body, textvariable=grp_var, width=40, state="readonly").grid(row=4, column=0, sticky="we", pady=(2, 0))
        ttk.Button(body, text="Выбрать…",
                   command=lambda: self._pick_group_popup(grp_var.set, parent=dlg)).grid(row=4, column=1, padx=(6, 0))
        body.grid_columnconfigure(0, weight=1)

        def ok():
            pattern = pat_var.get().strip()
            group = grp_var.get().strip()
            if not pattern or not group:
                messagebox.showwarning("Не заполнено", "Укажите фразу и группу", parent=dlg)
                return
            if rx_var.get():
                try:
                    re.compile(pattern)
                except re.error as e:
                    messagebox.showerror("Ошибка в регулярке", str(e), parent=dlg)
                    return
            new_rule = {"pattern": pattern, "group": group, "regex": bool(rx_var.get())}
            if index is None:
                self.rules_data.append(new_rule)
                self._refresh_rules_list(len(self.rules_data) - 1)
            else:
                self.rules_data[index] = new_rule
                self._refresh_rules_list(index)
            dlg.destroy()

        btns = ttk.Frame(body)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="OK", command=ok).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Отмена", command=dlg.destroy).pack(side="left")
        dlg.bind("<Escape>", lambda e: dlg.destroy())

        dlg.update_idletasks()
        w, h = 460, 250
        x = (dlg.winfo_screenwidth() // 2) - (w // 2)
        y = (dlg.winfo_screenheight() // 2) - (h // 2)
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        dlg.deiconify()
        dlg.grab_set()
        dlg.focus_force()

    def _test_rules(self):
        """Показывает, какое правило (если есть) сработает для введённого названия."""
        from matcher import SmartMatcher
        text = self.rule_test_var.get()
        if not text.strip():
            return
        m = self.app.matcher or SmartMatcher(
            self.app.groups, memory_file="matching_memory.json",
            generic_groups=set(self.app.generic_groups),
        )
        old_rules = list(getattr(m, "_compiled_rules", []))
        m.set_rules(self.rules_data)
        try:
            cleaned = m._clean(text)
            hit = m.match_rule(cleaned)
        finally:
            m._compiled_rules = old_rules
        if hit:
            self.rule_test_result.set(f"Очищенное: «{cleaned}»  →  сработает правило «{hit[1]}» → {hit[0]}")
        else:
            self.rule_test_result.set(f"Очищенное: «{cleaned}»  →  ни одно правило не подходит (пойдёт скоринг)")

    # ------------------------------------------------------------------
    def _pick_group_popup(self, on_pick, exclude=None, parent=None):
        """Мини-поиск по всем группам. Вызывает on_pick(название_группы)."""
        if not self.app.groups:
            messagebox.showinfo("Список групп пуст",
                                "Сначала загрузите файл групп на вкладке «Столбцы и файл групп»",
                                parent=parent or self)
            return
        exclude = exclude or set()
        popup = tk.Toplevel(parent or self)
        popup.withdraw()
        popup.title("Выбор группы")
        popup.transient(parent or self)

        ttk.Label(popup, text="Начните вводить название группы:", font=("Segoe UI", 10)).pack(
            pady=(10, 5), padx=10, anchor="w")
        search_var = tk.StringVar(value="")
        entry = ttk.Entry(popup, textvariable=search_var, font=("Segoe UI", 11))
        entry.pack(fill="x", padx=10, pady=(0, 8))

        frame_list = ttk.Frame(popup)
        frame_list.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        listbox = tk.Listbox(frame_list, font=("Segoe UI", 10), activestyle="dotbox")
        scrollbar = ttk.Scrollbar(frame_list, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def update_list(*args):
            typed = search_var.get().strip().lower()
            listbox.delete(0, tk.END)
            for g in self.app.groups:
                if g in exclude:
                    continue
                if not typed or typed in g.lower():
                    listbox.insert(tk.END, g)
            if listbox.size() > 0:
                listbox.selection_set(0)

        search_var.trace_add("write", update_list)
        update_list()

        def select_and_close(event=None):
            selection = listbox.curselection()
            if not selection:
                return
            value = listbox.get(selection[0])
            popup.destroy()
            on_pick(value)

        listbox.bind("<Double-Button-1>", select_and_close)
        listbox.bind("<Return>", select_and_close)
        entry.bind("<Return>", select_and_close)
        entry.bind("<Down>", lambda e: listbox.focus_set())
        popup.bind("<Escape>", lambda e: popup.destroy())

        popup.update_idletasks()
        w, h = 480, 380
        x = (popup.winfo_screenwidth() // 2) - (w // 2)
        y = (popup.winfo_screenheight() // 2) - (h // 2)
        popup.geometry(f"{w}x{h}+{x}+{y}")
        popup.deiconify()
        popup.grab_set()
        popup.focus_force()
        entry.focus_set()

    # ------------------------------------------------------------------
    def _build_autosave_tab(self, nb):
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="Автосохранение и обучение")

        frm_auto = ttk.LabelFrame(tab, text="Автосохранение", padding=8)
        frm_auto.pack(fill="x", pady=(0, 12))
        row = ttk.Frame(frm_auto)
        row.pack(fill="x")
        ttk.Label(row, text="Сохранять лог и память каждые").pack(side="left")
        ttk.Entry(row, textvariable=self.app.autosave_interval_var, width=5).pack(side="left", padx=4)
        ttk.Label(row, text="мин. в папку «remzona_groups_logs» (до 10 файлов каждого вида).").pack(side="left")

        row2 = ttk.Frame(frm_auto)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Button(row2, text="Сохранить сейчас", command=self.app.autosave_tick).pack(side="left")
        ttk.Label(row2, textvariable=self.app.autosave_status_var, foreground="gray").pack(side="left", padx=(10, 0))

        frm_learn = ttk.LabelFrame(tab, text="Обучение по файлу исправлений", padding=8)
        frm_learn.pack(fill="x", pady=(0, 12))
        ttk.Label(
            frm_learn,
            text=("Загрузить Excel с готовыми парами «Название — Группа» (например, "
                  "сохранённый лог сопоставления с дозаполненными группами) и обучить "
                  "память сразу на всех строках."),
            wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(frm_learn, text="Обучить по файлу исправлений…", command=self.app.import_corrections).pack(anchor="w")

    # ------------------------------------------------------------------
    def _build_help_tab(self, nb):
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="Подсказка")

        frame_text = ttk.Frame(tab)
        frame_text.pack(fill="both", expand=True)
        text_widget = tk.Text(frame_text, wrap="word", font=("Segoe UI", 10),
                              relief="flat", borderwidth=0, padx=4, pady=4)
        scrollbar = ttk.Scrollbar(frame_text, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        text_widget.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        text_widget.insert("1.0", self.app.HELP_TEXT)
        text_widget.configure(state="disabled")

    # ------------------------------------------------------------------
    def _save_and_close(self):
        self.app.generic_groups = list(self.generic_listbox.get(0, tk.END))
        self.app.rules = [dict(r) for r in self.rules_data]
        if self.app.matcher is not None:
            self.app.matcher.set_rules(self.app.rules)
        self.app.save_settings_to_disk()
        self.app.schedule_autosave()
        self.destroy()