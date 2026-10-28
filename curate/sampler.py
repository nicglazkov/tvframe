"""Slideshow sampler for TV Frame.

Picks the next photo by weighted random choice:
  score = (max(weight, FLOOR) / 100) ** GAMMA * boosts * wait_bonus
  - GAMMA > 1 leans toward the best photos.
  - FLOOR keeps weak photos in rotation instead of letting them vanish.
  - boosts: favorite, "on this day" (same month and day within +-3 days, any earlier year).
  - wait_bonus grows the longer a photo has gone unseen, so nothing good disappears.
  - cooldown: a photo shown in the last COOLDOWN slides cannot be picked again.
  - near-duplicates that are not the representative are skipped (show_duplicates=False).
"""
import random
from datetime import date

FLOOR = 8.0
GAMMA = 3.0
FAV_BOOST = 2.0
OTD_BOOST = 4.0
OTD_WINDOW_DAYS = 3
COOLDOWN_MAX = 90          # 90 slides = 30 min at 20 s
COOLDOWN_FRAC = 0.5        # but never more than half the pool
WAIT_FULL = 540            # slides unseen until the wait bonus reaches its max (3 h at 20 s)
WAIT_MAX = 3.0


def on_this_day(d, today):
    if not d:
        return False
    try:
        y, m, dd = (int(x) for x in d[:10].split("-"))
        t = date.fromisoformat(today)
        if y >= t.year:
            return False
        a = date(t.year, m, min(dd, 28) if m == 2 and dd == 29 else dd)
        return abs((a - t).days) <= OTD_WINDOW_DAYS
    except Exception:
        return False


class Sampler:
    def __init__(self, photos, today=None, seed=None, show_duplicates=False):
        self.today = today or date.today().isoformat()
        self.rng = random.Random(seed)
        self.items = []
        for p in photos:
            if not p.get("is_rep", True) and not show_duplicates and not p.get("favorite"):
                continue
            boost = 1.0
            if p.get("favorite"):
                boost *= FAV_BOOST
            otd = on_this_day(p.get("date"), self.today)
            if otd:
                boost *= OTD_BOOST
            base = (max(p["weight"], FLOOR) / 100.0) ** GAMMA
            self.items.append(dict(id=p["id"], base=base, boost=boost, otd=otd, fav=bool(p.get("favorite"))))
        n = len(self.items)
        self.cooldown = max(0, min(COOLDOWN_MAX, int(n * COOLDOWN_FRAC)))
        self.step = 0
        self.last = {it["id"]: -10**9 for it in self.items}
        # pretend everything was seen a while ago so the first hour is not all wait bonus
        for it in self.items:
            self.last[it["id"]] = -self.rng.randint(0, WAIT_FULL // 2)

    def _score(self, it):
        gap = self.step - self.last[it["id"]]
        if gap <= self.cooldown:
            return 0.0
        wait = 1.0 + (WAIT_MAX - 1.0) * min(1.0, gap / WAIT_FULL)
        return it["base"] * it["boost"] * wait

    def next(self):
        scores = [self._score(it) for it in self.items]
        tot = sum(scores)
        if tot <= 0:            # tiny pool: fall back to the longest unseen
            it = min(self.items, key=lambda i: self.last[i["id"]])
        else:
            r = self.rng.random() * tot
            acc = 0.0
            it = self.items[-1]
            for s, cand in zip(scores, self.items):
                acc += s
                if acc >= r:
                    it = cand
                    break
        self.last[it["id"]] = self.step
        self.step += 1
        return it["id"]


def simulate(photos, hours=15, slide_s=20, today=None, seed=42):
    s = Sampler(photos, today=today, seed=seed)
    n_slides = int(hours * 3600 / slide_s)
    seq = [s.next() for _ in range(n_slides)]
    by_id = {p["id"]: p for p in photos}
    eligible = [it["id"] for it in s.items]

    counts = {i: 0 for i in eligible}
    positions = {i: [] for i in eligible}
    for k, i in enumerate(seq):
        counts[i] += 1
        positions[i].append(k)

    # tiers by weight among the eligible photos (top / middle / bottom third)
    ranked = sorted(eligible, key=lambda i: -by_id[i]["weight"])
    t = len(ranked) // 3
    tiers = {"top": ranked[:t], "middle": ranked[t:len(ranked) - t], "bottom": ranked[len(ranked) - t:]}

    def gaps(i):
        p = positions[i]
        return [b - a for a, b in zip(p, p[1:])]

    min_repeat_gap = min((min(gaps(i)) for i in eligible if len(positions[i]) > 1), default=None)
    tier_stats = {}
    for name, ids in tiers.items():
        c = [counts[i] for i in ids]
        g = [x for i in ids for x in gaps(i)]
        tier_stats[name] = dict(
            photos=len(ids),
            weight_range=[round(min(by_id[i]["weight"] for i in ids), 1), round(max(by_id[i]["weight"] for i in ids), 1)],
            shows_total=sum(c), share_of_slides=round(sum(c) / n_slides, 3),
            shows_per_photo_mean=round(sum(c) / len(c), 1), shows_per_photo_min=min(c), shows_per_photo_max=max(c),
            minutes_between_repeats_mean=round(sum(g) / len(g) * slide_s / 60, 1) if g else None,
            appears_every_minutes=round(hours * 60 / (sum(c) / len(c)), 1) if sum(c) else None)

    longest_unseen = max(eligible, key=lambda i: max([positions[i][0]] + gaps(i) + [n_slides - positions[i][-1]] if positions[i] else [n_slides]))
    lu_p = positions[longest_unseen]
    lu_gap = max([lu_p[0]] + gaps(longest_unseen) + [n_slides - lu_p[-1]]) if lu_p else n_slides

    otd_ids = [it["id"] for it in s.items if it["otd"]]
    fav_ids = [it["id"] for it in s.items if it["fav"]]
    per_photo = sorted(
        ({"id": i, "weight": by_id[i]["weight"], "shows": counts[i], "favorite": by_id[i].get("favorite", False),
          "on_this_day": i in otd_ids} for i in eligible), key=lambda r: -r["weight"])

    return dict(
        settings=dict(hours=hours, slide_seconds=slide_s, slides=n_slides, today=s.today, seed=seed,
                      floor=FLOOR, gamma=GAMMA, favorite_boost=FAV_BOOST, on_this_day_boost=OTD_BOOST,
                      on_this_day_window_days=OTD_WINDOW_DAYS, cooldown_slides=s.cooldown,
                      cooldown_minutes=round(s.cooldown * slide_s / 60, 1),
                      wait_bonus_max=WAIT_MAX, wait_bonus_full_after_minutes=WAIT_FULL * slide_s / 60),
        summary=dict(
            slides=n_slides, eligible_photos=len(eligible),
            hidden_near_duplicates=sum(1 for p in photos if not p.get("is_rep", True) and not p.get("favorite")),
            distinct_shown=sum(1 for i in eligible if counts[i] > 0),
            never_shown=[i for i in eligible if counts[i] == 0],
            min_slides_between_repeats=min_repeat_gap,
            min_minutes_between_repeats=round(min_repeat_gap * slide_s / 60, 1) if min_repeat_gap else None,
            repeats_inside_cooldown=sum(1 for i in eligible for g in gaps(i) if g <= s.cooldown),
            longest_unseen=dict(id=longest_unseen, weight=by_id[longest_unseen]["weight"],
                                minutes=round(lu_gap * slide_s / 60, 1)),
            tiers=tier_stats,
            on_this_day=dict(ids=otd_ids, shows={i: counts[i] for i in otd_ids}),
            favorites=dict(ids=fav_ids, shows={i: counts[i] for i in fav_ids}),
        ),
        per_photo=per_photo,
        first_60=seq[:60],
    )


def synthetic_library(n=800, seed=7):
    """A made-up library shaped like a real phone camera roll: mostly ordinary shots,
    a long tail of great ones, some weak ones. Used to show how the sampler behaves at
    real scale, where the 60-photo sample pool is too small for weighting to matter much."""
    rng = random.Random(seed)
    photos = []
    for k in range(n):
        w = max(2.0, min(95.0, rng.betavariate(2.2, 2.6) * 100))
        photos.append(dict(id=f"s{k}", weight=round(w, 1), is_rep=True, favorite=rng.random() < 0.02,
                           date=f"{rng.randint(2014, 2025)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"))
    return photos
