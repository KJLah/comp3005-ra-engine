#!/usr/bin/env python3
"""Module 4 dataset (our own: the handout's dataset was not published).

    python3 gen_m4.py --db data/m4 [--scale 1]

Four tables, deterministic (random.Random(3005)), loaded straight into a
Module 1 database and then ANALYZEd:

    Dept(did, dname, budget, city)                         40 rows
    Emp(eid, name, age, salary, did, city, province, area, note)   2000 rows
    Proj(pid, pname, did, budget)                          300 rows
    Works(eid, pid, hours, role, note)                     6000 rows

Built so that each part of the report has something to find:

* Correlation (the "where it breaks" study). Emp.city decides
  Emp.province (20 cities, 5 in each of 4 provinces) and Emp.area (one area
  code per city). city = 'Ottawa' AND area = '201' has true selectivity
  sel(city), about 0.19, but is estimated as sel(city) x sel(area), about
  0.036: five times too small (q17). city AND province is off by about
  2.8 times (q18). Emp.salary also rises with Emp.age (weaker).
* Skew. Cities follow a Zipf-like distribution (Ottawa and Toronto are
  common, the last cities rare), so a histogram beats 1/ndv.
* Keys. did is a key of Dept and pid of Proj, so those joins are
  foreign-key joins where n(R) x n(S) / max(ndv) is right; Works.eid is not
  a key of anything, and some employees work on no project.
* note columns pad the records so the tables span tens of pages, enough
  for B to matter (Emp 47 pages, Works 100).
"""
from __future__ import annotations

import argparse
import os
import random
import shutil

from storage.buffer import BufferPool
from storage.catalog import Catalog
from storage.csvload import load_csv
from storage.record import parse_schema

PROVINCES = {
    "ON": ["Ottawa", "Toronto", "Kingston", "London", "Windsor"],
    "QC": ["Montreal", "Quebec", "Gatineau", "Laval", "Sherbrooke"],
    "BC": ["Vancouver", "Victoria", "Surrey", "Kelowna", "Burnaby"],
    "AB": ["Calgary", "Edmonton", "RedDeer", "Banff", "Jasper"],
}
CITY_PROV = {c: p for p, cs in PROVINCES.items() for c in cs}
# Zipf-like weights, cities ordered so ON is the most common province
CITIES = ["Ottawa", "Toronto", "Montreal", "Vancouver", "Calgary", "Kingston", "Quebec",
          "Victoria", "Edmonton", "London", "Gatineau", "Surrey", "RedDeer", "Windsor",
          "Laval", "Kelowna", "Banff", "Sherbrooke", "Burnaby", "Jasper"]
AREA = {c: str(201 + 37 * i) for i, c in enumerate(CITIES)}     # one area code per city
WEIGHTS = [1 / (i + 1) ** 0.8 for i in range(len(CITIES))]
ROLES = ["dev", "test", "lead", "ops", "design", "pm"]
WORDS = ["alpha", "bravo", "delta", "echo", "gamma", "kilo", "lima", "omega", "sigma", "zulu"]

SCHEMAS = {
    "Dept": "did:INT,dname:VARCHAR(20),budget:INT,city:VARCHAR(16)",
    "Emp": "eid:INT,name:VARCHAR(24),age:INT,salary:INT,did:INT,city:VARCHAR(16),"
           "province:VARCHAR(4),area:VARCHAR(4),note:VARCHAR(80)",
    "Proj": "pid:INT,pname:VARCHAR(20),did:INT,budget:INT",
    "Works": "eid:INT,pid:INT,hours:INT,role:VARCHAR(12),note:VARCHAR(80)",
}


def note(rng, n):
    return "-".join(rng.choice(WORDS) for _ in range(n))[:80]


def rows(scale: int = 1):
    rng = random.Random(3005)
    n_dept, n_emp, n_proj, n_works = 40, 2000 * scale, 300, 6000 * scale
    dept = [f"{d},Dept{d:02d},{rng.randint(10, 200) * 1000},{rng.choices(CITIES, WEIGHTS)[0]}"
            for d in range(1, n_dept + 1)]
    emp = []
    for e in range(1, n_emp + 1):
        age = rng.randint(20, 65)
        salary = 30000 + age * 1000 + rng.randint(0, 20) * 1000      # rises with age
        city = rng.choices(CITIES, WEIGHTS)[0]
        emp.append(f"{e},Emp{e:05d},{age},{salary},{rng.randint(1, n_dept)},{city},"
                   f"{CITY_PROV[city]},{AREA[city]},{note(rng, 8)}")
    proj = [f"{p},Proj{p:03d},{rng.randint(1, n_dept)},{rng.randint(1, 100) * 1000}"
            for p in range(1, n_proj + 1)]
    works = []
    for _ in range(n_works):
        e = rng.randint(1, int(n_emp * 0.9))          # the last 10% work on nothing
        works.append(f"{e},{rng.randint(1, n_proj)},{rng.randint(1, 40)},"
                     f"{rng.choice(ROLES)},{note(rng, 8)}")
    return {"Dept": dept, "Emp": emp, "Proj": proj, "Works": works}


def build(db: str, scale: int = 1, analyze_too: bool = True, buckets: int = 20) -> None:
    if os.path.exists(db):
        shutil.rmtree(db)
    os.makedirs(db)
    cat = Catalog(db, BufferPool(16))
    data = rows(scale)
    for name, schema in SCHEMAS.items():
        cat.create_table(name, parse_schema(schema))
        load_csv(cat, name, data[name])
    cat.flush()
    if analyze_too:
        from optimizer.stats import analyze
        for name in SCHEMAS:
            analyze(db, name, buckets)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/m4")
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--no-analyze", action="store_true")
    a = ap.parse_args()
    build(a.db, a.scale, not a.no_analyze)
    print(f"built {a.db}")


if __name__ == "__main__":
    main()