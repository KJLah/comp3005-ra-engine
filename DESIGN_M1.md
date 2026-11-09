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
| `storage/heap.py` | §4 | `DirectoryPage`, `HeapFile` |
| `storage/catalog.py` | §6 | `Catalog`, `TableInfo` |
| `storage/csvload.py` | §6 | `split_line`, `load_csv` |
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

### 3.2 Free-space strategy ✅

Each directory entry stores its data page's free space exactly as §3 defines
it: `free_end − (8 + 4 × slot_count)`. An insert of a record of L bytes goes
to the **first** data page, in directory order, whose `free_bytes ≥ L + 4`
(first fit, §4.1). If no page has room, a new data page is appended. The
entry is rewritten after every insert, so the directory never has to open a
data page to find out whether it has room.

Why first fit: it is what §4.1 specifies, it is deterministic (the grader
compares counters), and it keeps older pages full, so a scan touches as few
pages as possible.

### 3.3 Compaction decision ✅

**We do not compact.** A delete only marks the slot dead (offset 0, length 0);
the record's bytes stay where they are, and the slot is never reused (§3:
"Slot numbers are never reused"). As the 3.2 example shows, the deleted space
is therefore not usable again, and the directory's free_bytes does not change.

Why: §3.2 says compaction is optional. Without it a rid can never move,
nothing has to be rewritten on delete, and the page bytes match the 3.2
example exactly. The cost is wasted space in tables with many deletes, which
no Module 1 measurement depends on.

Delete still follows §4.1 ("update directory free_bytes"): it finds the
entry for the page and rewrites it. Since the value cannot change without
compaction, the directory page is only marked dirty if it actually changed,
so a delete never causes a needless write.

### 3.4 Byte diagram ✅

Table R(a INT, b VARCHAR(20), c INT) after loading `42,bob,7` and `43,"a, b",9`.
Output of `python3 -m storage layout --db /tmp/db --table R --page 1 --hex`:

```
data page: page_id=1 slot_count=2 free_end=4069 free_space=4053
  slot 0: (4083,13) live
  slot 1: (4069,14) live
  0000  01 00 00 00 02 00 e5 0f f3 0f 0d 00 e5 0f 0e 00
  4064  00 00 00 00 00 2b 00 00 00 04 00 61 2c 20 62 09
  4080  00 00 00 2a 00 00 00 03 00 62 6f 62 07 00 00 00
```

All integers are little-endian, so `e5 0f` is 0x0FE5 = 4069.

| Bytes | Hex | Meaning |
|---|---|---|
| 0–3 | `01 00 00 00` | page_id = 1 |
| 4–5 | `02 00` | slot_count = 2 |
| 6–7 | `e5 0f` | free_end = 4069 |
| 8–11 | `f3 0f 0d 00` | slot 0 = (offset 4083, length 13) |
| 12–15 | `e5 0f 0e 00` | slot 1 = (offset 4069, length 14) |
| 16–4068 | zeros | free space: 4069 − 16 = 4053 bytes |
| 4069–4072 | `2b 00 00 00` | record 1, a = 43 |
| 4073–4074 | `04 00` | record 1, b's length = 4 bytes |
| 4075–4078 | `61 2c 20 62` | record 1, b = "a, b" |
| 4079–4082 | `09 00 00 00` | record 1, c = 9 |
| 4083–4086 | `2a 00 00 00` | record 0, a = 42 |
| 4087–4088 | `03 00` | record 0, b's length = 3 bytes |
| 4089–4091 | `62 6f 62` | record 0, b = "bob" |
| 4092–4095 | `07 00 00 00` | record 0, c = 7 |

**What it shows**
- The slot directory grows forward from byte 8, and records grow backward from byte 4095. The free space is the gap between them.
- Record 0 (13 bytes) was inserted first, so it sits at the very end of the page. Record 1 (14 bytes) goes just in front of it, and free_end moves from 4083 to 4069.
- free_space = free_end − (8 + 4 × slot_count) = 4069 − 16 = 4053, which matches the directory entry on page 0 (`free_bytes=4053`).
- The comma inside `"a, b"` is stored as plain data (`2c`). The CSV quotes are gone after loading.

## 4. Heap file [§4] ✅

Directory pages use exactly the §4 layout (page_id, next_dir_page with
0xFFFFFFFF for none, entry_count, 6-byte entries; 681 per page). Page 0 is
always the first directory page.

- **Where new pages go.** Every new page (data or directory) is appended at
  the end of the file. When the last directory page is full (681 entries),
  a new directory page is appended, linked from the old one, and the new
  data page is listed there. So directory and data pages can interleave in
  the file; the directory chain is the only way to find them.
- **Pinning.** No heap-file method ever holds more than one directory page
  and one data page pinned at once, so the heap file needs a pool of at
  least B = 2. With B = 1, insert fails with "no unpinned frame available"
  (tested).
- **Scan.** `scan()` is a generator: it pins one directory page, then for
  each entry pins the data page, yields its live records one at a time,
  and unpins the data page before moving to the next entry. Nothing is
  collected in a list. If the caller stops early, `finally` blocks unpin
  both pages. Tested on a table 20× larger than the pool (80 data pages,
  B = 4): at most 2 pages are ever pinned.
- **Scan cost.** On a cold pool, a scan reads every page exactly once
  (1 directory + N data pages), whatever B is, because each page is
  visited once in order. Tested at B = 2, 8 and 64 (11 reads for 10 data
  pages in every case).
- **get(rid)** fetches only the data page (one page access).

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

## 6. Catalog [§6] ✅

**Where it lives.** `catalog.db` is itself a heap file with exactly the §4
layout (page 0 is its first directory page), so it is read and written
through the buffer pool like any other data, as §6 requires. Each table is one
record in it.

**Record format** (all integers little-endian, strings are a uint16 byte
length followed by UTF-8):

| Field | Size | Notes |
|---|---|---|
| format version | 1 byte | currently 1 |
| table name | 2 + n | |
| data file name | 2 + n | always `<name>.db` |
| number of pages | 4 (uint32) | directory + data pages |
| number of live tuples | 4 (uint32) | |
| column count | 2 (uint16) | |
| per column: name | 2 + n | in schema order |
| per column: type | 1 | 0 = INT, 1 = VARCHAR |
| per column: length | 2 (uint16) | n of VARCHAR(n); 0 for INT |
| statistics | 2 + n | UTF-8 JSON, `{}` in Module 1 |

Example: table `R(a INT, b VARCHAR(20), c INT)` with 3 pages and 300 tuples
is one 42-byte record. <!-- check with `storage layout --db DIR --table catalog --page 1` at step 6 -->

**Room for Module 4.** The statistics field is last and variable length, so
Module 4 can store distinct counts, min/max or histograms there without
changing any field before it. The version byte lets a later format be told
apart from this one.

**Why a heap file and not a custom page format.** It reuses code that is
already tested (insert, delete, the two-pin scan), and §6 only asks that the
catalog is read through the buffer pool. A table lookup scans the catalog,
which is a few pages at most for the number of tables in this project.

**Updating counts.** `update()` deletes the table's old record and inserts the
new one (its rid may change; nothing stores catalog rids). Since we do not
compact (3.3), each update leaves a dead slot behind in catalog.db; at a few
dozen bytes per update this does not matter for this project.

**One DiskManager per file.** The buffer pool tells files apart by their
DiskManager object (decision 5.1), so the catalog keeps exactly one per file
and every heap for that table uses it.

**Table names.** Letters, digits and `_`, starting with a letter or `_`,
because the name becomes a file name; `catalog` is reserved.

**Limit.** A catalog record must fit on one page (4084 bytes, 3.1). That
allows hundreds of columns now; if Module 4 statistics grow past it, they
will have to move to their own file. <!-- revisit in Module 4 -->


## 7. CSV loader [§6] ✅

**Hand-written parser.** `storage/csvload.py` splits each line character by
character (no `csv` module, no regex), so every rule below is visible in our
own code and can be explained in the oral check. Supported: comma-separated
fields, double-quoted fields that may contain commas, and `""` inside quotes
for one `"`. A quoted field cannot span lines.

**Choices the specification leaves open:**

1. **All or nothing.** The loader checks the whole file first and only then
   inserts. If any line is bad, the error names it and the table is left
   exactly as it was. Why: §6 says "the load stops"; doing a first checking
   pass means a half-loaded table can never happen, so the catalog's tuple
   count never disagrees with the file.
2. **INT fields may have spaces around the number** (`" 42 "` is 42);
   a sign is allowed; the value must fit in a signed 32-bit integer (§2).
   **VARCHAR fields are kept exactly**, spaces included, because a space can
   be real data.
3. **VARCHAR length is counted in UTF-8 bytes**, because the §2 length
   prefix counts bytes. `é` counts as 2.
4. **Empty lines.** A newline at the very end of the file is ignored. Any
   other empty line is an error (it would be a record with the wrong number
   of fields).
5. **Windows line endings** (`\r\n`) are accepted.

**Error messages** (§9 "Bad CSV field: error naming the line number and the
column"):

| Problem | Message (example) |
|---|---|
| INT does not parse | `line 3, column 'c': 'x7' is not a 32-bit INT` |
| VARCHAR too long | `line 1, column 'b': value is 11 bytes, longer than VARCHAR(10)` |
| wrong field count | `line 1: expected 3 fields, found 2` |
| unterminated quote | `line 5, column 2: unterminated quoted field` |
| record over 4084 bytes | `table W, line 1: record is 4112 bytes, longer than the 4084-byte maximum` |

**After a load** the catalog's page and live-tuple counts are recounted and
saved, and the buffer pool is flushed, so the files on disk are complete.

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
| Record over 4084 bytes | `RecordError` naming the table (the loader adds the row number, step 5) | `heap.py` ✅ |
| Bad CSV field | ` LoadError` error naming line and column | `csvload.py`  ✅ |

## 10. Command line ✅

`storage/cli.py`, run as `python3 -m storage <command>` from the repo root.

**Decisions and why**

1. **`python3 -m storage` instead of a `storage` script.** The spec writes `storage scan ...`. A `storage.py` file at the root would clash with the `storage/` package, so `storage/__main__.py` is the entry point. Nothing needs installing.
2. **The catalog gets its own small pool (4 frames).** `scan --frames B` builds a fresh pool for the table only. So its counters count only the table's pages, and the same command always prints the same numbers. A scan of a 1 + 9 page table reads 10 pages at B = 2, 8 and 64.
3. **`scan` needs B ≥ 2.** A scan pins one directory page and one data page at the same time (section 4). B = 1 gives a clear error instead of "no unpinned frame".
4. **`--limit` closes the scan generator.** Its `finally` unpins the open pages, so stopping early leaves nothing pinned.
5. **Errors are one line.** Every `StorageError` prints `Error: ...` and exits 1. Anything else prints `Internal error: ...` and exits 2. Neither shows a traceback.
6. **`layout` reads the page through a 1-frame pool.** It works out the page type itself: page 0 and every page on the `next_dir` chain are directory pages, the rest are slotted pages. `--hex` adds the raw bytes with decimal offsets (all-zero lines are skipped), which is what section 3.4 shows. `--table catalog` shows `catalog.db`.
7. **`trace` ignores `--db`.** It runs on an in-memory disk (section 8), but it accepts `--db` so the spec's command line works as written.

