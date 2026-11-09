**Buffer pool decisions (section 5 leaves these open):**

1. **Pages are identified by (file, page id), not page id alone.**
   Why: section 1 makes page ids per file (every table's page 0 is its
   first directory page), but section 6 says catalog.db is read through
   the same buffer pool. With one shared pool, page 0 of R.db and page 0
   of catalog.db must not be confused. The `trace` command uses a single
   file, so its counters are unaffected.

2. **Empty frames are filled first, lowest index first, before asking the
   policy for a victim.**
   Why: section 5 says "the first B misses on an empty pool are not
   evictions", so an empty frame must be used without replacing anything.
   Taking the lowest index first makes the result deterministic, which the
   grader needs ("running the same command twice gives identical
   counters"), and it puts pages 1, 2, 3 in frames 0, 1, 2 as in the
   5.3 example.

3. **`new_page()` is not counted as a hit, miss, read or eviction. Its
   write is counted when the page is later flushed or evicted.**
   Why: the counting rules define hits, misses and evictions in terms of
   fetches, and a read as a call to the disk's read. `new_page()` is not a
   fetch and reads nothing (the page is zero-filled in memory). Counting
   it as a miss would add reads that never happened. The write rule
   ("whether caused by eviction or by flush") already covers it once the
   page reaches the disk.

4. **CLOCK advances the hand past every newly loaded page, including pages
   placed in empty frames.**
   Why: section 5.2 says "a newly loaded page gets reference bit 1 and
   the hand advances past it", with no exception for empty frames. This is
   also what reproduces 5.3: after loading 1, 2, 3 the hand is back at
   frame 0, so fetching 4 sweeps 1, 2, 3 (clearing their bits) and evicts
   1, giving [4, 2, 3] with 2 hits, 4 misses, 1 eviction. Our test
   `test_clock_pool_after_each_fetch` checks every step.
