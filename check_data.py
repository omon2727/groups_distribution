"""
Проверка данных приложения. Запуск из папки приложения:  python check_data.py
Ничего не меняет, только печатает отчёт:
- повторы в groups.txt;
- слова, где кириллица смешана с латиницей («Прокладкa» с латинской «a»);
- группы из памяти, которых нет в groups.txt (опечатки/устаревшие названия);
- состояние правил, модели и файла памяти.
"""
import json
import re
from collections import Counter
from pathlib import Path

from app_config import load_config
from matcher import SmartMatcher

LAT = re.compile(r"[a-z]", re.I)
CYR = re.compile(r"[а-яё]", re.I)


def main():
    cfg = load_config()
    gp = Path(cfg["groups_file"])
    if not gp.exists():
        print(f"НЕТ файла групп: {gp}")
        return
    lines = [ln.strip() for ln in gp.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    groups = list(dict.fromkeys(lines))
    print(f"Групп в файле: {len(lines)} строк, {len(groups)} уникальных")

    dups = [(g, n) for g, n in Counter(lines).items() if n > 1]
    print(f"\nПовторы ({len(dups)}):" if dups else "\nПовторов нет")
    for g, n in dups:
        print(f"  {g!r} x{n}")

    homo = []
    for g in groups:
        for w in re.findall(r"[а-яёa-z]+", g, re.I):
            if CYR.search(w) and LAT.search(w):
                homo.append((g, w))
    print(f"\nСлова со смесью кириллицы и латиницы ({len(homo)}):" if homo else "\nСмешанных слов нет")
    for g, w in homo:
        bad = "".join(c for c in w if LAT.match(c))
        print(f"  {w!r} (латинские буквы: {bad!r}) в «{g}»")

    mem_path = Path("matching_memory.json")
    if mem_path.exists():
        try:
            hist = json.loads(mem_path.read_text(encoding="utf-8")).get("history", {})
            missing = Counter(v for v in hist.values() if v not in set(groups))
            print(f"\nПамять: {len(hist)} записей, групп: {len(set(hist.values()))}")
            print(f"Группы из памяти, которых нет в groups.txt ({len(missing)}):" if missing
                  else "Все группы из памяти есть в groups.txt")
            for g, n in missing.most_common(20):
                print(f"  {g!r}: {n} записей")
        except Exception as e:
            print(f"\nПАМЯТЬ ПОВРЕЖДЕНА: {e}  (есть ли matching_memory.json.bak?)")
    else:
        print("\nФайла памяти нет")

    print(f"\nПравил в настройках: {len(cfg['rules'])}" + ("  (пусто — стартовые правила НЕ действуют)" if not cfg["rules"] else ""))
    m = SmartMatcher(groups, memory_file="/nonexistent.json", rules=[])
    if m.model:
        print(f"Модель ранжирования: загружена (версия {m.model.get('version')}, T={m.model.get('T'):.3f})")
    else:
        print("Модель ранжирования: НЕ загружена (используются запасные веса) — запустите train_model.py")


if __name__ == "__main__":
    main()
