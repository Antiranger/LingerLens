"""Bounded session ledgers. Old numeric ids below the retired frontier are stale."""
from collections import OrderedDict
import math

class RecentIds:
    def __init__(self, limit=512):
        self.limit=limit
        self._ids=OrderedDict()
    def __contains__(self,key): return key in self._ids
    def __len__(self): return len(self._ids)
    def add(self,key):
        self._ids[key]=None
        while len(self._ids)>self.limit: self._ids.popitem(last=False)
    def discard(self,key): self._ids.pop(key,None)
    def clear(self): self._ids.clear()

class NumericTombstones(RecentIds):
    """Suppress repeated full-history results without retaining session history.

    Evidence older than the retired numeric start-time frontier is stale.
    This is a bounded-live-window policy, not proof that every old id finalized.
    """
    def __init__(self, limit=512):
        super().__init__(limit)
        self.floor=-math.inf
    def __contains__(self,key):
        try: return float(key)<=self.floor or key in self._ids
        except (ValueError,TypeError): return key in self._ids
    def add(self,key):
        if key in self: return
        self._ids[key]=None
        while len(self._ids)>self.limit:
            oldest=min(self._ids,key=lambda value:float(value))
            self.floor=max(self.floor,float(oldest))
            self._ids.pop(oldest)
