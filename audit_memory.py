"""
Аудит памяти сопоставления. Запуск: python audit_memory.py
Для каждой пары «название -> группа» из matching_memory.json убирает её из памяти и смотрит,
что предложил бы алгоритм. Если он уверенно (>= порога) предлагает ДРУГУЮ группу, пара
попадает в memory_audit.xlsx как подозрительная (частая причина — «принятое без проверки»
предложение). Ничего в памяти не меняет. Столбец «Верная_группа» можно заполнить и
импортировать в программе (кнопка импорта исправлений) — тогда память исправится.
"""
import json
import sys
from pathlib import Path

import pandas as pd

from app_config import load_config
from matcher import SmartMatcher

THRESHOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.75


def main():
    cfg = load_config()
    groups_path = Path(cfg["groups_file"])
    groups = [ln.strip() for ln in groups_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    m = SmartMatcher(groups, generic_groups=set(cfg["generic_groups"]),
                     generic_margin=cfg["generic_margin"], rules=cfg["rules"])
    rows = []
    for key, group in list(m.history.items()):
        m.unremember_key(key)
        pred, conf, dbg = m.find_best_group(key, return_debug=True, top_n=1)
        m.restore_key(key, group)
        if pred and pred != group and conf >= THRESHOLD:
            rows.append({
                "Название (очищенное)": key,
                "Группа в памяти": group,
                "Предлагает алгоритм": pred,
                "Уверенность": round(conf, 3),
                "Верная_группа": "",
            })
    rows.sort(key=lambda r: -r["Уверенность"])
    out = Path("memory_audit.xlsx")
    pd.DataFrame(rows).to_excel(out, index=False)
    print(f"Подозрительных пар: {len(rows)} из {len(m.history)} -> {out}")


if __name__ == "__main__":
    main()
