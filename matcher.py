"""
SmartMatcher — сопоставление названия запчасти -> группа.

Порядок принятия решения:
1. Точное совпадение очищенного названия с подтверждённым (память "history") -> 100%.
2. Пользовательские ПРАВИЛА (вкладка «Правила» в настройках): фраза/регулярка -> группа -> 100%.
3. Скоринг по словам: слова названия группы (IDF, покрытие группы, «главное слово»,
   запрос = название группы), штраф за слова группы, которых нет в запросе
   (в т.ч. «Ремкомплект …», если в названии детали нет ремкомплекта),
   согласование модификаторов (передний/задний, верхний/нижний, наружный/внутренний)
   + вклад ИСТОРИИ: похожие подтверждённые примеры и доля групп среди примеров с тем же
   главным словом (так матчер перенимает ваши привычки: «Болт …» -> Болты, винты).
   Явное совпадение с названием группы история не отменяет.
   Уверенность калибруется по отрыву лучшей группы от второй; 100% — только история и правила.
История — единственный источник обучения; «learned» пересчитывается из неё.
"""

import re
import json
import math
import difflib
from pathlib import Path
from collections import defaultdict


class SmartMatcher:
    # Слова-модификаторы (стороны, размеры, цвета, служебные): низкий вес,
    # в память не учатся. Здесь именно результаты стемминга (_stem).
    MODIFIERS = {
        "передн", "передня", "передне", "задн", "задня", "задне", "лев", "прав",
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
        (r'\bохл\.?(?=\s|$|[,;)])', 'охлаждающей '),
        (r'\bгидр\.?\b', 'гидравлический '),
        (r'\bпневм\.?\b', 'пневматический '),
        (r'\bр/к\b', 'ремкомплект '),
        (r'\bтурбокомпрессор[а-я]*\b', 'турбина '),
        (r'\bдвс\b', 'двигатель '),
        # шины: «автошина», «авторезина», «покрышка», опечатки «ашина»/«атошина»
        (r'\bа[втор]{0,4}шин[а-я]*\b', 'шины '),
        (r'\bавторезин[а-я]*\b', 'шины '),
        (r'\bпокрышк[а-я]*\b', 'шины '),
        (r'\bиммобилиз[а-я]*\b', 'иммобилайзер '),
        (r'\bблок[-\s]?фар[а-я]*\b', 'фара '),
        # устойчивые пары: «вал карданный» = Кардан, «вал распределительный» = Распредвал и т.д.
        (r'\bвал[а-я]*\s+кардан[а-я]*\b', 'кардан '),
        (r'\bкардан[а-я]*\s+вал[а-я]*\b', 'кардан '),
        (r'\bвал[а-я]*\s+распределительн[а-я]*\b', 'распредвал '),
        (r'\bраспределительн[а-я]*\s+вал[а-я]*\b', 'распредвал '),
        (r'\bвал[а-я]*\s+коленчат[а-я]*\b', 'коленвал '),
        (r'\bколенчат[а-я]*\s+вал[а-я]*\b', 'коленвал '),
        (r'\bвал[а-я]*\s+рулев[а-я]*(?:\s+управлени[а-я]*)?\b', 'рулевой вал '),
        (r'\bотопл\.?(?=\s|$)', 'отопления'),
        (r'\bбуфер[а-я]*\b', 'отбойник '),
        (r'\bвоздухопровод[а-я]*\b', 'воздуховод '),
        (r'\bгруз[а-я]*\s+балансировочн[а-я]*', 'грузики балансировочные'),
        (r'\bдвер[а-я]+\s+задка\b', 'крышка багажника'),
        (r'\bэлектропомп[а-я]*\b', 'помпа '),
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
        r'|мотор[а-я]*|двигател[а-я]*)\s+(?:системы\s+)?(?:отопител[а-я]*|отоплен[а-я]*)\b'
    )

    # Латинские технические термины -> кириллица (иначе латиница считается шумом-маркой).
    LATIN_TECH_SYNONYMS = {
        "abs": "абс",
        "egr": "егр",
    }

    # Настоящие служебные слова, которые не несут смысла в названии.
    # ВАЖНО: «комплект»/«ремкомплект» здесь БОЛЬШЕ НЕТ — они переехали в MODIFIERS.
    TIRE_SIZE_RE = re.compile(r'\b\d{3}\s*/\s*\d{2}\s*(?:[rр/]|\s)\s*\d{2}\b')

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
        # -ный/-ное: «уплотнительное» и «уплотнительные» должны давать одну основу
        'ный', 'ное', 'ного', 'ному', 'ным', 'ными', 'ных', 'ную', 'ном',
    ], key=len, reverse=True)

    _HOMOGLYPH_CHARS = set('acepoxykbhmt')
    _HOMOGLYPH_MAP = str.maketrans({
        'a': 'а', 'c': 'с', 'e': 'е', 'o': 'о',
        'p': 'р', 'x': 'х', 'y': 'у', 'k': 'к',
        'b': 'в', 'h': 'н', 'm': 'м', 't': 'т',
    })

    THRESHOLD = 2.0
    WEAK_HEADS = {"блок", "узел", "элемент", "деталь", "набор"}
    HEAD_PRIOR_W = 8.0      # вклад доли группы среди примеров с тем же главным словом
    HEAD_PRIOR_MIN = 4
    REPAIR_KIT_PEN = 10.0   # группа «Ремкомплект …», а в названии нет ремкомплекта
    HEAD_MISMATCH_PEN = 3.0  # главное слово запроса есть в группе, но не как главное
    PREC_PEN = 5.0          # штраф за слова группы, которых нет в запросе
    # Взаимоисключающие модификаторы: «задний» не должен давать «Передний бампер».
    EXCLUSIVE_PAIRS = [
        ({"передн", "передня", "передне"}, {"задн", "задня", "задне"}),
        ({"верхн", "верхня", "верхне"}, {"нижн", "нижня", "нижне"}),
        ({"наружн", "наруж", "наружне"}, {"внутренн", "внутрення", "внутренне"}),
    ]
    MOD_CONFLICT_PENALTY = 15.0
    MOD_AGREE_BONUS = 2.0
    GROUP_HEAD_BONUS = 2.0   # главное слово запроса = главное слово названия группы
    # --- сходство с подтверждёнными примерами из истории (kNN) ---
    KNN_WEIGHT = 20.0      # вклад похожих примеров в score
    KNN_SECOND = 0.3       # вклад второго по сходству примера той же группы
    KNN_POWER = 2.0
    MOD_W_KNN = 0.3        # вес модификаторов (лев/прав/цвет...) при сравнении примеров
    EX_MISS_PEN = 0.3      # штраф сходства за слова примера, которых нет в запросе
    MISS_PEN = 0.8         # штраф сходства, если в запросе есть значимое слово, которого нет ни в примере, ни в его группе
    HEAD_MISS = 0.7        # штраф сходства, если главное слово запроса не главное в примере
    EXACT_BONUS = 8.0      # запрос = название группы (без модификаторов)
    FULLCOVER_BONUS = 2.0  # название группы (>=2 слов) целиком содержится в запросе
    CONFIDENCE_SCALE = 14.0
    LEARNED_WEIGHT = 1.5
    LEARNED_VOTE_CAP = 5
    HEAD_BONUS = 3.0

    AMBIGUITY_TIE_MARGIN = 0.8
    AMBIGUITY_MIN_RIVALS = 6
    AMBIGUITY_CONFIDENCE_CAP = 0.4

    # Калибровка по отрыву от второй группы: при отрыве 0 уверенность <= CAL_MIN,
    # при отрыве >= CAL_GAP_FULL — до CAL_MAX. 100% для скоринга не выдаётся.
    CAL_GAP_FULL = 6.0
    CAL_MIN = 0.5
    CAL_MAX = 0.99
    GENERIC_OVERRIDE_CAP = 0.7

    # Защита от ложной "коррекции опечатки" (фазорегулятор -> регулятор).
    TYPO_CUTOFF = 0.8
    TYPO_MAX_LEN_DIFF = 2

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
        self.history, self.learned = self._load_memory()
        self.load_history(self.history)

        self.group_stems = {g: self._extract_stems(self._clean(g), keep_latin=True) for g in groups}
        self._compute_idf()
        self._group_head = {}
        for g_, st_ in self.group_stems.items():
            lst_ = self._extract_stems_list(self._clean(g_), keep_latin=True)
            core_ = [w for w in lst_ if w not in self.MODIFIERS]
            self._group_head[g_] = core_[0] if core_ else None

        self._vocab = set()
        for stems in self.group_stems.values():
            self._vocab.update(stems)

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
        # типоразмер шины (175/65 R14, 205/55/16): главное слово — «шины»
        if self.TIRE_SIZE_RE.search(s) and not re.search(r'\bшин', s):
            s = 'шины ' + s
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

    def _modifier_adjust(self, q_stems, g_stems):
        adj = 0.0
        for A, B in self.EXCLUSIVE_PAIRS:
            qa, qb = q_stems & A, q_stems & B
            ga, gb = g_stems & A, g_stems & B
            if bool(qa) == bool(qb):
                continue
            if (qa and gb and not ga) or (qb and ga and not gb):
                adj -= self.MOD_CONFLICT_PENALTY
            elif (qa and ga) or (qb and gb):
                adj += self.MOD_AGREE_BONUS
        return adj

    def _stem(self, word: str) -> str:
        if len(word) == 4 and word[-1] in 'аыиуяеоь' and re.search(r'[а-яё]', word):
            return word[:-1]          # «фара» / «фары» -> «фар»
        if len(word) <= 4:
            return word
        # беглые гласные: «патрубок» -> «патрубк», «ремень» -> «ремн»
        if len(word) >= 6 and word.endswith('ок') and re.search(r'[а-яё]', word):
            return word[:-2] + 'к'
        if len(word) >= 5 and word.endswith('ень'):
            return word[:-3] + 'н'
        for end in self.STEM_ENDINGS:
            if word.endswith(end) and len(word) - len(end) >= 3:
                return word[:-len(end)]
        return word

    def _extract_stems_list(self, cleaned_text: str, keep_latin: bool = False) -> list[str]:
        """Основы слов с сохранением порядка. keep_latin=False для названий запчастей
        (латиница = марка/модель), True для названий групп (NOx, AdBlue, VVT...).
        Слова после «без»/«безо» игнорируются — это отрицания."""
        tokens = cleaned_text.split()
        words = []
        for i, w in enumerate(tokens):
            if i > 0 and tokens[i - 1] in ("без", "безо"):
                continue
            if i > 1 and tokens[i - 1] in ("с", "со", "под"):
                continue
            if len(w) >= 3 and w not in self.STOPWORDS:
                words.append(w)
        stems = []
        for w in words:
            if not keep_latin and re.match(r'^[a-z]+$', w):
                continue
            stems.append(self._stem(w) if re.search(r'[а-яё]', w) else w)
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
        if not Path(self.memory_file).exists():
            return {}, {}
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
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
        self._head_groups = defaultdict(lambda: defaultdict(int))
        self._h_inv = defaultdict(set)
        self._h_df = defaultdict(int)
        for k in self.history:
            self._index_key(k)
        self.learned = self._learned_from_history()

    def _index_key(self, k):
        lst = self._extract_stems_list(k)
        st = frozenset(lst)
        self._h_stems[k] = st
        core = [w for w in lst if w not in self.MODIFIERS]
        self._h_head[k] = frozenset(core[:2]) if core else frozenset(lst[:1])
        hw = self._first_head(lst)
        if hw and k in self.history:
            self._head_groups[hw][self.history[k]] += 1
        for w in st:
            self._h_inv[w].add(k)
            self._h_df[w] += 1

    def _first_head(self, lst):
        core = [w for w in lst if w not in self.MODIFIERS]
        core2 = [w for w in core if w not in self.WEAK_HEADS]
        return (core2 or core or [None])[0]

    def _unindex_key(self, k):
        st = self._h_stems.pop(k, frozenset())
        if k in self.history:
            hw = self._first_head(self._extract_stems_list(k))
            if hw and self._head_groups[hw].get(self.history[k]):
                self._head_groups[hw][self.history[k]] -= 1
        self._h_head.pop(k, None)
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
        """Для каждой группы: сходство запроса с лучшим подтверждённым примером
        этой группы (+ доля от второго лучшего)."""
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
            g_ex = self.history[k]
            for A, B in self.EXCLUSIVE_PAIRS:
                qa, qb = stems & A, stems & B
                if bool(qa) != bool(qb):
                    ha, hb = hs & A, hs & B
                    if ((qa and hb and not ha) or (qb and ha and not hb)) \
                            and (self.group_stems.get(g_ex, frozenset()) & (A | B)):
                        sim *= 0.25   # только если сама группа различает перед/зад
            known = hs | self.group_stems.get(g_ex, frozenset())
            qv = [w for w in stems if w not in self.MODIFIERS and w in self._vocab]
            if qv:
                tot_w = sum(self._word_weight(w) for w in qv)
                miss_w = sum(self._word_weight(w) for w in qv if w not in known)
                if tot_w > 0:
                    sim *= 1.0 - self.MISS_PEN * miss_w / tot_w
            if self.EX_MISS_PEN:
                ex_core = [w for w in hs if w not in self.MODIFIERS and w in self._vocab]
                if ex_core:
                    tot_e = sum(self._word_weight(w) for w in ex_core)
                    miss_e = sum(self._word_weight(w) for w in ex_core if w not in stems)
                    if tot_e > 0:
                        sim *= 1.0 - self.EX_MISS_PEN * miss_e / tot_e
            per_group[g_ex].append(sim)
        out = {}
        for g, sims in per_group.items():
            sims.sort(reverse=True)
            v = sims[0] ** self.KNN_POWER
            if len(sims) > 1:
                v += self.KNN_SECOND * sims[1] ** self.KNN_POWER
            out[g] = v
        return out

    def save_memory(self):
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump({"history": self.history, "learned": self.learned},
                       f, ensure_ascii=False, indent=2)

    def remember(self, part_name: str, group: str):
        """Запоминает выбор пользователя. История — источник истины;
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
        if head_word in self.WEAK_HEADS:
            alt = next((w for w in stems_list if w not in self.MODIFIERS and w not in self.WEAK_HEADS), None)
            if alt:
                head_word = alt

        # Опечатка в главном слове («Бампе» -> «бампер»). Кандидат должен
        # начинаться с той же буквы и мало отличаться по длине — иначе
        # «фазорегулятор» превращался в «регулятор».
        if head_word not in self._vocab and len(head_word) >= 4:
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

        knn = self._knn_scores(stems, head_word)
        hp = self._head_groups.get(head_word, {}) if self.HEAD_PRIOR_W else {}
        hp_tot = sum(hp.values()) if hp else 0
        candidates = []  # (score, group, common, base, coverage, head_bonus, learned_bonus)
        for group, group_stems in self.group_stems.items():
            common = stems & group_stems
            if not common:
                continue
            base = sum(self._word_weight(w) for w in common)
            coverage = len(common) / len(group_stems) if group_stems else 0
            head_bonus = self.HEAD_BONUS if head_word in group_stems else 0.0
            learned_bonus = knn.get(group, 0.0) * self.KNN_WEIGHT
            if self.HEAD_PRIOR_W and hp_tot >= self.HEAD_PRIOR_MIN:
                learned_bonus += self.HEAD_PRIOR_W * hp.get(group, 0) / hp_tot
            score = base + coverage * 2.5 + head_bonus + learned_bonus
            g_core = {w for w in group_stems if w not in self.MODIFIERS}
            mod_adj = self._modifier_adjust(stems, group_stems)
            if self.REPAIR_KIT_PEN and 'ремкомплект' in group_stems and 'ремкомплект' not in stems:
                score -= self.REPAIR_KIT_PEN
            if (self.HEAD_MISMATCH_PEN and head_word in group_stems and self._group_head.get(group)
                    and self._group_head[group] != head_word and group not in self.generic_groups):
                score -= self.HEAD_MISMATCH_PEN
            if self.PREC_PEN and group not in self.generic_groups and g_core:
                tot_g = sum(self._word_weight(w) for w in g_core)
                miss_g = sum(self._word_weight(w) for w in g_core if w not in stems)
                if tot_g > 0:
                    score -= self.PREC_PEN * miss_g / tot_g
            score += mod_adj
            if self._group_head.get(group) == head_word:
                score += self.GROUP_HEAD_BONUS
            q_core = {w for w in stems if w not in self.MODIFIERS}
            if g_core and g_core == q_core:
                score += self.EXACT_BONUS
            elif coverage >= 1.0 and len(g_core) >= 2:
                score += self.FULLCOVER_BONUS
            candidates.append((score, group, common, base, coverage, head_bonus, learned_bonus))
        if self.HEAD_PRIOR_W and hp_tot >= self.HEAD_PRIOR_MIN:
            seen_h = {c[1] for c in candidates}
            for group, n_ in hp.items():
                if n_ > 0 and group not in seen_h and group in self.group_stems and group not in knn:
                    lb = self.HEAD_PRIOR_W * n_ / hp_tot
                    candidates.append((lb, group, set(), 0.0, 0.0, 0.0, lb))
        # группы, найденные только по примерам из истории (нет общих слов с названием группы)
        seen = {c[1] for c in candidates}
        for group, kv in knn.items():
            if group in seen or group not in self.group_stems:
                continue
            lb = kv * self.KNN_WEIGHT
            candidates.append((lb, group, set(), 0.0, 0.0, 0.0, lb))

        if not candidates:
            debug["source"] = "none"
            return out(None, 0.0)

        candidates.sort(key=lambda c: c[0], reverse=True)
        best_score, best_group, best_common, best_base, best_coverage, best_head_bonus, best_learned = candidates[0]

        # Правило порога отрыва для обобщённых групп. Сравниваем по СТРУКТУРНОМУ
# score (без learned_bonus): специфичная группа не должна выигрывать
# лишь за счёт памяти, если структурно обобщённая группа не хуже.
        if self.generic_groups and best_group not in self.generic_groups:
            generic_candidates = [c for c in candidates if c[1] in self.generic_groups]
            if generic_candidates:
                best_generic = max(generic_candidates, key=lambda c: c[0])

                best_structural = best_score - best_learned
                best_generic_structural = best_generic[0] - best_generic[6]

                if (best_generic[0] >= self.THRESHOLD
                        and (best_structural - best_generic_structural) < self.generic_margin
                        and best_coverage < 1.0):
                    best_score, best_group, best_common, best_base, best_coverage, best_head_bonus, best_learned = best_generic
                    debug["generic_override"] = True

        # Ложная уверенность на одном общем слове
        ambiguous = False
        if len(best_common) <= 1 and best_group not in self.generic_groups and best_learned < 2.0:
            best_structural = best_score - best_learned
            rivals = sum(
                1 for c in candidates
                if c[1] != best_group
                and c[1] not in self.generic_groups
                and (best_structural - (c[0] - c[6])) <= self.AMBIGUITY_TIE_MARGIN
            )
            ambiguous = rivals >= self.AMBIGUITY_MIN_RIVALS
        debug["ambiguous"] = ambiguous

        # Отрыв от ближайшего конкурента (другая группа)
        second_score = max((c[0] for c in candidates if c[1] != best_group), default=0.0)
        gap = best_score - second_score
        debug["gap"] = round(gap, 2)

        def calibrate(score, gap_value):
            conf = min(score / self.CONFIDENCE_SCALE, 1.0)
            frac = max(min(gap_value / self.CAL_GAP_FULL, 1.0), 0.0)
            cap = self.CAL_MIN + (self.CAL_MAX - self.CAL_MIN) * frac
            return min(conf, cap)

        confidence_raw = min(best_score / self.CONFIDENCE_SCALE, 1.0)
        debug["confidence_raw"] = round(confidence_raw, 3)

        best_conf = calibrate(best_score, gap)
        if debug["generic_override"]:
            best_conf = min(best_conf, self.GENERIC_OVERRIDE_CAP)
        if ambiguous:
            best_conf = min(best_conf, self.AMBIGUITY_CONFIDENCE_CAP)

        # Топ-N альтернатив
        top_list = [(best_group, best_conf)]
        seen_groups = {best_group}
        for c in candidates:
            if len(top_list) >= max(top_n, 1):
                break
            g = c[1]
            if g in seen_groups:
                continue
            seen_groups.add(g)
            ratio = max(c[0], 0.0) / max(best_score, 1e-6)
            top_list.append((g, min(best_conf * ratio ** 2, best_conf)))
        debug["top"] = top_list

        if best_score >= self.THRESHOLD:
            debug.update({
                "source": "scoring",
                "matched_words": sorted(best_common),
                "base": round(best_base, 2),
                "coverage": round(best_coverage, 2),
                "head_bonus": round(best_head_bonus, 2),
                "learned_bonus": round(best_learned, 2),
                "score": round(best_score, 2),
            })
            return out(best_group, best_conf)

        debug.update({"source": "none", "score": round(best_score, 2)})
        return out(None, 0.0)