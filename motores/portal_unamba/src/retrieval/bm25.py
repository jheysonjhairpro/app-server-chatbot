"""BM25 en Python puro (sin dependencias) para complementar la búsqueda vectorial.

Útil para siglas, nombres propios, códigos y fechas ("CPU", "Resolución 123", "2025-II")
que los embeddings suelen recuperar mal.
"""

import math
import re
import unicodedata
from collections import Counter, defaultdict

STOPWORDS = frozenset(
    """a al algo ante con contra cual cuales cuando cuanto cuantos cuanta cuantas de del desde donde e el ella
    ellos en entre es esa ese eso esta este esto estas estos hay la las le les lo los me mi mis mas muy ni no
    nos o para pero por que quien quienes se ser si sin sobre son su sus te tiene tienen tu tus u un una uno
    unas unos y ya puedo puede pueden hacer como""".split()
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _stem(word: str) -> str:
    """Stemming ligero y simétrico (consulta y documentos pasan por la misma función)."""
    if len(word) > 4 and word.endswith("es"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s"):
        word = word[:-1]
    if len(word) > 5 and word[-1] in "aeo":
        word = word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", normalize(text)) if t not in STOPWORDS and len(t) > 1]


class BM25Index:
    def __init__(self, ids: list[str], texts: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.ids = ids
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.doc_len: list[int] = []
        for index, text in enumerate(texts):
            tokens = tokenize(text)
            self.doc_len.append(len(tokens))
            for term, freq in Counter(tokens).items():
                self.postings[term].append((index, freq))
        self.n = len(ids)
        self.avg_len = (sum(self.doc_len) / self.n) if self.n else 0.0
        self._id_positions = {cid: index for index, cid in enumerate(ids)}
        self._term_documents = {term: {index for index, _ in postings} for term, postings in self.postings.items()}

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        if not self.n:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            postings = self.postings.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for index, freq in postings:
                norm = 1 - self.b + self.b * self.doc_len[index] / (self.avg_len or 1.0)
                scores[index] += idf * freq * (self.k1 + 1) / (freq + self.k1 * norm)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
        return [(self.ids[index], score) for index, score in ranked]

    def coverage(self, query_terms: set[str], cid: str) -> float:
        if not query_terms:
            return 0.0
        index = self._id_positions[cid]
        hits = sum(index in self._term_documents.get(term, ()) for term in query_terms)
        return hits / len(query_terms)
