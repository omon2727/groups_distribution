"""
SmartMatcher — сопоставление названия запчасти -> группа.

Порядок принятия решения:
1. Точное совпадение очищенного названия с подтверждённым (память "history") -> 100%.
2. Пользовательские ПРАВИЛА (вкладка «Правила»), если они есть -> 100%.
3. Ранжирование кандидатов. Для каждой группы считаются признаки: совпадение слов
   с названием группы, сходство с ПОДТВЕРЖДЁННЫМИ ПРИМЕРАМИ из истории (kNN), доля
   группы среди примеров с теми же словами, «главное слово», признак обобщённой группы.
   К оценке добавляется БОНУС ЗА НАЗВАНИЕ ГРУППЫ (_name_bonus): если название запроса
   совпадает с названием группы («Ремень грм» -> «Ремень ГРМ», «Стекло зеркала» ->
   «Стекло зеркала»), это перебивает случайные соседи из памяти. Примеры памяти, у которых
   нет ни одного общего слова со своей группой, учитываются с пониженным весом.
   Уверенность = вероятность лучшей группы (softmax с температурой TEMP), поэтому
   0.9 ≈ 90% верных. 100% выдаётся только для истории и правил.
История — единственный источник обучения; «learned» пересчитывается из неё.

Версия 2: исправлено стемминг («топливного» -> «топливн», «переднего» -> «передн» —
раньше такие слова не совпадали с названиями групп и не считались модификаторами),
добавлены синонимы (ручной тормоз = ручник, поликлиновой = ручейковый, охл. = охлаждающей,
указатель поворота = поворотник), защита от ложной «коррекции опечаток»
(«дискового» -> «диск»). Файл matcher_model.json старой версии игнорируется.
"""

import re
import os
import json
import math
import shutil
import difflib
import time
from pathlib import Path
from collections import defaultdict


class SmartMatcher:
    # Слова-модификаторы (стороны, размеры, цвета, служебные): низкий вес,
    # в память не учатся. Здесь именно результаты стемминга (_stem).
    MODIFIERS = {
        "передн", "передня", "задн", "задня", "лев", "прав",
        "верхн", "верхня", "нижн", "нижня", "наружн", "наруж",
        "внутренн", "внутрення", "боков", "центральн", "централь",
        "больш", "мал", "коротк", "длинн", "длин", "нов", "стар",
        "универсальн", "универсаль", "средн", "средня",
        "бел", "черн", "чёрн", "чер", "чёр", "сер", "син", "синя",
        "красн", "крас", "зелен", "зелён", "зеле", "зелё", "желт", "жёлт",
        "коричнев", "оранжев", "фиолетов", "розов", "бежев",
        "серебрист", "золотист",
        # «комплект» и «ремкомплект» сами ничего не различают, но их наличие
        # даёт маленький плюс группам, где они тоже есть
        # («Комплект ГРМ», «Комплект сцепления» и т. п.).
        "комплект", "ремкомплект", "компл",
        # описательные слова, не определяющие тип детали
        "шариков", "роликов", "игольчат", "сбор", "оригинал", "аналог",
        "универс",
    }

    # Сокращения -> полное слово. Пробел в конце нужен, чтобы слова не склеивались.
    ABBREV = [
        (r'\bрем\.?\s*к[-\s]?кт\.?\b', 'ремкомплект '),
        (r'\bк[-\s]?кт\.?\b', 'комплект '),
        (r'\bк[-\s]?т\.?\b', 'комплект '),
        (r'\bкомпл\.?\b', 'комплект '),
        (r'\bподш\.?\b', 'подшипник '),
        (r'\bступ\.?\b', 'ступица '),
        (r'\bторм\.?\b', 'тормозной '),
        (r'\bамортиз\.?\b', 'амортизатор '),
        (r'\bпер\.?\b', 'передний '),
        (r'\bзад\.?\b', 'задний '),
        (r'\bправ\.?\b', 'правый '),
        (r'\bлев\.?\b', 'левый '),
        (r'\bверхн\.?\b', 'верхний '),
        (r'\bнижн\.?\b', 'нижний '),
        (r'\bнаружн\.?\b', 'наружный '),
        (r'\bвнутр\.?\b', 'внутренний '),
        (r'\bцил\.?\b', 'цилиндр '),
        (r'\bрад\.?\b', 'радиатор '),
        (r'\bэл\.?\b', 'электрический '),
        (r'\bгидр\.?\b', 'гидравлический '),
        (r'\bпневм\.?\b', 'пневматический '),
        (r'\bр/к\b', 'ремкомплект '),
        (r'\bтурбокомпрессор[а-я]*\b', 'турбина '),
        (r'\bдвс\b', 'двигатель '),
        (r'\bэлектропомп[а-я]*\b', 'помпа '),
        (r'\bохл\.?(?=\s|$)', 'охлаждающей '),
        (r'\bподвес\.\s*кабин', 'подвески кабины'),
        # «ручной/стояночный тормоз» в каталоге = «ручник»
        (r'\b(?:ручн|стояночн)[а-я]*\s+тормоз[а-я]*', 'ручник '),
        (r'\bполиклинов[а-я]*\b', 'ручейковый '),
        (r'\bуказател[а-я]*\s+поворот[а-я]*', 'поворотник '),
    ]

    # Синонимы каталога: слово-пара ДОБАВЛЯЕТСЯ рядом с исходным.
    SYNONYM_ADDITIONS = [
        (r'\bотопител[а-я]*\b', ' печка'),
    ]

    # «Электродвигатель/вентилятор … отопителя» в каталоге = «Моторчик печки».
    # Заменяем ВСЮ пару на «моторчик печка», чтобы «главным словом» стал
    # именно «моторчик» и он не проиграл по HEAD_BONUS слову «вентилятор».
    HEATER_MOTOR_RE = re.compile(
        r'\b(?:электродвигател[а-я]*|вентилятор[а-я]*|моторчик[а-я]*'
        r'|мотор[а-я]*|двигател[а-я]*)\s+отопител[а-я]*\b'
    )

    # Латинские технические термины -> кириллица (иначе латиница считается шумом-маркой).
    LATIN_TECH_SYNONYMS = {
        "abs": "абс",
        "egr": "егр",
    }

    # Настоящие служебные слова, которые не несут смысла в названии.
    # ВАЖНО: «комплект»/«ремкомплект» здесь БОЛЬШЕ НЕТ — они переехали в MODIFIERS.
    STOPWORDS = {
        "для", "или", "под", "при", "без", "что", "это", "как", "том",
        "его", "них", "они", "она", "оно", "есть", "этот",
    }

    # ВАЖНО: убрано окончание "ка" — оно ломало стем «подшипника» -> «подшипни»
    # (в группах лежит «подшипник», и они не совпадали).
    STEM_ENDINGS = sorted([
        'иями', 'ями', 'ами', 'ов', 'ев', 'ей', 'ий', 'ый', 'ая', 'ое', 'ые',
        'ом', 'ем', 'ой', 'ах', 'ях', 'ам', 'ям', 'у', 'ю', 'а', 'я',
        'е', 'и', 'ы', 'о', 'ь', 'ие', 'ия', 'ной', 'ная', 'ные',
        'ский', 'ская', 'ского', 'ок', 'ек',
        # родительный/дательный падежи прилагательных: «топливного» -> «топливн»,
        # «переднего» -> «передн» (раньше оставались «топливног», «переднег»,
        # и слова не совпадали ни с названием группы, ни с модификаторами)
        'ого', 'его', 'ому', 'ему', 'ыми', 'ими', 'ых', 'их', 'ым', 'им',
        'ую', 'юю', 'ее', 'яя', 'ные', 'ных', 'ным',
    ], key=len, reverse=True)

    _HOMOGLYPH_CHARS = set('acepoxyk')
    _HOMOGLYPH_MAP = str.maketrans({
        'a': 'а', 'c': 'с', 'e': 'е', 'o': 'о',
        'p': 'р', 'x': 'х', 'y': 'у', 'k': 'к',
    })

    THRESHOLD = 2.0
    KNN_WEIGHT = 30.0
    KNN_SECOND = 0.3
    KNN_POWER = 2.0
    HEAD_MISS = 0.5
    CAL_W = (1.75, 0.52, 0.91, 2.03)
    CAL_B = -3.61
    EXACT_FLOOR = 0.9
    EXACT_BONUS = 8.0
    FULLCOVER_BONUS = 2.0
    MOD_W_KNN = 0.3
    CONFIDENCE_SCALE = 14.0
    LEARNED_WEIGHT = 1.5
    LEARNED_VOTE_CAP = 5
    HEAD_BONUS = 3.0

    AMBIGUITY_TIE_MARGIN = 0.8
    AMBIGUITY_MIN_RIVALS = 6
    AMBIGUITY_CONFIDENCE_CAP = 0.4

    # Калибровка по отрыву от второй группы: при отрыве 0 уверенность <= CAL_MIN,
    # при отрыве >= CAL_GAP_FULL — до CAL_MAX. 100% для скоринга не выдаётся.
    CAL_GAP_FULL = 2.0
    CAL_MIN = 0.5
    CAL_MAX = 0.99
    GENERIC_OVERRIDE_CAP = 0.7

    # Защита от ложной "коррекции опечатки" (фазорегулятор -> регулятор).
    TYPO_CUTOFF = 0.8
    TYPO_MAX_LEN_DIFF = 1

    DEFAULT_GENERIC_GROUPS = (
        "Соединители", "Болты, винты", "Гайки, шайбы", "Втулки",
        "Кольца уплотнительные", "Стопорные кольца", "Кронштейны", "Заглушки",
        "Клипсы, фиксаторы, зажимы",
    )
    GENERIC_MARGIN = 1.5

    def __init__(self, groups: list[str], memory_file: str = "matching_memory.json",
                 generic_groups: "set[str] | None" = None, generic_margin: "float | None" = None,
                 rules: "list[dict] | None" = None):
        self.groups = groups
        self.memory_file = memory_file
        self._canon_cache = {}
        self._vocab = set()
        # словарь основ из названий групп (нужен для приведения основ к общему виду)
        raw_group_stems = {g: self._extract_stems_list(self._clean(g), keep_latin=True, canon=False)
                           for g in groups}
        for lst_ in raw_group_stems.values():
            self._vocab.update(lst_)

        self.history, self.learned = self._load_memory()
        self._load_model()
        self._hidx_built = False

        self.group_stems = {g: self._extract_stems(self._clean(g), keep_latin=True) for g in groups}
        self._compute_idf()
        self._g_inv = defaultdict(set)
        self._group_head = {}
        self._group_core = {}
        self._group_noun = {}
        for g, st_ in self.group_stems.items():
            for w in st_:
                self._g_inv[w].add(g)
            lst_ = self._extract_stems_list(self._clean(g), keep_latin=True)
            core_ = [w for w in lst_ if w not in self.MODIFIERS]
            self._group_head[g] = core_[0] if core_ else None
            self._group_core[g] = frozenset(core_)
            self._group_noun[g] = self._head_noun(self._clean(g), keep_latin=True) or self._group_head[g]

        self._noun_words = defaultdict(set)
        _gen = set(generic_groups if generic_groups is not None else self.DEFAULT_GENERIC_GROUPS)
        for g_, core_ in self._group_core.items():
            n_ = self._group_noun.get(g_)
            if n_ and g_ not in _gen:
                self._noun_words[n_].update(w for w in core_ if w != n_)

        self.load_history(self.history)

        base_generic = generic_groups if generic_groups is not None else self.DEFAULT_GENERIC_GROUPS
        self.generic_groups = {g for g in base_generic if g in self.group_stems}
        self.generic_margin = self.GENERIC_MARGIN if generic_margin is None else generic_margin

        self._compiled_rules = []
        self.set_rules(rules or [])

    # ---------- правила ----------
    def set_rules(self, rules: list):
        """Правило: {"pattern": str, "group": str, "regex": bool}.
        Обычная фраза сопоставляется с ОЧИЩЕННЫМ названием по границам слов
        (сама фраза очищается так же). Регулярка ищется в очищенном названии
        (нижний регистр, без знаков препинания и цифр). Побеждает первое
        подходящее правило сверху вниз. Некорректные регулярки пропускаются."""
        compiled = []
        for r in rules or []:
            pattern = (r.get("pattern") or "").strip()
            group = (r.get("group") or "").strip()
            if not pattern or not group:
                continue
            try:
                if r.get("regex"):
                    rx = re.compile(pattern, re.IGNORECASE)
                else:
                    phrase = self._clean(pattern)
                    if not phrase:
                        continue
                    rx = re.compile(r'(?<!\S)' + re.escape(phrase) + r'(?!\S)')
            except re.error:
                continue
            compiled.append((rx, group, pattern))
        self._compiled_rules = compiled

    def match_rule(self, cleaned: str):
        for rx, group, pattern in self._compiled_rules:
            if rx.search(cleaned):
                return group, pattern
        return None

    # ---------- очистка и разбор текста ----------
    def _fix_homoglyphs(self, s: str) -> str:
        def repl(m):
            token = m.group(0)
            has_cyr = any(('а' <= ch <= 'я') or ch == 'ё' for ch in token)
            has_lookalike = any(ch in self._HOMOGLYPH_CHARS for ch in token)
            if has_cyr and has_lookalike:
                return token.translate(self._HOMOGLYPH_MAP)
            return token
        return re.sub(r'[а-яёa-z]+', repl, s)

    def _clean(self, name: str) -> str:
        if not isinstance(name, str):
            return ""
        s = name.lower()
        s = self._fix_homoglyphs(s)
        s = s.replace('_', ' ')
        for pattern, repl in self.ABBREV:
            s = re.sub(pattern, repl, s)
        for latin, cyr in self.LATIN_TECH_SYNONYMS.items():
            s = re.sub(rf'\b{latin}\b', cyr, s)
        # «Вентилятор отопителя» -> «моторчик печка» (одним куском, без «вентилятор»).
        s = self.HEATER_MOTOR_RE.sub('моторчик печка', s)
        for pattern, addition in self.SYNONYM_ADDITIONS:
            added_word = addition.strip()
            # Не добавляем «печка», если оно уже есть в тексте.
            if added_word and added_word in s.split():
                continue
            s = re.sub(pattern, lambda m: m.group(0) + addition, s)
        s = re.sub(r'[!=\\/|\[\]()+,;]', ' ', s)
        s = re.sub(r'[^а-яёa-z\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    def _stem(self, word: str) -> str:
        if len(word) == 4 and word[-1] in 'аыиуяеоь' and re.search(r'[а-яё]', word):
            # «фара» / «фары» -> «фар», «шина» / «шины» -> «шин»
            return word[:-1]
        if len(word) <= 4:
            return word
        # Беглые гласные: «патрубок» -> «патрубк» (как «патрубки»), «ремень» -> «ремн» (как «ремни»).
        if len(word) >= 6 and word.endswith('ок') and re.search(r'[а-яё]', word):
            return word[:-2] + 'к'
        if len(word) >= 5 and word.endswith('ень'):
            return word[:-3] + 'н'
        for end in self.STEM_ENDINGS:
            if word.endswith(end) and len(word) - len(end) >= 3:
                st = word[:-len(end)]
                # «ножной» -> «нож» совпало бы со словом «нож»: у длинных прилагательных
                # оставляем ещё одну букву
                if len(st) <= 3 and len(word) >= 6 and self._ADJ_RE.search(word):
                    return word[:-2]
                return st
        return word

    _ADJ_RE = re.compile(r'(ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|его|ых|их|ому|ему|ым|им|ую|юю|ные|ных)$')

    def _head_noun(self, cleaned_text: str, keep_latin: bool = False):
        """Основа главного СУЩЕСТВИТЕЛЬНОГО названия: первое слово, которое не прилагательное
        и не модификатор («Тормозные колодки» -> колодк, «Натяжной ролик ГРМ» -> ролик)."""
        tokens = cleaned_text.split()
        for i, w in enumerate(tokens):
            if i > 0 and tokens[i - 1] in ("без", "безо"):
                continue
            if len(w) < 3 or w in self.STOPWORDS:
                continue
            if not re.search(r'[а-яё]', w):
                continue
            if self._ADJ_RE.search(w) and len(w) > 5:
                continue
            st = self._canon(self._stem(w))
            if st in self.MODIFIERS:
                continue
            return st
        return None

    def _canon(self, stem: str) -> str:
        """Приводит основу к общему виду с основами из названий групп:
        «топливн» ~ «топлив», «тормоз» ~ «тормозн», «колесн» ~ «колес»
        (иначе прилагательное и существительное с одним корнем не совпадают)."""
        if len(stem) < 5:
            return stem
        c = self._canon_cache.get(stem)
        if c is not None:
            return c
        best = stem
        # «топливн» -> «топлив», если «топлив» есть среди слов групп
        if stem.endswith('н') and len(stem) >= 6 and stem[:-1] in self._vocab:
            best = stem[:-1]
        elif stem not in self._vocab:
            best_d = 99
            for v in self._vocab:
                if len(v) < 5 or not re.search(r'[а-яё]', v):
                    continue
                if (v.startswith(stem) or stem.startswith(v)) and abs(len(v) - len(stem)) <= 2:
                    d = abs(len(v) - len(stem))
                    if d < best_d:
                        best, best_d = v, d
        self._canon_cache[stem] = best
        return best

    def _extract_stems_list(self, cleaned_text: str, keep_latin: bool = False, canon: bool = True) -> list[str]:
        """Основы слов с сохранением порядка. keep_latin=False для названий запчастей
        (латиница = марка/модель), True для названий групп (NOx, AdBlue, VVT...).
        Слова после «без»/«безо» игнорируются — это отрицания."""
        tokens = cleaned_text.split()
        words = []
        for i, w in enumerate(tokens):
            if i > 0 and tokens[i - 1] in ("без", "безо"):
                continue
            if len(w) >= 3 and w not in self.STOPWORDS:
                words.append(w)
        stems = []
        for w in words:
            if not keep_latin and re.match(r'^[a-z]+$', w):
                continue
            st = self._stem(w) if re.search(r'[а-яё]', w) else w
            if canon and re.search(r'[а-яё]', st):
                st = self._canon(st)
            stems.append(st)
        return stems

    def _extract_stems(self, cleaned_text: str, keep_latin: bool = False) -> set[str]:
        return set(self._extract_stems_list(cleaned_text, keep_latin=keep_latin))

    # ---------- вес слова ----------
    def _compute_idf(self):
        df = defaultdict(int)
        for stems in self.group_stems.values():
            for w in stems:
                df[w] += 1
        n = max(len(self.group_stems), 1)
        self.idf = {w: math.log((n + 1) / (c + 0.5)) + 0.1 for w, c in df.items()}
        self.default_idf = math.log(n + 1) + 0.1

    def _word_weight(self, word: str) -> float:
        if word in self.MODIFIERS:
            return 0.4
        return self.idf.get(word, self.default_idf)

    # ---------- память ----------
    def _load_memory(self):
        self.memory_error = None
        mem_path = Path(self.memory_file)
        if not mem_path.exists():
            return {}, {}
        try:
            with open(mem_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            # Файл памяти есть, но не читается. Раньше тут молча возвращалась пустая память,
            # и первое же «запомнить» перезаписывало файл — вся история терялась.
            # Теперь: сохраняем копию повреждённого файла и пробуем резервную копию (.bak).
            stamp = time.strftime("%Y%m%d_%H%M%S")
            try:
                shutil.copy2(mem_path, mem_path.with_name(f"{mem_path.name}.corrupt-{stamp}"))
            except Exception:
                pass
            bak_path = mem_path.with_name(mem_path.name + ".bak")
            try:
                with open(bak_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.memory_error = (f"Файл памяти «{mem_path.name}» повреждён ({e}). "
                                     f"Память восстановлена из резервной копии «{bak_path.name}». "
                                     f"Повреждённый файл сохранён рядом (*.corrupt-{stamp}).")
            except Exception:
                self.memory_error = (f"Файл памяти «{mem_path.name}» повреждён ({e}), "
                                     f"резервной копии нет — память пуста. Повреждённый файл "
                                     f"сохранён рядом (*.corrupt-{stamp}); его можно попробовать "
                                     f"починить вручную.")
                return {}, {}

        if isinstance(data, dict) and ("history" in data or "learned" in data):
            history = data.get("history", {}) or {}
            learned = data.get("learned", {}) or {}
            learned = {w: v for w, v in learned.items() if w not in self.MODIFIERS}
            # Ключи истории, записанные старой версией очистки, пересчитываем
            # текущей очисткой — иначе точное совпадение по ним не сработает.
            history = {(self._clean(k) or k): v for k, v in history.items()}
            return history, learned

        if isinstance(data, dict):
            history = {(self._clean(k) or k): v for k, v in data.items()}
            learned = {}
            for cleaned_name, group in history.items():
                for w in self._extract_stems(cleaned_name):
                    learned.setdefault(w, {})
                    learned[w][group] = learned[w].get(group, 0) + 1
            return history, learned

        return {}, {}

    # ---------- индекс истории (поиск по похожим примерам) ----------
    def load_history(self, history: dict):
        self.history = dict(history)
        self._h_stems = {}
        self._h_head = {}
        self._h_trust = {}
        self._h_inv = defaultdict(set)
        self._h_df = defaultdict(int)
        for k, g in self.history.items():
            self._index_key(k)
        self.learned = self._learned_from_history()

    def _index_key(self, k):
        lst = self._extract_stems_list(k)
        st = frozenset(lst)
        self._h_stems[k] = st
        core = [w for w in lst if w not in self.MODIFIERS]
        self._h_head[k] = frozenset(core[:2]) if core else frozenset(lst[:1])
        for w in st:
            self._h_inv[w].add(k)
            self._h_df[w] += 1
        # доверие к примеру: есть ли у ключа хоть одно общее слово с названием его группы
        g = self.history.get(k)
        gcore = self._group_core.get(g)
        if gcore is None:
            self._h_trust[k] = 1.0
        else:
            self._h_trust[k] = 1.0 if (set(core) & gcore) else self.UNTRUSTED

    def _unindex_key(self, k):
        st = self._h_stems.pop(k, frozenset())
        self._h_head.pop(k, None)
        self._h_trust.pop(k, None)
        for w in st:
            self._h_inv[w].discard(k)
            self._h_df[w] -= 1

    def _learned_from_history(self):
        learned = {}
        for k, g in self.history.items():
            for w in self._h_stems.get(k, ()):
                if w in self.MODIFIERS:
                    continue
                learned.setdefault(w, {})
                learned[w][g] = learned[w].get(g, 0) + 1
        return learned

    def unremember_key(self, k):
        self._unindex_key(k)
        self.history.pop(k, None)

    def restore_key(self, k, g):
        self.history[k] = g
        self._index_key(k)

    def _hw(self, w):
        if w in self.MODIFIERS:
            return self.MOD_W_KNN
        n = max(len(self.history), 1)
        return math.log((n + 1) / (self._h_df.get(w, 0) + 0.5)) + 0.1

    def _knn_scores(self, stems, head=None):
        """Для каждой группы: лучшее взвешенное косинусное сходство запроса
        с подтверждёнными примерами этой группы (+ доля от второго лучшего)."""
        if not self.history or not stems:
            return {}
        qw = {w: self._hw(w) for w in stems}
        qn = sum(qw.values())
        cand = set()
        for w in stems:
            cand |= self._h_inv.get(w, set())
        per_group = defaultdict(list)
        for k in cand:
            hs = self._h_stems[k]
            common = stems & hs
            if not common:
                continue
            num = sum(qw[w] for w in common)
            hn = sum(self._hw(w) for w in hs)
            sim = num / math.sqrt(qn * hn) if qn > 0 and hn > 0 else 0.0
            if head and head not in self._h_head[k] and not (self._h_head[k] <= stems):
                sim *= self.HEAD_MISS
            per_group[self.history[k]].append(sim)
        out = {}
        for g, sims in per_group.items():
            sims.sort(reverse=True)
            v = sims[0] ** self.KNN_POWER + (self.KNN_SECOND * sims[1] ** self.KNN_POWER if len(sims) > 1 else 0.0)
            out[g] = v
        return out

    def save_memory(self):
        """Атомарная запись: пишем во временный файл и подменяем им основной (os.replace),
        поэтому сбой посреди записи не оставит наполовину записанный файл.
        Прежняя версия (если она читается) остаётся как <файл>.bak."""
        path = Path(self.memory_file)
        tmp = path.with_name(path.name + ".tmp")
        payload = json.dumps({"history": self.history, "learned": self.learned},
                             ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    json.load(f)          # копируем в .bak только заведомо целый файл
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            except Exception:
                pass
        os.replace(tmp, path)

    def remember(self, part_name: str, group: str):
        """Запоминает выбор пользователя. Источник истины — history;
        learned пересчитывается из неё (правки не оставляют «мусорных» голосов)."""
        cleaned = self._clean(part_name)
        if not cleaned or not group:
            return
        if cleaned in self.history:
            self._unindex_key(cleaned)
        self.history[cleaned] = group
        self._index_key(cleaned)
        self.learned = self._learned_from_history()
        self.save_memory()

    # ---------- поиск группы ----------
    def find_best_group(self, part_name: str, return_debug: bool = False, top_n: int = 3):
        """Возвращает (группа, уверенность) или, при return_debug=True,
        (группа, уверенность, debug_dict). debug["source"]: history / rule /
        scoring / none; debug["top"] — до top_n лучших (группа, уверенность)."""
        cleaned = self._clean(part_name)
        debug = {
            "cleaned": cleaned, "source": None, "matched_words": [], "head_word": "",
            "base": 0.0, "coverage": 0.0, "head_bonus": 0.0, "learned_bonus": 0.0, "score": 0.0,
            "generic_override": False, "top": [], "typo_fixed": False,
            "ambiguous": False, "gap": 0.0, "rule": "",
            "confidence_raw": 0.0,
        }

        def out(group, conf):
            return (group, conf, debug) if return_debug else (group, conf)

        if not cleaned:
            return out(None, 0.0)

        # 1. История
        if cleaned in self.history:
            debug.update(source="history", score=1.0, confidence_raw=1.0,
                         top=[(self.history[cleaned], 1.0)])
            return out(self.history[cleaned], 1.0)

        # 2. Правила пользователя
        hit = self.match_rule(cleaned)
        if hit:
            group, pattern = hit
            debug.update(source="rule", score=1.0, confidence_raw=1.0,
                         rule=pattern, top=[(group, 1.0)])
            return out(group, 1.0)

        # 3. Скоринг
        stems_list = self._extract_stems_list(cleaned)
        stems = set(stems_list)
        if not stems:
            return out(None, 0.0)

        head_word = next((w for w in stems_list if w not in self.MODIFIERS), stems_list[0])

        # Опечатка в главном слове («Бампе» -> «бампер»).
        if head_word not in self._vocab and head_word not in self._h_inv and len(head_word) >= 5:
            close = [
                c for c in difflib.get_close_matches(head_word, self._vocab, n=3, cutoff=self.TYPO_CUTOFF)
                if c[0] == head_word[0] and abs(len(c) - len(head_word)) <= self.TYPO_MAX_LEN_DIFF
            ]
            if close:
                fixed_word = close[0]
                stems_list = [fixed_word if w == head_word else w for w in stems_list]
                stems = set(stems_list)
                head_word = fixed_word
                debug["typo_fixed"] = True
        debug["head_word"] = head_word

        self._q_noun = self._head_noun(cleaned)
        self._q_stems_list = list(stems_list)
        feats, meta = self.candidate_features(stems_list, head_word)
        if not feats:
            debug["source"] = "none"
            return out(None, 0.0)

        groups_c = list(feats)
        # Температура одна: у обученной модели — её собственная T (подобрана в train_model.py
        # уже с учётом name_bonus), у запасных весов — TEMP.
        temp = self.model.get("T", self.TEMP) if getattr(self, "model", None) else self.TEMP
        logits = [(self._logit(feats[g]) + meta[g].get("name_bonus", 0.0)) / temp for g in groups_c]
        mx = max(logits)
        exps = [math.exp(l - mx) for l in logits]
        tot = sum(exps)
        probs = {g: e / tot for g, e in zip(groups_c, exps)}
        order = sorted(groups_c, key=lambda g: probs[g], reverse=True)
        best_group = order[0]
        bm = meta[best_group]

        best_conf = min(probs[best_group], self.CAL_MAX)
        second = probs[order[1]] if len(order) > 1 else 0.0
        debug["gap"] = round(probs[best_group] - second, 3)
        debug["confidence_raw"] = round(probs[best_group], 3)
        debug["generic_override"] = False
        debug["ambiguous"] = False

        debug["top"] = [(g, probs[g]) for g in order[:max(top_n, 1)]]

        # нет никакой опоры: ни слов группы, ни похожих примеров
        if bm["evidence"] < self.MIN_EVIDENCE:
            debug.update({"source": "none"})
            return out(None, 0.0)

        debug.update({
            "source": "scoring",
            "matched_words": sorted(bm["common"]),
            "base": round(bm["base"], 2),
            "coverage": round(bm["coverage"], 2),
            "head_bonus": round(bm["head_in_group"] * self.HEAD_BONUS, 2),
            "learned_bonus": round(bm["knn1"], 2),
            "name_bonus": round(bm.get("name_bonus", 0.0), 2),
            "score": round(logits[groups_c.index(best_group)], 2),
        })
        return out(best_group, best_conf)

    # ---------- признаки кандидатов ----------
    FEATURE_NAMES = [
        "qcov", "gcov", "head_in_group", "group_head_is_q_head", "exact",
        "knn1", "knn2", "knn_cnt", "nb", "generic", "generic_x_knn1",
        "log_prior", "gsize", "base10", "knn_only", "generic_x_qcov",
        "ex_in_q", "q_in_ex", "jaccard", "exq_x_qex", "generic_x_exq",
    ]
    # веса подгоняются скриптом train_weights.py по логу (условный логит)
    WEIGHTS = None
    MIN_EVIDENCE = 0.12

    def _load_model(self):
        """Ищем matcher_model.json рядом с matcher.py и в текущей папке."""
        self.model = None
        for p in (Path(__file__).with_name(self.MODEL_FILE), Path(self.MODEL_FILE)):
            try:
                if p.exists():
                    with open(p, "r", encoding="utf-8") as fh:
                        mdl = json.load(fh)
                    if (mdl.get("n_features") == len(self.FEATURE_NAMES)
                            and mdl.get("version") == self.MODEL_VERSION):
                        self.model = mdl
                        return
            except Exception:
                pass

    def _logit(self, f):
        mdl = getattr(self, "model", None)
        if mdl:
            s = mdl["init"]
            lr = mdl["lr"]
            for feat, thr, left, right, val in mdl["trees"]:
                n = 0
                while left[n] != -1:
                    n = left[n] if f[feat[n]] <= thr[n] else right[n]
                s += lr * val[n]
            return s  # «сырой» логит; температура применяется один раз в find_best_group
        return sum(a * b for a, b in zip(self.DEFAULT_WEIGHTS, f))

    def candidate_features(self, stems_list, head_word):
        stems = set(stems_list)
        q_noun = getattr(self, '_q_noun', None)
        core_q = [w for w in stems if w not in self.MODIFIERS]
        q_wsum = sum(self._word_weight(w) for w in core_q) or 1.0

        # --- kNN по истории ---
        knn = self._knn_detail(stems, head_word)

        # --- наивный байес по словам (доля групп в истории для слова) ---
        nb = defaultdict(float)
        nb_norm = 0.0
        for w in core_q:
            keys = self._h_inv.get(w)
            if not keys:
                continue
            cnt = defaultdict(float)
            for k in keys:
                cnt[self.history[k]] += self._h_trust.get(k, 1.0)
            tot = sum(cnt.values())
            wt = self._hw(w)
            nb_norm += wt
            for g, c in cnt.items():
                nb[g] += wt * c / tot
        if nb_norm:
            nb = {g: v / nb_norm for g, v in nb.items()}

        # --- кандидаты: общие слова с названием группы или похожие примеры ---
        cand_groups = set(knn) | set(nb)
        for w in stems:
            cand_groups |= self._g_inv.get(w, set())

        group_count = defaultdict(int)
        for g in self.history.values():
            group_count[g] += 1

        feats, meta = {}, {}
        pre = []
        for g in cand_groups:
            gs = self.group_stems.get(g)
            if not gs:
                continue
            common = stems & gs
            g_core = [w for w in gs if w not in self.MODIFIERS]
            base = sum(self._word_weight(w) for w in common)
            q_cov = sum(self._word_weight(w) for w in common if w not in self.MODIFIERS) / q_wsum
            coverage = len(common) / len(gs)
            head_in_group = 1.0 if head_word in gs else 0.0
            g_head = self._group_head.get(g)
            group_head_is_q_head = 1.0 if (g_head and g_head == head_word) else 0.0
            exact = 1.0 if (g_core and set(g_core) == set(core_q)) else 0.0
            k1, k2, kc, k_exq, k_qex, k_jac = knn.get(g, (0.0, 0.0, 0, 0.0, 0.0, 0.0))
            is_gen = 1.0 if g in self.generic_groups else 0.0
            f = [
                q_cov, coverage, head_in_group, group_head_is_q_head, exact,
                k1, k2, math.log1p(kc), nb.get(g, 0.0), is_gen, is_gen * k1,
                math.log1p(group_count.get(g, 0)), math.log1p(len(gs)),
                base / 10.0, 1.0 if not common else 0.0, is_gen * q_cov,
                k_exq, k_qex, k_jac, k_exq * k_qex, is_gen * k_exq,
            ]
            evidence = max(k1, q_cov if common else 0.0, nb.get(g, 0.0))
            name_bonus = self._name_bonus(g, set(core_q), head_word, q_noun)
            feats[g] = f
            meta[g] = {"common": common, "base": base, "coverage": coverage,
                       "head_in_group": head_in_group, "knn1": k1, "evidence": evidence,
                       "name_bonus": name_bonus}
            pre.append((base + 30.0 * k1 + 10.0 * nb.get(g, 0.0) + 3.0 * name_bonus, g))
        if len(pre) > self.MAX_CANDIDATES:
            pre.sort(reverse=True)
            keep = {g for _, g in pre[:self.MAX_CANDIDATES]}
            feats = {g: f for g, f in feats.items() if g in keep}
        return feats, meta

    MAX_CANDIDATES = 40

    # --- совпадение с НАЗВАНИЕМ группы (добавляется к логиту кандидата) ---
    # Названия групп — самый надёжный источник; память может содержать чужие
    # (принятые по ошибке) соответствия, поэтому название должно уметь их перебить.
    NAME_COS_W = 5.0        # косинус взвешенных основ (только если главное слово запроса есть в группе)
    NAME_SUPER_W = 2.5      # все слова группы есть в запросе
    NAME_EXACT_W = 2.0
    NAME_HEAD_EQ_W = 0.0      # доп. бонус, если главные слова запроса и (необобщённой) группы совпадают
    GENERIC_NAME_SCALE = 0.2  # множитель бонуса за название для обобщённых групп
    UNEXPLAINED_W = 5.0       # штраф за слова запроса, различающие другие группы с тем же существительным
    KIT_WORDS = frozenset({"комплект", "ремкомплект", "компл"})
    KIT_MISSING_W = 2.5       # штраф группе «Ремкомплект…», если в названии запроса нет «комплект»
    NAME_ADJ_MOD_W = 1.0      # модификатор группы стоит рядом с главным словом запроса
    NAME_NOUN_ONLY_SCALE = 0.3  # множитель, если общее у запроса и группы — лишь главное существительное
    NAME_HEAD_MISMATCH = 0.35  # множитель бонуса, если главные слова запроса и группы разные      # слова группы и запроса совпадают полностью
    GENERIC_BARE_W = 2.0    # запрос — «голое» слово («Болт!»), обобщённая группа его содержит
    UNTRUSTED = 0.35        # вес примера памяти, у которого нет ни одного общего слова с его группой
    MODEL_VERSION = 2
    TEMP = 1.5              # температура softmax (подобрана по leave-one-out на памяти)

    def _name_bonus(self, g, core_q, head_word, q_noun=None):
        """Бонус за совпадение запроса с названием группы g (см. NAME_*_W)."""
        gcore = self._group_core.get(g)
        if not gcore or not core_q or head_word not in gcore:
            return 0.0
        common = gcore & core_q
        if not common:
            return 0.0
        wg = sum(self._word_weight(w) for w in gcore)
        wq = sum(self._word_weight(w) for w in core_q)
        wc = sum(self._word_weight(w) for w in common)
        gcov = wc / wg if wg else 0.0
        qcov = wc / wq if wq else 0.0
        bonus = self.NAME_COS_W * math.sqrt(gcov * qcov)
        # общее только главное существительное, а в группе есть и другие слова — слабый довод
        if (len(gcore) > 1 and q_noun is not None and common <= {q_noun}
                and len(self._g_inv.get(q_noun, ())) > 3):
            bonus *= self.NAME_NOUN_ONLY_SCALE
        # слова запроса, которых нет в группе g, но которые различают ДРУГИЕ группы с тем же
        # существительным («Радиатор … АКПП»: «Масляный радиатор двигателя» их не объясняет)
        if q_noun is not None:
            unexplained = [w for w in core_q - gcore if w in self._noun_words.get(q_noun, ())]
            if unexplained:
                bonus -= self.UNEXPLAINED_W * sum(self._word_weight(w) for w in unexplained) / wq
        if len(common) == len(gcore):
            bonus += self.NAME_SUPER_W * qcov * qcov
            if len(common) == len(core_q):
                bonus += self.NAME_EXACT_W
        # «Ремкомплект …» / «Комплект …» в названии группы, а в запросе слова «комплект» нет
        gfull = self.group_stems.get(g, frozenset())
        qlist = getattr(self, "_q_stems_list", [])
        if (gfull & self.KIT_WORDS) and not (set(qlist) & self.KIT_WORDS):
            bonus -= self.KIT_MISSING_W
        # модификатор группы («переднее», «заднее») стоит в запросе прямо рядом с главным словом
        gmods = gfull - gcore
        if gmods and head_word in qlist:
            hi = qlist.index(head_word)
            near = set(qlist[max(0, hi - 1):hi + 2])
            if gmods & near - self.KIT_WORDS:
                bonus += self.NAME_ADJ_MOD_W
        # главное существительное запроса не совпало с главным существительным группы
        # («Ступица … с подшипником» vs «Подшипник ступицы») — совпадение слабее
        if q_noun is not None and self._group_noun.get(g) != q_noun:
            bonus *= self.NAME_HEAD_MISMATCH
        # обобщённая группа («Кольца уплотнительные», «Втулки») содержится в очень многих
        # названиях — само по себе это слабый довод, его должна давать память
        if g in self.generic_groups:
            bonus *= self.GENERIC_NAME_SCALE
        if g in self.generic_groups and core_q <= gcore:
            bonus += self.GENERIC_BARE_W
        return bonus

    def _knn_detail(self, stems, head):
        """{группа: (лучшее сходство, второе лучшее, число примеров со сходством >= 0.5)}"""
        if not self.history or not stems:
            return {}
        qw = {w: self._hw(w) for w in stems}
        qn = sum(qw.values())
        cand = set()
        for w in stems:
            cand |= self._h_inv.get(w, set())
        per = defaultdict(list)
        for k in cand:
            hs = self._h_stems[k]
            common = stems & hs
            if not common:
                continue
            num = sum(qw[w] for w in common)
            hn = sum(self._hw(w) for w in hs)
            sim = num / math.sqrt(qn * hn) if qn > 0 and hn > 0 else 0.0
            if head and head not in self._h_head[k] and not (self._h_head[k] <= stems):
                sim *= self.HEAD_MISS
            sim *= self._h_trust.get(k, 1.0)
            hcore = {w for w in hs if w not in self.MODIFIERS}
            qcore = {w for w in stems if w not in self.MODIFIERS}
            cc = hcore & qcore
            ex_in_q = (sum(qw[w] for w in cc) / sum(self._hw(w) for w in hcore)) if hcore else 0.0
            q_in_ex = (sum(qw[w] for w in cc) / sum(qw[w] for w in qcore)) if qcore else 0.0
            jac = len(cc) / len(hcore | qcore) if (hcore | qcore) else 0.0
            per[self.history[k]].append((sim, ex_in_q, q_in_ex, jac))
        res = {}
        for g, lst in per.items():
            sims = sorted((x[0] for x in lst), reverse=True)
            res[g] = (sims[0], sims[1] if len(sims) > 1 else 0.0,
                      sum(1 for x in sims if x >= 0.5),
                      max(x[1] for x in lst), max(x[2] for x in lst), max(x[3] for x in lst))
        return res

    DEFAULT_WEIGHTS = [2.056, 1.714, 0.391, 0.523, 2.139, 4.022, 1.618, -0.244, 3.03, 0.275, -0.133,
                       0.216, -0.153, 1.374, 1.33, 0.974, -1.94, -1.501, 1.547, 2.756, 1.194]
    MODEL_FILE = 'matcher_model.json'