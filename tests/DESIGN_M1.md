# Module 1 design: storage engine and buffer pool

This document records the design of the storage layer (`storage/`) and every
choice the Module 1 specification leaves open. Section numbers in brackets
refer to the Module 1 document.

Status key: ✅ written · ⏳ filled in at a later step

---

## 1. Layout of the code ✅

| File | Spec | Contents |
|---|---|---|
| `storage/errors.py` | §9 | `StorageError`, `RecordError`, `PageError`, `BufferPoolError` |
| `storage/record.py` | §2 | schema parsing, record `encode` / `decode` |
| `storage/page.py` | §3 | `SlottedPage` |
| `storage/disk.py` | §1 | `DiskManager` (a `.db` file), `MemoryDisk` (tests and traces) |
| `storage/buffer.py` | §5 | `BufferPool`, `LRUPolicy`, `ClockPolicy`, `run_trace` |
| `storage/heap.py` | §4 | heap file ⏳ step 3 |
| `storage/catalog.py` | §6 | catalog ⏳ step 4 |
| `storage/csvload.py` | §6 | CSV loader ⏳ step 5 |
| `storage.py` | §7 | command line ⏳ step 6 |

The library never prints and never reads a file except through the disk
manager. Only `storage.py` prints.

## 2. Records [§2] ✅

Implemented exactly as specified: INT is 4 bytes signed little-endian;
VARCHAR(n) is a 2-byte unsigned length followed by UTF-8 bytes; no header,
no null bitmap. The VARCHAR limit is checked in bytes of UTF-8, not in
characters, because the length prefix counts bytes.

## 3. Slotted page [§3] ✅

Implemented exactly as specified (header at offset 0, slot directory growing
forward, data growing backward). The 3.2 worked example and the 240 records
per page from 3.3 are reproduced by `tests/test_module1.py`.

### 3.1 Largest record: 4084 bytes, not 4088

§3.3 says a record longer than 4088 bytes cannot be stored. On an empty page
the free space is 4088 bytes, but every insert also needs a 4-byte slot
(§3.1: "an insert of a record of length L needs L + 4 bytes"). So the largest
record that fits is 4088 − 4 = **4084 bytes**. Records of 4085–4088 bytes pass
the 4088 rule but still do not fit, so we reject anything over 4084, with an
error naming the table and the row. Test:
`test_largest_record_fits_and_one_more_byte_does_not`.

### 3.2 Free-space strategy ⏳ step 3

<!-- How insert picks a page (first fit over the directory, §4.1), what
free_bytes in a directory entry means (free_end − (8 + 4 × slot_count)), and
when it is updated (after every insert and delete). -->

### 3.3 Compaction decision ⏳ step 3

<!-- Decide: no compaction (simplest; deleted space is never reused, as in the
3.2 example), or compaction and when it runs. If you implement it, the spec
requires a before/after page dump here. -->

### 3.4 Byte-level diagram of a real page ⏳ step 6

<!-- Required deliverable: dump one page from one of our own .db files (via
`storage layout`) and annotate the header, slot directory, free space and
records byte by byte. -->

## 4. Heap file [§4] ⏳ step 3

<!-- Directory page format (as specified), first-fit insert, how a new
directory page is chained when one fills (681 entries), and how the scan
keeps at most one directory page and one data page pinned. -->

## 5. Buffer pool [§5] ✅

LRU and CLOCK are implemented as specified. The 5.3 trace reproduces exactly:
LRU 1 hit, 5 misses, 2 evictions; CLOCK 2 hits, 4 misses, 1 eviction
(`tests/test_m1_buffer.py`, which also checks the pool contents after every
fetch).

Choices the specification leaves open:

1. **Pages are identified by (file, page id), not by page id alone.**
   Page ids restart at 0 in every file (§1), but catalog.db is read through
   the same buffer pool as the tables (§6). With one shared pool, page 0 of
   `R.db` and page 0 of `catalog.db` must not be confused. The `trace`
   command uses one file, so its counters are unaffected.

2. **Empty frames are filled first, lowest index first, before the policy
   is asked for a victim.** §5 says "the first B misses on an empty pool are
   not evictions", so an empty frame must be used without replacing anything.
   Taking the lowest index first makes runs deterministic ("running the same
   command twice gives identical counters") and places pages 1, 2, 3 in
   frames 0, 1, 2 as in 5.3.

3. **`new_page()` counts as no hit, no miss, no read and no eviction; its
   write is counted when the page is later flushed or evicted.** The
   counting rules define hits, misses and evictions in terms of fetches, and
   a read as a call to the disk's read. `new_page()` is not a fetch and reads
   nothing (the page is zero-filled in memory). The write rule ("whether
   caused by eviction or by flush") covers it once the page reaches disk.

4. **CLOCK advances the hand past every newly loaded page, including pages
   placed in empty frames.** §5.2 says "a newly loaded page gets reference
   bit 1 and the hand advances past it", with no exception for empty frames.
   This is also what reproduces 5.3: after loading 1, 2, 3 the hand is back
   at frame 0, so fetching 4 clears 1, 2, 3 and evicts 1.

## 6. Catalog [§6] ⏳ step 4

<!-- Required: our on-page format for catalog.db. For each table: name,
ordered columns with type and length, data file name, number of pages,
number of live tuples. Leave room for Module 4 statistics (say how). -->

## 7. CSV loader [§6] ⏳ step 5

<!-- Hand-written reader (no csv module? decide and say why), quoting rules,
how a bad INT or an over-long VARCHAR is reported (line number and column),
and that the load stops at the first error. -->

## 8. Traces ✅ (source) / ⏳ step 7 (files)

The five published traces will not be released. The professor's reply
(2026-11-08):

> "Traces. Write your own. Start with the worked trace in M1 Section 5.3 and
> match its counts. Then build the scan trace described in Section 8, an 80
> page file scanned ten times, plus a few of your own. Where something is
> unclear, make a reasonable choice and state it in your design document."

So our traces are:

| # | File | What it is |
|---|---|---|
| 1 | `traces/t1_worked.txt` | 1 2 3 1 4 2 (§5.3) |
| 2 | ⏳ | ⏳ |
| 3 | `traces/t3_scan80x10.txt` | pages 0–79 in order, repeated 10 times (§8) |
| 4 | ⏳ | ⏳ |
| 5 | ⏳ | ⏳ |

<!-- Fill in 2, 4, 5 at step 7 (for example a hot/cold skewed trace and a
random uniform trace), and say why each was chosen. -->

## 9. Errors [§9] ✅ partly

| Case | Behaviour | Where |
|---|---|---|
| Evicting a pinned page | never happens; fetch raises `BufferPoolError` "no unpinned frame available" | `buffer.py` ✅ |
| Unpin at pin count 0 | `BufferPoolError` | `buffer.py` ✅ |
| Reading a dead slot | `PageError` naming the rid | `page.py` ✅ |
| Record over 4084 bytes | error naming table and row | ⏳ step 3 |
| Bad CSV field | error naming line and column | ⏳ step 5 |

