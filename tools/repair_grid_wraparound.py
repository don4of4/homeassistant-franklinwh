#!/usr/bin/env python3
"""Repair int16-wraparound corruption in recorded grid power history.

Grid power was read from a signed 16-bit register, so any true value above
32.767 kW wrapped to a large negative number and was recorded as export.
This rebuilds states and statistics from the energy balance.

Run with --apply to write; default is a dry run. Home Assistant must be
STOPPED before applying.
"""
import argparse, bisect, sqlite3, sys, datetime

DB = "/config/home-assistant_v2.db"
GRID = "sensor.franklinwh_grid_use"
HOME = "sensor.franklinwh_home_power"
BATT = "sensor.franklinwh_battery_use"
SOLAR = "sensor.franklinwh_solar_power"
SPAN_KW = 65.536
SUSPECT_KW = 20.0
BUCKET = 300      # short-term statistics period
HOUR = 3600


def load_series(c, entity):
    sm = c.execute("select metadata_id from states_meta where entity_id=?", (entity,)).fetchone()
    if not sm:
        return [], []
    rows = c.execute(
        "select last_updated_ts, cast(state as real) from states "
        "where metadata_id=? and state not in ('unknown','unavailable','') order by 1",
        (sm[0],)).fetchall()
    return [t for t, _ in rows], [v for _, v in rows]


def ff(ts, ta, va):
    i = bisect.bisect_right(ta, ts) - 1
    return va[i] if i >= 0 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = sqlite3.connect(DB)
    gm = c.execute("select metadata_id from states_meta where entity_id=?", (GRID,)).fetchone()[0]
    stat_id = c.execute("select id from statistics_meta where statistic_id=?", (GRID,)).fetchone()[0]
    ids = {}
    for e in (HOME, BATT, SOLAR, GRID):
        r = c.execute("select id from statistics_meta where statistic_id=?", (e,)).fetchone()
        ids[e] = r[0] if r else None

    ht, hv = load_series(c, HOME)
    bt, bv = load_series(c, BATT)
    st_, sv = load_series(c, SOLAR)

    # ---------- 1. states ----------
    rows = c.execute(
        "select state_id, last_updated_ts, cast(state as real) from states "
        "where metadata_id=? and state not in ('unknown','unavailable','') order by last_updated_ts",
        (gm,)).fetchall()
    fixes = []
    for sid, ts, v in rows:
        if abs(v) < SUSPECT_KW:
            continue
        h = ff(ts, ht, hv); b = ff(ts, bt, bv); s = ff(ts, st_, sv) or 0.0
        if h is None or b is None:
            continue
        exp = h - b - s
        best = min((v, v + SPAN_KW, v - SPAN_KW), key=lambda x: abs(x - exp))
        if abs(best - v) > 1e-9:
            fixes.append((sid, ts, v, round(best, 3)))
    print(f"states to correct: {len(fixes)}")
    if fixes:
        print(f"  first {datetime.datetime.fromtimestamp(fixes[0][1])}  {fixes[0][2]:.3f} -> {fixes[0][3]:.3f}")
        print(f"  last  {datetime.datetime.fromtimestamp(fixes[-1][1])}  {fixes[-1][2]:.3f} -> {fixes[-1][3]:.3f}")

    if args.apply and fixes:
        c.executemany("update states set state=? where state_id=?",
                      [(str(n), sid) for sid, _, _, n in fixes])
        print("  states updated")

    # corrected in-memory series for recomputation
    corr = dict((sid, n) for sid, _, _, n in fixes)
    gts, gvs = [], []
    for sid, ts, v in rows:
        gts.append(ts); gvs.append(corr.get(sid, v))

    # ---------- 2. short-term statistics ----------
    def bucket_stats(start):
        """Time-weighted mean plus min/max, including the state carried in."""
        i0 = bisect.bisect_left(gts, start)
        i1 = bisect.bisect_left(gts, start + BUCKET)
        inside = [(gts[i], gvs[i]) for i in range(i0, i1)]
        j = bisect.bisect_right(gts, start) - 1
        carry = gvs[j] if j >= 0 else None
        if not inside and carry is None:
            return None
        vals = ([carry] if carry is not None else []) + [v for _, v in inside]
        prev_t, prev_v = start, carry if carry is not None else inside[0][1]
        acc = 0.0
        for t, v in inside:
            acc += prev_v * (t - prev_t); prev_t, prev_v = t, v
        acc += prev_v * (start + BUCKET - prev_t)
        return acc / BUCKET, min(vals), max(vals)

    touched = sorted({(ts // BUCKET) * BUCKET for _, ts, _, _ in fixes} |
                     {((ts // BUCKET) + 1) * BUCKET for _, ts, _, _ in fixes})
    st_updates = []
    for start in touched:
        row = c.execute("select mean,min,max from statistics_short_term where metadata_id=? and start_ts=?",
                        (stat_id, start)).fetchone()
        if not row:
            continue
        new = bucket_stats(start)
        if new is None:
            continue
        if any(abs(a - b) > 1e-6 for a, b in zip(row, new)):
            st_updates.append((new, start, row))
    print(f"short-term buckets to recompute: {len(st_updates)}")
    if args.apply and st_updates:
        c.executemany("update statistics_short_term set mean=?,min=?,max=? where metadata_id=? and start_ts=?",
                      [(n[0], n[1], n[2], stat_id, s) for n, s, _ in st_updates])
        print("  short-term updated")

    # ---------- 3. hourly statistics ----------
    # 3a. hours that still have short-term rows: aggregate from them
    hours = sorted({(s // HOUR) * HOUR for _, s, _ in st_updates})
    hourly_from_st = 0
    for h0 in hours:
        sub = c.execute("select mean,min,max from statistics_short_term where metadata_id=? and start_ts>=? and start_ts<?",
                        (stat_id, h0, h0 + HOUR)).fetchall()
        sub = [r for r in sub if r[0] is not None]
        if not sub:
            continue
        mean = sum(r[0] for r in sub) / len(sub)
        mn = min(r[1] for r in sub); mx = max(r[2] for r in sub)
        if args.apply:
            c.execute("update statistics set mean=?,min=?,max=? where metadata_id=? and start_ts=?",
                      (mean, mn, mx, stat_id, h0))
        hourly_from_st += 1
    print(f"hourly rows recomputed from short-term: {hourly_from_st}")

    # 3b. older hours with no states/short-term: reconstruct from the hourly balance
    done = set(hours)
    bad = c.execute(
        "select start_ts,mean,min,max from statistics where metadata_id=? and (min<-20 or max<-20) order by start_ts",
        (stat_id,)).fetchall()
    rebuilt = []
    for ts, mean, mn, mx in bad:
        if ts in done:
            continue
        def hm(mid):
            r = c.execute("select mean from statistics where metadata_id=? and start_ts=?", (mid, ts)).fetchone()
            return r[0] if r and r[0] is not None else None
        h, b = hm(ids[HOME]), hm(ids[BATT])
        s = hm(ids[SOLAR]) or 0.0
        if h is None or b is None:
            continue
        # Mean is linear, so the energy balance recovers it exactly.
        new_mean = h - b - s

        # min/max are NOT recoverable for these hours. Once some samples wrap
        # and others do not, the stored min is the wrapped form of the hour's
        # *largest* true value and the stored max is whichever unwrapped sample
        # happened to be highest -- the real extremes are gone. So publish
        # honest bounds that are at least self-consistent with the mean rather
        # than fabricated precision.
        def hstat(mid_, col):
            r = c.execute(f"select {col} from statistics where metadata_id=? and start_ts=?",
                          (mid_, ts)).fetchone()
            return r[0] if r and r[0] is not None else None

        cmn = mn + SPAN_KW if mn < -SUSPECT_KW else mn
        cmx = mx + SPAN_KW if mx < -SUSPECT_KW else mx
        # Lower bound: min(grid) >= min(home) - max(batt) - max(solar)
        bound = (hstat(ids[HOME], "min") or new_mean) \
            - (hstat(ids[BATT], "max") or 0.0) - (hstat(ids[SOLAR], "max") or 0.0)
        new_min = min(bound, new_mean)
        new_max = max(cmn, cmx, new_mean)
        rebuilt.append((ts, mean, new_mean, new_min, new_max))
        if args.apply:
            c.execute("update statistics set mean=?,min=?,max=? where metadata_id=? and start_ts=?",
                      (new_mean, new_min, new_max, stat_id, ts))
    print(f"hourly rows rebuilt from energy balance: {len(rebuilt)}")
    for ts, old, new, mn, mx in rebuilt:
        print(f"   {datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')}  mean {old:8.3f} -> {new:7.3f}   min {mn:7.3f} max {mx:7.3f}")

    if args.apply:
        c.commit(); print("\nCOMMITTED")
    else:
        print("\nDRY RUN - nothing written")
    c.close()


if __name__ == "__main__":
    main()
