"""Exact seating-plan search after a table closure (stage-4 seating changes, G3/G4).

Among feasible assignments minimise, lexicographically: the number of bookings whose table set
changes, the total unused seats, then the vector of option ranks in reference order."""

from dataclasses import dataclass


@dataclass
class Candidate:
    tables: tuple  # table ids of the option
    rank: int  # singles in fixture order, then declared pairs
    unused: int  # capacity under the booking's own terms minus its party size
    changed: bool  # differs from the booking's current table set


@dataclass
class Booking:
    key: object  # caller's identifier
    start: object
    end: object
    candidates: list


def best_plan(bookings):
    """Bookings in reference order. Returns the chosen Candidate per booking, or None."""
    n = len(bookings)
    if n == 0:
        return []
    overlap = [[i != j and bookings[i].start < bookings[j].end and
                bookings[j].start < bookings[i].end for j in range(n)] for i in range(n)]
    ordered = [sorted(b.candidates, key=lambda c: (c.changed, c.unused, c.rank)) for b in bookings]
    if any(not c for c in ordered):
        return None
    # Lower bounds for the bookings still to assign (suffix sums).
    min_moves = [0] * (n + 1)
    min_unused = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        min_moves[i] = min_moves[i + 1] + min(c.changed for c in ordered[i])
        min_unused[i] = min_unused[i + 1] + min(c.unused for c in ordered[i])

    best = {"key": None, "choice": None}
    choice = [None] * n

    def search(i, moves, unused, ranks):
        if best["key"] is not None:
            bound = (moves + min_moves[i], unused + min_unused[i])
            if bound > best["key"][:2]:
                return
            if bound == best["key"][:2] and tuple(ranks) > best["key"][2][:i]:
                return
        if i == n:
            key = (moves, unused, tuple(ranks))
            if best["key"] is None or key < best["key"]:
                best["key"], best["choice"] = key, list(choice)
            return
        for c in ordered[i]:
            if any(overlap[i][j] and not set(c.tables).isdisjoint(choice[j].tables)
                   for j in range(i)):
                continue
            choice[i] = c
            search(i + 1, moves + c.changed, unused + c.unused, ranks + [c.rank])
        choice[i] = None

    search(0, 0, 0, [])
    return best["choice"]
