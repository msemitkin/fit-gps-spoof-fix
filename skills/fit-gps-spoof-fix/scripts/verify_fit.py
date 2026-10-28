#!/usr/bin/env python3
"""
verify_fit.py - independent check of a fixed FIT against the original.  No external dependencies.
Usage: python3 verify_fit.py original.fit fixed.fit [--max-kmh N]  (default: by sport, same as the fixer)
Exit code 0 = all checks passed.
"""
import argparse, struct, sys
from fix_fit_spoof import parse, get, crc16, load_records, resolve_max_kmh, SC, SESSION, LAP, REC

ap = argparse.ArgumentParser(); ap.add_argument('orig'); ap.add_argument('fixed'); ap.add_argument('--max-kmh', type=float)
a = ap.parse_args()
ok = True
def check(name, cond, extra=''):
    global ok; ok &= bool(cond); print(f'[{"ok" if cond else "FAIL"}] {name} {extra}')

ra, rb = open(a.orig, 'rb').read(), open(a.fixed, 'rb').read()
(_, A), (_, B) = parse(a.orig), parse(a.fixed)
a.max_kmh = resolve_max_kmh(a.max_kmh, A, quiet=True)
check('same size and message count', len(ra) == len(rb) and len(A) == len(B))
check('file CRC', crc16(rb[:-2]) == struct.unpack('<H', rb[-2:])[0])
if rb[0] >= 14:
    check('header CRC', crc16(rb[:12]) == struct.unpack('<H', rb[12:14])[0])
changed = {}
bad_fields = 0
for x, y in zip(A, B):
    if x.raw != y.raw:
        changed[x.gnum] = changed.get(x.gnum, 0) + 1
        if x.gnum == REC:
            off = 1
            for f, size, bt in x.defn['fields']:
                if x.raw[off:off + size] != y.raw[off:off + size] and f not in (0, 1, 5, 6, 73):
                    bad_fields += 1
                off += size
check('only record/lap/session messages changed', set(changed) <= {REC, LAP, SESSION}, str(changed))
check('in records only lat/lon/distance/speed changed', bad_fields == 0)
ro, rf = load_records(A), load_records(B)
check('timestamps, HR, cadence, altitude untouched', all((p['t'], p['hr'], p['cad'], p['alt']) == (q['t'], q['hr'], q['cad'], q['alt']) for p, q in zip(ro, rf)))
d = [r for r in rf if r['dist'] is not None]
steps = [(q['dist'] - p['dist']) / max(q['t'] - p['t'], 1) for p, q in zip(d, d[1:])]
check('distance is monotonic', all(s >= -0.011 for s in steps))
vmax = max(max(r['spd'] or 0 for r in rf), max(steps)) * 3.6
check(f'no speed above {a.max_kmh:.0f} km/h', vmax <= a.max_kmh, f'(max {vmax:.1f})')
lat = [r['lat'] for r in rf if r['lat'] is not None]; lon = [r['lon'] for r in rf if r['lon'] is not None]
print(f'     positions: lat {min(lat):.4f}..{max(lat):.4f}, lon {min(lon):.4f}..{max(lon):.4f}; without position: {sum(r["lat"] is None for r in rf)} (was {sum(r["lat"] is None for r in ro)})')
print(f'     distance: {ro[-1]["dist"] / 1000:.2f} km -> {rf[-1]["dist"] / 1000:.2f} km;  max speed: {max(r["spd"] or 0 for r in ro) * 3.6:.1f} -> {max(r["spd"] or 0 for r in rf) * 3.6:.1f} km/h')
for m in B:
    if not m.is_def and m.gnum in (SESSION, LAP):
        af, mf = (124, 125) if m.gnum == SESSION else (110, 111)
        print(f'     {"session" if m.gnum == SESSION else "lap    "}: {get(m, 9) / 1e5:.2f} km, avg {(get(m, af) or 0) * 3.6 / 1000:.1f}, max {(get(m, mf) or 0) * 3.6 / 1000:.1f} km/h, timer {get(m, 8) / 1000:.0f} s')
        if m.gnum == SESSION:
            check('session distance matches last record', abs(get(m, 9) / 100 - rf[-1]['dist']) < 1)
sys.exit(0 if ok else 1)
