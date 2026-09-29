"""
Точка входа. Запуск: python fill_groups_gui.py
- matcher.py          — алгоритм сопоставления и правила
- app_config.py       — настройки (app_settings.json), стартовые правила
- settings_window.py  — окно «Настройки» (в т.ч. вкладка «Правила»)
- app.py              — главное окно
"""

from app import App

if __name__ == "__main__":
    app = App()
    app.mainloop()