from collections import OrderedDict
import time


class TTLCache:
    def __init__(self, size, ttl):
        self.size, self.ttl = size, ttl
        self.data = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        item = self.data.get(key)
        if item and time.monotonic() - item[0] < self.ttl:
            self.data.move_to_end(key)
            self.hits += 1
            return item[1]
        self.data.pop(key, None)
        self.misses += 1
        return None

    def put(self, key, value):
        self.data[key] = (time.monotonic(), value)
        self.data.move_to_end(key)
        while len(self.data) > self.size:
            self.data.popitem(last=False)

    def stats(self):
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / total if total else 0,
            "entries": len(self.data),
        }
