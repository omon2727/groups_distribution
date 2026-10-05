"""Проверка матчера на вашей истории (leave-one-out): каждая строка проверяется на памяти
БЕЗ неё самой. Запуск из папки приложения: python check_matcher_loo.py
Важно: история состоит в основном из строк, которые вы ИСПРАВЛЯЛИ (трудные случаи), поэтому
цифра ниже, чем будет на обычном файле."""
import json, sys
sys.path.insert(0, ".")
import matcher as M

groups = list(dict.fromkeys(l.strip() for l in open("groups.txt", encoding="utf-8") if l.strip()))
st = json.load(open("app_settings.json", encoding="utf-8"))
hist = json.load(open("matching_memory.json", encoding="utf-8"))["history"]
m = M.SmartMatcher(groups, memory_file="/nonexistent.json",
                   generic_groups=set(st["generic_groups"]), generic_margin=st["generic_margin"], rules=[])
m.load_history(dict(hist))
rows = []
for k, g in list(m.history.items()):
    m.unremember_key(k)
    gg, c = m.find_best_group(k)
    m.restore_key(k, g)
    rows.append((k, g, gg, c))
ok = sum(r[1] == r[2] for r in rows)
print(f"LOO acc {ok}/{len(rows)}={ok/len(rows):.3f}")
for lo, hi in [(0, .55), (.55, .75), (.75, .9), (.9, .97), (.97, 1.01)]:
    s = [r for r in rows if lo <= r[3] < hi]
    if s:
        print(f"conf[{lo},{hi}): n={len(s)} acc={sum(r[1]==r[2] for r in s)/len(s):.2f}")
input("Для продолжения нажмите Enter...")