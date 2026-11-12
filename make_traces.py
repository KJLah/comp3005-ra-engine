"""Write the five Module 1 trace files into traces/ (DESIGN_M1.md section 8).

    python3 make_traces.py

One page id per line. The output is the same on every machine and every
Python version: the "random" traces use a small fixed LCG, not the random
module, whose algorithms are allowed to change between versions.
"""
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "traces")


class LCG:
    """Numerical Recipes constants; seed fixed so the files never change."""
    def __init__(self, seed: int):
        self.x = seed

    def below(self, n: int) -> int:
        self.x = (1664525 * self.x + 1013904223) % 2**32
        return (self.x >> 8) % n


def t1_worked():
    return [1, 2, 3, 1, 4, 2]                       # M1 section 5.3


def t2_hotcold():
    """1000 fetches over 200 pages: 80% go to 20 hot pages (0-19)."""
    g = LCG(2)
    return [g.below(20) if g.below(100) < 80 else 20 + g.below(180)
            for _ in range(1000)]


def t3_scan80x10():
    return [p for _ in range(10) for p in range(80)]   # M1 section 8


def t4_uniform():
    """1000 fetches, every one of 200 pages equally likely."""
    g = LCG(4)
    return [g.below(200) for _ in range(1000)]


def t5_hot_plus_scan():
    """A 10-page hot set, interleaved with a one-time scan of 500 other pages."""
    out = []
    for i in range(500):
        out.append(i % 10)                           # hot pages 0-9
        out.append(100 + i)                          # scan pages 100-599, each once
    return out


TRACES = {"t1_worked": t1_worked, "t2_hotcold": t2_hotcold,
          "t3_scan80x10": t3_scan80x10, "t4_uniform": t4_uniform,
          "t5_hot_plus_scan": t5_hot_plus_scan}


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, make in TRACES.items():
        ids = make()
        with open(os.path.join(OUT, name + ".txt"), "w", encoding="utf-8") as f:
            f.write("".join(f"{p}\n" for p in ids))
        print(f"traces/{name}.txt: {len(ids)} fetches, {len(set(ids))} distinct pages")


if __name__ == "__main__":
    main()