"""WO 4.2: the seating-plan search is exact and fast at the planning limits."""

import itertools
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from tablekeeper.planner import Booking, Candidate, best_plan  # noqa: E402


def brute_force(bookings):
    best = None
    for combo in itertools.product(*[b.candidates for b in bookings]):
        ok = all(not (bookings[i].start < bookings[j].end and bookings[j].start < bookings[i].end)
                 or set(combo[i].tables).isdisjoint(combo[j].tables)
                 for i in range(len(bookings)) for j in range(i + 1, len(bookings)))
        if not ok:
            continue
        key = (sum(c.changed for c in combo), sum(c.unused for c in combo),
               tuple(c.rank for c in combo))
        if best is None or key < best[0]:
            best = (key, list(combo))
    return None if best is None else best[1]


def random_instance(rng, n_bookings):
    tables = [f"t_{i}" for i in range(6)]
    caps = {t: rng.choice([2, 2, 4, 4, 6]) for t in tables}
    pairs = rng.sample([p for p in itertools.combinations(tables, 2)], 4)
    options = [(t,) for t in tables] + pairs
    bookings = []
    for k in range(n_bookings):
        start = rng.choice([0, 30, 60, 90, 120])
        party = rng.choice([1, 2, 3, 4, 5, 6])
        current = set(rng.choice(options))
        candidates = [Candidate(o, r, sum(caps[t] for t in o) - party, set(o) != current)
                      for r, o in enumerate(options)
                      if sum(caps[t] for t in o) >= party and rng.random() > 0.15]
        bookings.append(Booking(k, start, start + 90, candidates))
    return bookings


class PlannerTest(unittest.TestCase):
    def test_matches_brute_force(self):
        rng = random.Random(4)
        for _ in range(300):
            bookings = random_instance(rng, rng.randint(0, 4))
            got, want = best_plan(bookings), brute_force(bookings)
            self.assertEqual(got, want)

    def test_fast_at_the_limits(self):
        rng = random.Random(7)
        worst = 0.0
        for _ in range(30):
            bookings = random_instance(rng, 6)
            started = time.monotonic()
            best_plan(bookings)
            worst = max(worst, time.monotonic() - started)
        self.assertLess(worst, 1.0)


if __name__ == "__main__":
    unittest.main()
