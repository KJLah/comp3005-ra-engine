"""Buffer pool with LRU and CLOCK replacement (Module 1, section 5).

Counting rules (must be exact, the grader compares them):
  hit       fetch of a resident page
  miss      fetch that needed a disk read
  eviction  a miss that replaced a resident page (filling an empty frame is not one)
  read      one call to disk.read_page
  write     one call to disk.write_page (eviction or flush)

One pool serves several files (catalog.db and every table), so a page is
identified by (disk, page_id), not by page_id alone.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

from .errors import BufferPoolError
from .page import PAGE_SIZE

Key = Tuple[int, int]          # (id(disk), page_id)


class Frame:
    __slots__ = ("index", "key", "disk", "page_id", "data", "pin_count", "dirty")

    def __init__(self, index: int):
        self.index = index
        self.key: Optional[Key] = None
        self.disk = None
        self.page_id: Optional[int] = None
        self.data = bytearray(PAGE_SIZE)
        self.pin_count = 0
        self.dirty = False


# ---- replacement policies ------------------------------------------------------
class LRUPolicy:
    name = "lru"

    def __init__(self, frames: List[Frame]):
        self.frames = frames
        self.order: "OrderedDict[int, None]" = OrderedDict()   # oldest first

    def on_access(self, frame: Frame, loaded: bool) -> None:
        # every fetch, hit or miss, makes the frame most recently used
        self.order[frame.index] = None
        self.order.move_to_end(frame.index)

    def choose_victim(self) -> Optional[Frame]:
        for i in self.order:
            if self.frames[i].pin_count == 0:
                return self.frames[i]
        return None


class ClockPolicy:
    name = "clock"

    def __init__(self, frames: List[Frame]):
        self.frames = frames
        self.ref = [0] * len(frames)
        self.hand = 0

    def on_access(self, frame: Frame, loaded: bool) -> None:
        self.ref[frame.index] = 1
        if loaded:
            # "A newly loaded page gets reference bit 1 and the hand advances past it."
            self.hand = (frame.index + 1) % len(self.frames)

    def choose_victim(self) -> Optional[Frame]:
        b = len(self.frames)
        swept = 0
        while True:
            f = self.frames[self.hand]
            if f.pin_count > 0:
                self.hand = (self.hand + 1) % b
                swept += 1
            elif self.ref[f.index] == 1:
                self.ref[f.index] = 0
                self.hand = (self.hand + 1) % b
                swept += 1
            else:
                self.hand = (self.hand + 1) % b
                return f
            if swept > 2 * b:
                return None          # every frame pinned


POLICIES = {"lru": LRUPolicy, "clock": ClockPolicy}


# ---- the pool ---------------------------------------------------------------------
class BufferPool:
    def __init__(self, num_frames: int, policy: str = "lru"):
        if num_frames < 1:
            raise BufferPoolError("the buffer pool needs at least one frame")
        if policy not in POLICIES:
            raise BufferPoolError(f"unknown policy '{policy}' (use lru or clock)")
        self.frames = [Frame(i) for i in range(num_frames)]
        self.policy = POLICIES[policy](self.frames)
        self.page_table: Dict[Key, int] = {}
        self.free = list(range(num_frames))     # empty frames, used lowest first
        self.hits = self.misses = self.evictions = self.reads = self.writes = 0

    # ---- helpers ------------------------------------------------------------
    def _write(self, frame: Frame) -> None:
        frame.disk.write_page(frame.page_id, bytes(frame.data))
        self.writes += 1
        frame.dirty = False

    def _take_frame(self) -> Tuple[Frame, bool]:
        """An empty frame if there is one, else a victim. Returns (frame, evicted)."""
        if self.free:
            return self.frames[self.free.pop(0)], False
        frame = self.policy.choose_victim()
        if frame is None:
            raise BufferPoolError("no unpinned frame available")
        if frame.dirty:
            self._write(frame)
        del self.page_table[frame.key]
        return frame, True

    def _install(self, frame: Frame, disk, page_id: int) -> None:
        frame.key = (id(disk), page_id)
        frame.disk = disk
        frame.page_id = page_id
        frame.pin_count = 1
        frame.dirty = False
        self.page_table[frame.key] = frame.index

    # ---- section 5 interface -------------------------------------------------
    def fetch_page(self, disk, page_id: int) -> bytearray:
        key = (id(disk), page_id)
        if key in self.page_table:
            frame = self.frames[self.page_table[key]]
            frame.pin_count += 1
            self.hits += 1
            self.policy.on_access(frame, loaded=False)
            return frame.data
        frame, evicted = self._take_frame()
        frame.data[:] = disk.read_page(page_id)
        self.reads += 1
        self._install(frame, disk, page_id)
        self.misses += 1
        if evicted:
            self.evictions += 1
        self.policy.on_access(frame, loaded=True)
        return frame.data

    def unpin_page(self, disk, page_id: int, is_dirty: bool) -> None:
        key = (id(disk), page_id)
        if key not in self.page_table:
            raise BufferPoolError(f"unpin of page {page_id} of {disk.path}: not resident")
        frame = self.frames[self.page_table[key]]
        if frame.pin_count == 0:
            raise BufferPoolError(f"unpin of page {page_id} of {disk.path}: "
                                  f"pin count is already 0")
        frame.dirty = frame.dirty or is_dirty
        frame.pin_count -= 1

    def new_page(self, disk) -> Tuple[int, bytearray]:
        """Allocate a page on `disk` and return (page_id, frame data): zero
        filled, pinned, dirty. Not a fetch, so it is neither a hit nor a miss
        and does no READ; replacing a page for it is not counted as an
        eviction (the eviction rule only counts misses). Documented in DESIGN_M1."""
        page_id = disk.allocate_page()
        frame, _ = self._take_frame()
        frame.data[:] = bytes(PAGE_SIZE)
        self._install(frame, disk, page_id)
        frame.dirty = True
        self.policy.on_access(frame, loaded=True)
        return page_id, frame.data

    def flush_all(self) -> None:
        for frame in self.frames:
            if frame.key is not None and frame.dirty:
                self._write(frame)

    def stats(self) -> Dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions,
                "reads": self.reads, "writes": self.writes}

    def resident(self) -> List[Optional[int]]:
        """Page id in each frame, in frame order (None = empty). For tests and debugging."""
        return [f.page_id if f.key is not None else None for f in self.frames]

    def pinned_count(self) -> int:
        return sum(1 for f in self.frames if f.pin_count > 0)


def run_trace(page_ids: Iterable[int], frames: int, policy: str) -> Dict[str, int]:
    """Section 7 `trace`: fetch each page id and unpin it immediately as clean."""
    from .disk import MemoryDisk
    disk = MemoryDisk("<trace>")
    pool = BufferPool(frames, policy)
    for pid in page_ids:
        pool.fetch_page(disk, pid)
        pool.unpin_page(disk, pid, False)
    return {"policy": policy, "frames": frames, **pool.stats()}
