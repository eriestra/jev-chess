"""Maximum-likelihood Elo for a pool of players, with anchors and a draw prior.

Model: P(a beats b) = 1 / (1 + 10^((Rb - Ra) / 400)); a draw counts as half a win
and half a loss. Each pair that met gets `prior` virtual draws (as BayesElo does)
so a 100% score gives a finite rating. Anchored players keep their fixed rating.
Confidence intervals come from resampling the games of each pair (bootstrap).
"""

import math
import random
from collections import defaultdict

import numpy as np
from scipy.optimize import minimize


def fit(games: list[tuple[str, str, float]], anchors: dict[str, float], prior: float = 2.0) -> dict[str, float]:
    players = sorted({g[0] for g in games} | {g[1] for g in games})
    free = [p for p in players if p not in anchors]
    idx = {p: i for i, p in enumerate(free)}
    pairs = defaultdict(lambda: [0.0, 0.0])  # (a, b) -> [points for a, games]
    for a, b, s in games:
        key = (a, b) if a < b else (b, a)
        pts = s if a < b else 1 - s
        pairs[key][0] += pts
        pairs[key][1] += 1
    items = [(a, b, pts + prior / 2, n + prior) for (a, b), (pts, n) in pairs.items()]

    def rating(x, p):
        return anchors[p] if p in anchors else x[idx[p]]

    def nll(x):
        total = 0.0
        grad = np.zeros_like(x)
        for a, b, pts, n in items:
            d = (rating(x, a) - rating(x, b)) * math.log(10) / 400
            pa = 1 / (1 + math.exp(-d))
            pa = min(max(pa, 1e-12), 1 - 1e-12)
            total -= pts * math.log(pa) + (n - pts) * math.log(1 - pa)
            g = -(pts - n * pa) * math.log(10) / 400
            if a in idx:
                grad[idx[a]] += g
            if b in idx:
                grad[idx[b]] -= g
        return total, grad

    x0 = np.full(len(free), float(np.mean(list(anchors.values()))))
    res = minimize(nll, x0, jac=True, method="L-BFGS-B")
    out = {p: float(rating(res.x, p)) for p in players}
    return out


def bootstrap(games, anchors, prior: float = 2.0, n: int = 500, seed: int = 1) -> dict[str, tuple[float, float]]:
    rng = random.Random(seed)
    by_pair = defaultdict(list)
    for g in games:
        by_pair[tuple(sorted(g[:2]))].append(g)
    samples = defaultdict(list)
    for _ in range(n):
        resampled = []
        for gs in by_pair.values():
            resampled += [rng.choice(gs) for _ in gs]
        for p, r in fit(resampled, anchors, prior).items():
            samples[p].append(r)
    return {p: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))) for p, v in samples.items()}


def score_table(games) -> dict:
    t = defaultdict(lambda: {"games": 0, "points": 0.0, "w": 0, "d": 0, "l": 0})
    for a, b, s in games:
        row = t[(a, b)]
        row["games"] += 1
        row["points"] += s
        row["w" if s == 1 else "l" if s == 0 else "d"] += 1
    return {f"{a} vs {b}": v for (a, b), v in t.items()}
