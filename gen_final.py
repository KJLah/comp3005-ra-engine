#!/usr/bin/env python3
"""Dress rehearsal for the demo (Section 6): our dataset as CSV files.

    python3 gen_final.py [--out data/final] [--scale 1]

The demo dataset is published 72 hours before the demo, so this writes our
Module 4 data (gen_m4.py, same seed) as one CSV file per table, each with a
header line, the way a published dataset most likely arrives. The shell
then builds the database from scratch with queries/final/setup.sql
(CREATE TABLE, LOAD ... HEADER, ANALYZE), and run_workload.py runs the 25
queries of queries/final/workload.sql.
"""
from __future__ import annotations

import argparse
import os

from gen_m4 import SCHEMAS, rows


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="data/final")
    ap.add_argument("--scale", type=int, default=1)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    for name, data in rows(a.scale).items():
        header = ",".join(part.split(":")[0] for part in SCHEMAS[name].split(","))
        with open(os.path.join(a.out, f"{name}.csv"), "w", encoding="utf-8") as f:
            f.write(header + "\n" + "\n".join(data) + "\n")
        print(f"wrote {a.out}/{name}.csv ({len(data)} rows)")


if __name__ == "__main__":
    main()