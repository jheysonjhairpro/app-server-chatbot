"""Caché en memoria; almacena únicamente respuestas ya validadas."""
from collections import OrderedDict
from copy import deepcopy
from time import monotonic


class VerifiedAnswerCache:
    def __init__(self, capacity=128, ttl=300, clock=monotonic):
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self.entries = OrderedDict()

    def get(self, question, revision):
        key = (' '.join(question.casefold().split()), revision)
        entry = self.entries.get(key)
        if not entry:
            return None
        timestamp, answer = entry
        if self.clock() - timestamp >= self.ttl:
            del self.entries[key]
            return None
        self.entries.move_to_end(key)
        return deepcopy(answer)

    def put(self, question, revision, answer):
        if self.capacity <= 0 or self.ttl <= 0 or answer.abstained or not answer.sources:
            return
        key = (' '.join(question.casefold().split()), revision)
        self.entries[key] = (self.clock(), deepcopy(answer))
        self.entries.move_to_end(key)
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
