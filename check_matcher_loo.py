"""Проверка матчера на вашей истории (leave-one-out): python check_matcher_loo.py
ВНИМАНИЕ: модель в matcher_model.json обучалась на этой же истории, поэтому цифра здесь
завышена на 4-5 п.п. Честная оценка (кросс-валидация) печатается при запуске train_model.py."""
import json,sys,collections
sys.path.insert(0,'.')
import matcher as M
groups=list(dict.fromkeys(l.strip() for l in open('groups.txt',encoding='utf-8') if l.strip()))
st=json.load(open('app_settings.json',encoding='utf-8'))
hist=json.load(open('matching_memory.json',encoding='utf-8'))['history']
m=M.SmartMatcher(groups,memory_file='/nonexistent.json',generic_groups=set(st['generic_groups']),generic_margin=st['generic_margin'],rules=[])
m.load_history(dict(hist))
rows=[]
for k,g in list(m.history.items()):
    m.unremember_key(k); gg,c=m.find_best_group(k); m.restore_key(k,g); rows.append((k,g,gg,c))
ok=sum(r[1]==r[2] for r in rows); print(f"LOO acc {ok}/{len(rows)}={ok/len(rows):.3f}")
for lo,hi in [(0,.4),(.4,.55),(.55,.7),(.7,.85),(.85,.95),(.95,1.01)]:
    s=[r for r in rows if lo<=r[3]<hi]
    if s: print(f"conf[{lo},{hi}): n={len(s)} acc={sum(r[1]==r[2] for r in s)/len(s):.2f}")