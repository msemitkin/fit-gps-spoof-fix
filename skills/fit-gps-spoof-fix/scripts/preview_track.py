#!/usr/bin/env python3
"""
preview_track.py - PNG of how the fixed track will look on a map (needs matplotlib).
Usage: python3 preview_track.py original.fit fixed.fit out.png [--title "..."]
Solid = real GPS; blue dashed = bridge where spoofed positions were removed; grey dashed = no fix at all.
ALWAYS look at the PNG afterwards: leftover spikes or a far-away stub mean something was missed.
"""
import argparse, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # works under python3 -I too
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from fix_fit_spoof import parse, load_records, hav_m

ap = argparse.ArgumentParser(); ap.add_argument('orig'); ap.add_argument('fixed'); ap.add_argument('out'); ap.add_argument('--title')
a = ap.parse_args()
O, F = load_records(parse(a.orig)[1]), load_records(parse(a.fixed)[1])
removed = [o['lat'] is not None and f['lat'] is None for o, f in zip(O, F)]
idx = [k for k, r in enumerate(F) if r['lat'] is not None]
fig, ax = plt.subplots(figsize=(7, 7.5), dpi=130)
piece = [idx[0]]
for p, q in zip(idx, idx[1:]):
    if q - p > 1:
        ax.plot([F[k]['lon'] for k in piece], [F[k]['lat'] for k in piece], color='#fc4c02', lw=1.6); piece = []
        gap = hav_m(F[p]['lat'], F[p]['lon'], F[q]['lat'], F[q]['lon']); ridden = F[q]['dist'] - F[p]['dist']
        spoof = any(removed[p:q + 1])
        if (spoof and (gap > 50 or ridden > 100)) or gap > 150:   # skip tiny gaps left by removed jitter
            c = '#1f77b4' if spoof else '#555555'
            ax.plot([F[p]['lon'], F[q]['lon']], [F[p]['lat'], F[q]['lat']], color=c, lw=2.2, ls='--')
            ax.scatter([F[p]['lon'], F[q]['lon']], [F[p]['lat'], F[q]['lat']], color=c, s=28, zorder=5)
            t0 = F[0]['t']
            print(f'bridge ({"spoofed or jitter positions removed" if spoof else "no GPS fix"}): elapsed {F[p]["t"] - t0}..{F[q]["t"] - t0} s, straight {gap:.0f} m, actually covered {ridden:.0f} m')
    piece.append(q)
ax.plot([F[k]['lon'] for k in piece], [F[k]['lat'] for k in piece], color='#fc4c02', lw=1.6, label='GPS track')
ax.plot([], [], color='#1f77b4', lw=2.2, ls='--', label='gap: spoofed or jitter points removed')
ax.plot([], [], color='#555555', lw=2.2, ls='--', label='no GPS fix at all (jamming)')
ax.scatter(F[idx[0]]['lon'], F[idx[0]]['lat'], color='green', s=50, zorder=6, label='first real GPS point')
ax.scatter(F[idx[-1]]['lon'], F[idx[-1]]['lat'], color='black', s=50, zorder=6, label='last real GPS point')
ax.set_aspect(1 / math.cos(math.radians(F[idx[0]]['lat']))); ax.grid(alpha=.3); ax.legend(loc='best', fontsize=8)
ax.set_title(a.title or f'Track after the fix - {F[-1]["dist"] / 1000:.2f} km', fontsize=11)
plt.tight_layout(); plt.savefig(a.out)
