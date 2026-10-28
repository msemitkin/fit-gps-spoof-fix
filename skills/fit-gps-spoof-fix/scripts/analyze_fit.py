#!/usr/bin/env python3
"""
analyze_fit.py - diagnose GPS spoofing in a FIT activity BEFORE fixing it.  No external dependencies.

Usage: python3 analyze_fit.py activity.fit [--max-kmh N] [--max-km 100] [--center LAT,LON]

Prints: devices (is there a speed sensor: bike wheel or foot pod?), session totals, spoofed positions, speed histogram
(is there a clean gap under the threshold?), fake-speed episodes with a standstill verdict, borderline
records just under the threshold, gaps in valid GPS, sensor-vs-GPS distance ratio, raw GPS-chip speed.
"""
import argparse, datetime as dt, os, statistics, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # works under python3 -I too
from fix_fit_spoof import (parse, get, crc16, load_records, center_of, hav_m, resolve_max_kmh, standing_jitter,
                           session_sport, FOOT_SPORTS, NO_STEP_KMH, SC, FIT_EPOCH,
                           FILE_ID, SESSION, LAP, EVENT, DEVICE_INFO, ACTIVITY, GPS_META)

ANT_TYPES = {11: 'bike power', 120: 'heart rate', 121: 'bike speed+cadence', 122: 'bike cadence', 123: 'bike SPEED sensor',
             124: 'foot pod (stride SPEED/distance)'}
BLE_TYPES = {1: 'heart rate', 2: 'bike power', 3: 'bike speed+cadence', 4: 'bike SPEED sensor', 5: 'bike cadence',
             6: 'foot pod (running SPEED/cadence)'}
SOURCE = {0: 'ANT', 1: 'ANT+', 2: 'BT', 3: 'BLE', 4: 'wifi', 5: 'local'}


def hms(s):
    s = int(s); return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('--max-kmh', type=float, help='default by sport: cycling 60, running 30, walking/hiking 15')
    ap.add_argument('--max-km', type=float, default=100.0); ap.add_argument('--center')
    a = ap.parse_args()

    raw = open(a.src, 'rb').read()
    header, msgs = parse(a.src)
    a.max_kmh = resolve_max_kmh(a.max_kmh, msgs); thr = a.max_kmh / 3.6
    print(f'file: {len(raw)} bytes, {len(msgs)} messages, CRC ok: {crc16(raw[:-2]) == struct.unpack("<H", raw[-2:])[0]}')

    # ---- identity, devices, session ---------------------------------------------------
    tz = 0
    for m in msgs:
        if not m.is_def and m.gnum == ACTIVITY and get(m, 5) and get(m, 253):
            tz = get(m, 5) - get(m, 253)
    def when(v):
        return dt.datetime.fromtimestamp(v + FIT_EPOCH, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    print(f'device local offset at save time: UTC{tz / 3600:+.0f}  (the watch takes its timezone from GPS, so a spoofed fix gives e.g. UTC-5 = Lima; '
          f'Strava derives local time from the first valid position, not from this)')
    seen, has_speed_sensor = set(), False
    for m in msgs:
        if m.is_def:
            continue
        if m.gnum == FILE_ID:
            print(f'file_id: manufacturer {get(m, 1)} (1 = Garmin), product {get(m, 2)}, created {when(get(m, 4))}')
        elif m.gnum == DEVICE_INFO:
            key = (get(m, 0), get(m, 1), get(m, 2), get(m, 4), get(m, 25))
            if key in seen or key[1] is None:
                continue
            seen.add(key); src = key[4]
            name = (ANT_TYPES if src in (0, 1) else BLE_TYPES if src in (2, 3) else {}).get(key[1], '')
            if 'SPEED' in name or 'speed' in name:
                has_speed_sensor = True
            if name:
                print(f'  sensor: {name} via {SOURCE.get(src, src)} (manufacturer {key[2]}, product {key[3]})')
    print('speed sensor (wheel or foot pod) paired:', 'YES' if has_speed_sensor else 'NO  <-- speed/distance are GPS-based; see SKILL.md "No speed sensor"')
    for m in msgs:
        if not m.is_def and m.gnum == SESSION:
            print(f'session: start {when(get(m, 2))}, elapsed {hms((get(m, 7) or 0) / 1000)}, timer {hms((get(m, 8) or 0) / 1000)}, '
                  f'distance {(get(m, 9) or 0) / 1e5:.2f} km, avg {(get(m, 124) or get(m, 14) or 0) * 3.6 / 1000:.1f} km/h, '
                  f'max {(get(m, 125) or get(m, 15) or 0) * 3.6 / 1000:.1f} km/h, ascent {get(m, 22)} m, sport {get(m, 5)}/{get(m, 6)}')
    print('laps:', sum(1 for m in msgs if not m.is_def and m.gnum == LAP))

    # ---- records ----------------------------------------------------------------------
    recs = load_records(msgs)
    t0 = recs[0]['t']
    clat, clon = center_of(recs, tuple(map(float, a.center.split(','))) if a.center else None)
    prev = None
    for r in recs:
        r['s'] = r['t'] - t0
        r['far'] = r['lat'] is not None and hav_m(r['lat'], r['lon'], clat, clon) > a.max_km * 1000
        r['dd'] = r['dt'] = 0.0
        if prev is not None and r['dist'] is not None:
            r['dd'] = r['dist'] - prev['dist']; r['dt'] = max(r['t'] - prev['t'], 1)
        if r['dist'] is not None:
            prev = r
        r['v'] = max(r['spd'] or 0.0, r['dd'] / r['dt'] if r['dt'] else 0.0)
    n = len(recs)
    print(f'\nrecords: {n}, without position: {sum(r["lat"] is None for r in recs)}, without speed: {sum(r["spd"] is None for r in recs)}')
    print(f'centre of activity: {clat:.4f}, {clon:.4f}')
    far = [r for r in recs if r['far']]
    print(f'SPOOFED positions (> {a.max_km:.0f} km from centre): {len(far)}')
    clusters = {}
    for r in far:
        k = (round(r['lat'], 1), round(r['lon'], 1)); clusters[k] = clusters.get(k, 0) + 1
    for k, c in sorted(clusters.items(), key=lambda x: -x[1])[:5]:
        print(f'   {c:5d} points near {k[0]}, {k[1]}' + ('   (Lima, Peru - the usual Kyiv spoofing target)' if abs(k[0] + 12) < 1 and abs(k[1] + 77) < 1 else ''))

    # ---- speed histogram --------------------------------------------------------------
    edges = [0.1, 10, 20, 30, 40, 45, 50, 55, 60, 70, 80, 100, 150, 200, 300, 1e9]
    print('\nspeed histogram, km/h  [max(record speed, distance step / time step)]:')
    lo = -1
    for e in edges:
        c = sum(lo < r['v'] * 3.6 <= e for r in recs)
        if c:
            print(f'   {max(lo, 0):>5.0f} - {e if e < 1e9 else float("inf"):<5.0f}: {c}')
        lo = e
    foot = session_sport(msgs) in FOOT_SPORTS and any((r['cad'] or 0) > 0 for r in recs)
    for r in recs:   # same rule as the fixer: over the threshold, or (on foot) a no-step jump over NO_STEP_KMH
        r['fake'] = r['v'] > thr or (foot and (r['cad'] or 0) == 0 and r['v'] > NO_STEP_KMH / 3.6)
    real = [r['v'] * 3.6 for r in recs if not r['fake']]; fake = [r for r in recs if r['fake']]
    over = [r for r in fake if r['v'] > thr]
    print(f'fastest kept record: {max(real):.1f} km/h;  slowest above the threshold: {min((r["v"] * 3.6 for r in over), default=0):.1f} km/h'
          f'   -> {"CLEAN GAP, threshold is safe" if not over or min(r["v"] for r in over) * 3.6 > 1.5 * max(real) else "gap is NARROW - check the episodes and borderline records below before trusting the threshold"}')
    if foot:
        ns = [r for r in fake if r['v'] <= thr]
        print(f'on foot: {len(ns)} more records are GPS jumps faster than {NO_STEP_KMH} km/h with no steps (cadence 0), +{sum(r["dd"] for r in ns):.0f} m; the fixer removes them too')

    # ---- fake-speed episodes ----------------------------------------------------------
    print(f'\nfake records: {len(fake)}, fake distance {sum(r["dd"] for r in fake) / 1000:.2f} km')
    eps, cur = [], []
    for r in fake:
        if cur and r['s'] - cur[-1]['s'] > 300:
            eps.append(cur); cur = []
        cur.append(r)
    if cur:
        eps.append(cur)
    for e in eps:
        cads = [r['cad'] or 0 for r in e]; hrs = [r['hr'] for r in e if r['hr']]; alts = [r['alt'] for r in e if r['alt'] is not None]
        med = statistics.median(alts) if alts else 0
        steady = sum(abs(x - med) < 3 for x in alts) / len(alts) if alts else 1   # ignores single altitude glitches
        still = max(cads) < 40 and steady >= 0.7
        print(f'   {hms(e[0]["s"])} - {hms(e[-1]["s"])} elapsed: {len(e)} records, +{sum(r["dd"] for r in e):.0f} m, max record speed {max(r["spd"] or 0 for r in e) * 3.6:.0f} km/h, '
              f'cadence max {max(cads)}, HR {min(hrs, default=0)}-{max(hrs, default=0)}, altitude within 3 m of median {steady:.0%}'
              f'  -> {"athlete was STANDING or barely moving (sensor asleep, GPS fallback)" if still else "athlete may have been MOVING - look closer"}')

    # ---- borderline records -----------------------------------------------------------
    border = [k for k, r in enumerate(recs) if 0.8 * thr < r['v'] <= thr]
    print(f'\nborderline records ({0.8 * a.max_kmh:.0f}-{a.max_kmh:.0f} km/h): {len(border)}  (real descents: smooth ramp, altitude drops, valid GPS)')
    for k in border[:15]:
        w = recs[max(k - 5, 0):k + 6]; alts = [x['alt'] for x in w if x['alt'] is not None]
        print(f'   {hms(recs[k]["s"])}: {recs[k]["v"] * 3.6:.1f} km/h, cad {recs[k]["cad"]}, HR {recs[k]["hr"]}, spoofed pos: {recs[k]["far"]}, '
              f'altitude change over +-5 rec {alts[-1] - alts[0] if alts else 0:+.1f} m, neighbours {[round((x["spd"] or 0) * 3.6) for x in w]}')

    # ---- hidden fake distance under the threshold ---------------------------------------
    cand = []
    for k, r in enumerate(recs):
        if r['dd'] > 15 and r['v'] <= thr and (r['cad'] or 0) == 0 and (r['hr'] or 999) < 100 and (r['far'] or r['lat'] is None):
            w = [x['alt'] for x in recs[max(k - 5, 0):k + 6] if x['alt'] is not None]
            if w and max(w) - min(w) < 1.0:
                cand.append(r)
    print(f'\npossible hidden fake steps under the threshold (no real GPS, cadence 0, HR<100, flat altitude, step >15 m): {len(cand)}')
    for r in cand[:10]:
        print(f'   {hms(r["s"])}: +{r["dd"]:.0f} m in {r["dt"]:.0f} s, speed {(r["spd"] or 0) * 3.6:.1f} km/h')
    jit = standing_jitter(recs, msgs, clat, clon, a.max_km)
    jit = [recs[k] for k in sorted(jit) if not recs[k]['fake']]
    if foot:
        print(f'standing-still GPS jitter (on foot, cadence 0, track going nowhere for +-60 s): {len(jit)} records, '
              f'+{sum(r["dd"] for r in jit) / 1000:.2f} km; the fixer removes it unless --keep-jitter')

    # ---- GPS gaps & sensor-vs-GPS ratio ---------------------------------------------------
    ok = [r for r in recs if r['lat'] is not None and not r['far']]
    g_sum = d_sum = 0.0
    print('\ngaps in REAL GPS in the ORIGINAL file (map will show a straight line there; preview_track.py lists the final ones after the fix):')
    for p, q in zip(ok, ok[1:]):
        gd = hav_m(p['lat'], p['lon'], q['lat'], q['lon']); dd = (q['dist'] or 0) - (p['dist'] or 0); gt = q['t'] - p['t']
        if gt <= 5:
            g_sum += gd; d_sum += dd
        elif gt > 30 and (gd > 150 or dd > 150):
            had = any(r['far'] for r in recs if p['t'] < r['t'] < q['t'])
            print(f'   {hms(p["s"])} - {hms(q["s"])}: straight {gd:.0f} m, recorded {dd:.0f} m, {"spoofed positions inside" if had else "no fix at all (jamming)"}')
    if ok:   # no real position at the start or the end: the map track starts late / ends early, with no line drawn
        d0 = (ok[0]['dist'] or 0) - (recs[0]['dist'] or 0); d1 = (recs[-1]['dist'] or 0) - (ok[-1]['dist'] or 0)
        if d0 > 150:
            print(f'   start - {hms(ok[0]["s"])}: no real position, recorded {d0:.0f} m  -> the map track will START LATE')
        if d1 > 150:
            print(f'   {hms(ok[-1]["s"])} - end: no real position, recorded {d1:.0f} m  -> the map track will END EARLY (no line drawn)')
    if d_sum:
        ratio = g_sum / d_sum
        print(f'GPS / recorded distance over clean stretches: {ratio:.4f}  '
              f'({"~1.000 -> recorded distance is GPS-based" if abs(ratio - 1) <= 0.004 else "differs from 1 -> recorded distance comes from the speed sensor" if has_speed_sensor else "differs from 1 -> the watch smooths GPS distance or adds wrist-accelerometer distance; still no sensor"})')

    # ---- raw GPS-chip speed (Garmin message 160) --------------------------------------------
    g = [get(m, 4) for m in msgs if not m.is_def and m.gnum == GPS_META]
    g = [x / 1000 for x in g if x is not None]
    if g:
        print(f'raw GPS-chip speed samples (msg 160): {len(g)}, above threshold: {sum(x > thr for x in g)}, max {max(g) * 3.6:.0f} km/h'
              + ('   (many spoofed samples + sane record speed = the watch was using the speed sensor)' if has_speed_sensor else ''))
    ev = [(get(m, 253) - t0, get(m, 1)) for m in msgs if not m.is_def and m.gnum == EVENT and get(m, 0) == 0 and get(m, 253)]
    print(f'timer events: {sum(1 for e in ev if e[1] == 0)} starts / {sum(1 for e in ev if e[1] == 4)} stops (auto-pause {"on" if len(ev) > 6 else "probably off"})')


if __name__ == '__main__':
    main()
