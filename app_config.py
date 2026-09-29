"""
Хранение настроек приложения (app_settings.json): столбцы, путь к файлу групп,
обобщённые группы, интервал автосохранения и ПРАВИЛА сопоставления.
"""

import json
from pathlib import Path

CONFIG_PATH = Path("app_settings.json")

# Стартовые правила — взяты из вашей ручной разметки второй пачки (match_log).
# Действуют, пока в app_settings.json нет собственного списка "rules";
# редактируются на вкладке «Правила» в настройках.
DEFAULT_RULES = [
    {"pattern": "подшипник трансмиссии", "group": "Подшипник первичного вала", "regex": False},
    {"pattern": r"^сальник(?:\s+[a-z]+)*$", "group": "Сальник коленвала", "regex": True},
    {"pattern": r"^крестовина кардана\b(?!.*рулев)", "group": "Муфта кардана", "regex": True},
    {"pattern": "тяга реактивная", "group": "Рычаги", "regex": False},
    {"pattern": "тяга рулевая", "group": "Рычаги", "regex": False},
    {"pattern": r"^помпа$", "group": "Водяной насос / Помпа", "regex": True},
]

DEFAULTS = {
    "col_name": "Имя артикула",
    "col_group": "Название группы",
    "col_brand": "Имя бренда",
    "col_article": "Артикул на сайте",
    "groups_file": "groups.txt",
    "generic_groups": [
        "Соединители", "Болты, винты", "Гайки, шайбы", "Втулки",
        "Кольца уплотнительные", "Стопорные кольца", "Кронштейны", "Заглушки",
        "Клипсы, фиксаторы, зажимы",
    ],
    "generic_margin": 1.5,
    "autosave_interval_minutes": 30,
    "rules": DEFAULT_RULES,
}


def _copy_default(key):
    value = DEFAULTS[key]
    if isinstance(value, list):
        return [dict(v) if isinstance(v, dict) else v for v in value]
    return value


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Сохранённые значения поверх значений по умолчанию."""
    data = {key: _copy_default(key) for key in DEFAULTS}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for key in DEFAULTS:
                    if key in saved:
                        data[key] = saved[key]
        except Exception:
            pass
    return data


def save_config(data: dict, path: Path = CONFIG_PATH) -> bool:
    """Сохраняет только известные ключи (см. DEFAULTS)."""
    try:
        payload = {key: data.get(key, DEFAULTS[key]) for key in DEFAULTS}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False