"""Переобучение модели ранжирования по вашей истории (matching_memory.json).
Запуск из папки приложения:  python train_model.py
Нужны: pip install scikit-learn scipy numpy. Результат: matcher_model.json (рядом с matcher.py).
Стоит переобучать после того, как история заметно вырастет (например, +500 строк)."""
import json,sys,math,pickle,warnings,numpy as np
warnings.filterwarnings('ignore')
sys.path.insert(0,'.')
GROUPS_FILE='groups.txt'
import matcher as M
from sklearn.ensemble import GradientBoostingClassifier
from scipy.optimize import minimize_scalar
groups=list(dict.fromkeys(l.strip() for l in open(GROUPS_FILE,encoding='utf-8') if l.strip()))
st=json.load(open('app_settings.json',encoding='utf-8'));mem=json.load(open('matching_memory.json',encoding='utf-8'))
m=M.SmartMatcher(groups,memory_file='/nonexistent.json',generic_groups=set(st['generic_groups']),generic_margin=st['generic_margin'],rules=[])
m.load_history(dict(mem['history']))
data=[]
for k,g in list(m.history.items()):
    m.unremember_key(k)
    sl=m._extract_stems_list(k)
    if sl:
        head=next((w for w in sl if w not in m.MODIFIERS),sl[0])
        m._q_noun=m._head_noun(k); m._q_stems_list=list(sl)   # как в find_best_group
        feats,meta=m.candidate_features(sl,head)
        gs=list(feats)
        if gs: data.append((np.array([feats[x] for x in gs]),gs.index(g) if g in feats else -1,
                            np.array([meta[x].get('name_bonus',0.0) for x in gs])))
    m.restore_key(k,g)
def mk(): return GradientBoostingClassifier(max_depth=2,n_estimators=200,learning_rate=0.1,subsample=0.8,random_state=0)
def stack(D): return np.vstack([d[0] for d in D]), np.concatenate([(np.arange(len(d[0]))==d[1]).astype(int) for d in D])
rng=np.random.RandomState(0); idx=rng.permutation(len(data)); folds=np.array_split(idx,5)
oof=[None]*len(data)
for f in folds:
    fs=set(f); tr=[data[i] for i in idx if i not in fs]
    clf=mk().fit(*stack(tr))
    for i in f: oof[i]=clf.decision_function(data[i][0])+data[i][2]   # + бонус за название группы, как в матчере
ok=sum(1 for i,(X,y,_) in enumerate(data) if y>=0 and oof[i].argmax()==y)
print('CV acc',round(ok/len(data),3))
def nll(T):
    t=0
    for i,(X,y,_) in enumerate(data):
        if y<0: continue
        z=oof[i]/T; z=z-z.max(); p=np.exp(z)/np.exp(z).sum(); t-=math.log(p[y]+1e-12)
    return t
T=minimize_scalar(nll,bounds=(0.3,5),method='bounded').x
print('temperature',round(T,3))
# calibration of OOF
bins=[(0,.4),(.4,.55),(.55,.7),(.7,.85),(.85,.95),(.95,1.01)]
res=[]
for i,(X,y,_) in enumerate(data):
    z=oof[i]/T; z=z-z.max(); p=np.exp(z)/np.exp(z).sum(); res.append((p.max(), y>=0 and p.argmax()==y))
for lo,hi in bins:
    s=[r for r in res if lo<=r[0]<hi]
    if s: print(f'conf[{lo},{hi}) n={len(s)} acc={np.mean([r[1] for r in s]):.2f}')
clf=mk().fit(*stack(data))
trees=[]
for est in clf.estimators_[:,0]:
    t=est.tree_
    trees.append([t.feature.tolist(),[round(float(x),6) for x in t.threshold],t.children_left.tolist(),t.children_right.tolist(),[round(float(x),6) for x in t.value[:,0,0]]])
init=float(clf.init_.predict_proba(np.zeros((1,data[0][0].shape[1])))[0,1]); init=math.log(init/(1-init))
model={'version':M.SmartMatcher.MODEL_VERSION,'init':init,'lr':clf.learning_rate,'T':float(T),'trees':trees,'n_features':int(data[0][0].shape[1])}
json.dump(model,open('matcher_model.json','w'))
# parity check
X=data[0][0]; ref=clf.decision_function(X)
def ev(x):
    s=model['init']
    for f,th,l,r,v in trees:
        n=0
        while l[n]!=-1: n=l[n] if x[f[n]]<=th[n] else r[n]
        s+=model['lr']*v[n]
    return s
print('parity',np.abs(ref-np.array([ev(x) for x in X])).max())

