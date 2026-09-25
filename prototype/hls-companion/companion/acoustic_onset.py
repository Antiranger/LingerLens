"""Conservative acoustic onset refinement on the decoded PCM clock.

This is NOT a speech recognizer. It corrects only a nearby, unambiguous transition
from at least 80 ms of near-digital silence to sustained audible signal. With
music, noise, missing samples or ambiguous boundaries the provider timing wins.
No raw PCM is retained: one energy summary per 10 ms, bounded to 120 seconds.
"""
from __future__ import annotations
from array import array
from collections import deque
from dataclasses import dataclass
import math
import sys

@dataclass(frozen=True)
class _Frame:
    start: float
    end: float
    rms: float

class AcousticOnsetIndex:
    MAX_SHIFT = 0.30
    QUIET_RMS = 20.0
    ACTIVE_RMS = 80.0
    def __init__(self, sample_rate: int):
        if sample_rate not in (16000,24000):
            raise ValueError('sample rate must be 16000 or 24000')
        self.sample_rate=sample_rate
        self.frame_samples=sample_rate//100
        self.frame_bytes=2*self.frame_samples
        self.frames: deque[_Frame]=deque(maxlen=12000)
        self._tail=bytearray()
        self._tail_start: float | None=None
        self._next_pcm: float | None=None

    def feed(self, pcm: bytes, start: float) -> None:
        if not pcm or len(pcm)%2 or not math.isfinite(start):
            return
        if self._next_pcm is None or abs(start-self._next_pcm)>1/self.sample_rate:
            self._tail.clear();self._tail_start=start
        self._next_pcm=start+len(pcm)/(2*self.sample_rate)
        if self._tail_start is None: self._tail_start=start
        self._tail.extend(pcm)
        consumed=0
        while len(self._tail)-consumed>=self.frame_bytes:
            samples=array('h', self._tail[consumed:consumed+self.frame_bytes])
            if sys.byteorder!='little':samples.byteswap()
            rms=math.sqrt(sum(v*v for v in samples)/self.frame_samples)
            begin=self._tail_start+consumed/(2*self.sample_rate)
            self.frames.append(_Frame(begin,begin+.01,rms))
            consumed+=self.frame_bytes
        if consumed:
            del self._tail[:consumed]
            self._tail_start+=consumed/(2*self.sample_rate)

    def refine(self, proposed: float, end: float) -> float:
        if not math.isfinite(proposed) or not math.isfinite(end):return proposed
        frames=[f for f in self.frames if proposed-.45<=f.start<=proposed+.40]
        quiet=0;candidate=None;active=0;last_end=None;candidates=[]
        for f in frames:
            if last_end is not None and abs(f.start-last_end)>.002:
                quiet=0;candidate=None;active=0
            last_end=f.end
            if f.rms<=self.QUIET_RMS:
                quiet+=1;active=0;candidate=None
            else:
                if quiet>=8:
                    candidate=f.start
                quiet=0
                if candidate is None:continue
                if f.rms>=self.ACTIVE_RMS:active+=1
                elif f.start-candidate>.05:candidate=None;active=0
                if active>=4 and candidate is not None:
                    if abs(candidate-proposed)<=self.MAX_SHIFT and candidate<end:
                        candidates.append(candidate)
                    candidate=None;active=0
        # Multiple nearby sound starts do not establish which word was meant.
        return candidates[0] if len(candidates)==1 else proposed
