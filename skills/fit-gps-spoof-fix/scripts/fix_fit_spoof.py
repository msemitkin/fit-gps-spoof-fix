#!/usr/bin/env python3
"""
fix_fit_spoof.py - clean GPS-spoofing artefacts out of a Garmin FIT activity.

Patches the file in place, byte for byte (every other message is left untouched):
  * records whose speed (or distance step / time step) exceeds --max-kmh are treated as
    spoofed-GPS fallback: speed -> 0, their distance increment is removed and the
    cumulative distance of all later records is shifted down accordingly;
  * positions further than --max-km from the activity's median position (or --center) are
    invalidated (lat/lon set to the FIT "invalid" value, the rest of the record is kept);
  * session / lap totals are rewritten: total_distance, avg & max speed, bounding box;
  * header + file CRC are recomputed.

No external dependencies. Also used as a tiny FIT library by the sibling scripts.
Usage:  python3 fix_fit_spoof.py in.fit out.fit [--max-kmh N] [--max-km 100] [--center LAT,LON]
Limits: single (non-chained) FIT file; array fields are read by first element only.
"""
import argparse, math, struct, statistics

BASE = {  # base type -> (struct fmt, size, invalid value)
    0x00: ('B', 1, 0xFF), 0x01: ('b', 1, 0x7F), 0x02: ('B', 1, 0xFF), 0x83: ('h', 2, 0x7FFF),
    0x84: ('H', 2, 0xFFFF), 0x85: ('i', 4, 0x7FFFFFFF), 0x86: ('I', 4, 0xFFFFFFFF),
    0x07: ('s', 1, None), 0x88: ('f', 4, None), 0x89: ('d', 8, None), 0x0A: ('B', 1, 0),
    0x8B: ('H', 2, 0), 0x8C: ('I', 4, 0), 0x0D: ('B', 1, 0xFF),
    0x8E: ('q', 8, 0x7FFFFFFFFFFFFFFF), 0x8F: ('Q', 8, 0xFFFFFFFFFFFFFFFF), 0x90: ('Q', 8, 0),
}
SC = 180 / 2 ** 31          # semicircles -> degrees
FIT_EPOCH = 631065600       # 1989-12-31T00:00:00Z as unix time
FILE_ID, SESSION, LAP, REC, EVENT, DEVICE_INFO, ACTIVITY, GPS_META = 0, 18, 19, 20, 21, 23, 34, 160


def crc16(data, crc=0):
    t = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
         0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
    for b in data:
        tmp = t[crc & 0xF]; crc = (crc >> 4) & 0x0FFF; crc ^= tmp ^ t[b & 0xF]
        tmp = t[crc & 0xF]; crc = (crc >> 4) & 0x0FFF; crc ^= tmp ^ t[(b >> 4) & 0xF]
    return crc


class Msg:
    __slots__ = ('is_def', 'gnum', 'raw', 'defn')


def parse(path):
    """-> (header bytearray, [Msg]). Msg.raw keeps the exact bytes so fields can be patched in place."""
    data = open(path, 'rb').read()
    hsize = data[0]
    dsize, sig = struct.unpack('<I4s', data[4:12])
    if sig != b'.FIT':
        raise ValueError('not a FIT file')
    pos, end, defs, msgs = hsize, hsize + dsize, {}, []
    while pos < end:
        start, h = pos, data[pos]; pos += 1
        if h & 0x80:                       # compressed-timestamp data message
            local, is_def = (h >> 5) & 0x3, False
        else:
            local, is_def = h & 0x0F, bool(h & 0x40)
        m = Msg(); m.is_def = is_def
        if is_def:
            en = '>' if data[pos + 1] else '<'
            gnum = struct.unpack(en + 'H', data[pos + 2:pos + 4])[0]
            nf = data[pos + 4]; pos += 5
            fields = [(data[pos + 3 * k], data[pos + 3 * k + 1], data[pos + 3 * k + 2]) for k in range(nf)]
            pos += 3 * nf
            size = sum(f[1] for f in fields)
            if h & 0x20:                   # developer fields: only their total size matters here
                nd = data[pos]; pos += 1
                size += sum(data[pos + 3 * k + 1] for k in range(nd)); pos += 3 * nd
            defs[local] = m.defn = {'gnum': gnum, 'en': en, 'fields': fields, 'size': size}
        else:
            m.defn = defs[local]; pos += m.defn['size']
        m.gnum = m.defn['gnum']; m.raw = bytearray(data[start:pos]); msgs.append(m)
    return bytearray(data[:hsize]), msgs


def _loc(m, fnum):
    off = 1
    for f, size, bt in m.defn['fields']:
        if f == fnum:
            return off, size, bt
        off += size
    return None


def get(m, fnum):
    """Raw (unscaled) value of a field, or None if absent / invalid."""
    loc = _loc(m, fnum)
    if not loc:
        return None
    off, size, bt = loc
    fmt, bsize, inv = BASE.get(bt, ('B', 1, 0xFF))
    if fmt == 's':
        return bytes(m.raw[off:off + size]).split(b'\0')[0].decode('utf8', 'replace')
    v = struct.unpack(m.defn['en'] + fmt, bytes(m.raw[off:off + bsize]))[0]
    return None if v == inv else v


def put(m, fnum, value):
    """Overwrite a field in place (None -> FIT invalid value). Returns False if the field is not in this message."""
    loc = _loc(m, fnum)
    if not loc:
        return False
    off, size, bt = loc
    fmt, bsize, inv = BASE[bt]
    m.raw[off:off + bsize] = struct.pack(m.defn['en'] + fmt, inv if value is None else value)
    return True


def write(header, msgs, path):
    body = b''.join(bytes(m.raw) for m in msgs)
    header[4:8] = struct.pack('<I', len(body))
    if len(header) >= 14:
        header[12:14] = struct.pack('<H', crc16(bytes(header[:12])))
    out = bytes(header) + body
    open(path, 'wb').write(out + struct.pack('<H', crc16(out)))


def hav_m(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def load_records(msgs):
    """List of dicts, one per record message: m, idx, t, lat, lon (deg), dist (m), spd (m/s), alt (m), hr, cad."""
    out, last_t = [], None
    for idx, m in enumerate(msgs):
        if m.is_def:
            continue
        t = get(m, 253)
        if t is None and (m.raw[0] & 0x80) and last_t is not None:   # compressed timestamp header
            off = m.raw[0] & 0x1F
            t = (last_t & ~0x1F) + off + (0x20 if off < (last_t & 0x1F) else 0)
        if t is not None:
            last_t = t
        if m.gnum != REC:
            continue
        la, lo, d = get(m, 0), get(m, 1), get(m, 5)
        s = get(m, 73); s = get(m, 6) if s is None else s
        a = get(m, 78); a = get(m, 2) if a is None else a
        out.append(dict(m=m, idx=idx, t=t,
                        lat=None if la is None or lo is None else la * SC,
                        lon=None if la is None or lo is None else lo * SC, lat_raw=la, lon_raw=lo,
                        dist=None if d is None else d / 100, spd=None if s is None else s / 1000,
                        alt=None if a is None else a / 5 - 500, hr=get(m, 3), cad=get(m, 4)))
    return out


def center_of(recs, center=None):
    if center:
        return center
    pts = [(r['lat'], r['lon']) for r in recs if r['lat'] is not None]
    return statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts)


# FIT sport enum -> (name, default speed threshold in km/h). Anything else falls back to 60.
SPORTS = {1: ('running', 30), 2: ('cycling', 60), 11: ('walking', 30), 12: ('cross-country skiing', 40),
          16: ('mountaineering', 30), 17: ('hiking', 30), 21: ('e-biking', 60)}
NO_STEP_KMH = 10   # foot sports: faster than this with cadence 0 (no steps) cannot be real movement


def default_max_kmh(msgs):
    """Default spoofing threshold for the activity's sport: (km/h, sport name)."""
    for m in msgs:
        if not m.is_def and m.gnum == SESSION:
            sp = get(m, 5)
            name, kmh = SPORTS.get(sp, (f'sport {sp}', 60))
            return kmh, name
    return 60, 'unknown sport'


def resolve_max_kmh(arg, msgs, quiet=False):
    """--max-kmh if given, otherwise the sport default; prints which one is used."""
    if arg is not None:
        return arg
    kmh, name = default_max_kmh(msgs)
    if not quiet:
        print(f'threshold: {kmh} km/h (default for {name}; override with --max-kmh)')
    return kmh


FOOT_SPORTS = {1, 11, 16, 17}          # running, walking, mountaineering, hiking: cadence 0 = not stepping


def session_sport(msgs):
    for m in msgs:
        if not m.is_def and m.gnum == SESSION:
            return get(m, 5)
    return None


def standing_jitter(recs, msgs, clat, clon, max_km, window_s=60, max_ratio=0.3):
    """Indexes (into recs) of records that are GPS jitter while standing still, for foot sports only:
    cadence 0 (no steps) and the valid track within +-window_s goes nowhere
    (net displacement < max_ratio of path length). On a bike cadence 0 just means coasting, so never there."""
    if session_sport(msgs) not in FOOT_SPORTS or not any((r['cad'] or 0) > 0 for r in recs):
        return set()
    pos = [(k, r) for k, r in enumerate(recs)
           if r['lat'] is not None and hav_m(r['lat'], r['lon'], clat, clon) <= max_km * 1000]
    out, lo = set(), 0
    for j, (k, r) in enumerate(pos):
        if (r['cad'] or 0) != 0:
            continue
        while pos[lo][1]['t'] < r['t'] - window_s:
            lo += 1
        hi = j
        while hi + 1 < len(pos) and pos[hi + 1][1]['t'] <= r['t'] + window_s:
            hi += 1
        w = [p for _, p in pos[lo:hi + 1]]
        if len(w) < 4:
            continue
        path = sum(hav_m(a['lat'], a['lon'], b['lat'], b['lon']) for a, b in zip(w, w[1:]))
        if path >= 5 and hav_m(w[0]['lat'], w[0]['lon'], w[-1]['lat'], w[-1]['lon']) < max_ratio * path:
            out.add(k)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src'); ap.add_argument('dst')
    ap.add_argument('--max-kmh', type=float, help='anything faster is treated as spoofed '
                                                  '(default by sport: cycling 60, running/walking/hiking 30; on foot also '
                                                  'anything over 10 km/h with no steps)')
    ap.add_argument('--max-km', type=float, default=100.0, help='positions further than this from the centre are dropped')
    ap.add_argument('--center', help='LAT,LON of the real activity area; default = median of all positions '
                                     '(give it explicitly if more than half of the positions are spoofed)')
    ap.add_argument('--keep-jitter', action='store_true',
                    help='running/walking/hiking only: do NOT remove GPS jitter recorded while standing still')
    a = ap.parse_args()
    header, msgs = parse(a.src)
    thr = resolve_max_kmh(a.max_kmh, msgs) / 3.6
    recs = load_records(msgs)
    clat, clon = center_of(recs, tuple(map(float, a.center.split(','))) if a.center else None)
    jitter = set() if a.keep_jitter else standing_jitter(recs, msgs, clat, clon, a.max_km)
    foot = session_sport(msgs) in FOOT_SPORTS and any((r['cad'] or 0) > 0 for r in recs)

    removed = 0.0; n_fake = n_pos = n_jit = 0; jit_m = 0.0
    prev_t = prev_d = None
    new = []   # (t, new_dist_m, new_speed_mps, lat_raw, lon_raw) per record, after correction
    for k, r in enumerate(recs):
        m, t, d, la, lo = r['m'], r['t'], r['dist'], r['lat'], r['lon']
        v = r['spd'] or 0.0
        if d is not None and prev_d is not None:
            dd, dt = d - prev_d, max(t - prev_t, 1)
            fake = max(v, dd / dt) > thr
        else:                                            # first record, or no distance: judge by speed alone
            dd, fake = 0.0, v > thr
        if not fake and foot and (r['cad'] or 0) == 0:      # GPS jump while not stepping
            fake = max(v, dd / dt if d is not None and prev_d is not None else 0) > NO_STEP_KMH / 3.6
        if fake:                                         # spoofed-GPS fallback
            removed += dd; n_fake += 1; v = 0.0
            put(m, 73, 0); put(m, 6, 0)
        elif k in jitter:                                # standing still, GPS wandering (foot sports)
            removed += dd; jit_m += dd; n_jit += 1; v = 0.0
            put(m, 73, 0); put(m, 6, 0); put(m, 0, None); put(m, 1, None); la = None
        if d is not None:
            prev_t, prev_d = t, d
            put(m, 5, int(round((d - removed) * 100)))
        if la is not None and hav_m(la, lo, clat, clon) > a.max_km * 1000:
            put(m, 0, None); put(m, 1, None); n_pos += 1; la = None
        new.append((t, None if d is None else d - removed, v,
                    None if la is None else r['lat_raw'], None if la is None else r['lon_raw']))

    # ---- summaries -----------------------------------------------------------------
    def window(t0, t1):
        idx = [k for k, r in enumerate(new) if t0 <= r[0] <= t1]
        before = [r[1] for r in new[:idx[0]] if r[1] is not None] if idx else []
        return [new[k] for k in idx], (before[-1] if before else 0.0)

    laps = [m for m in msgs if not m.is_def and m.gnum == LAP]
    for m in msgs:
        if m.is_def or m.gnum not in (LAP, SESSION):
            continue
        t0 = get(m, 2); el = get(m, 7) or 0
        t1 = float('inf') if laps and m is laps[-1] else t0 + el / 1000   # last lap runs to the end of the file
        w, first = window(t0, t1) if m.gnum == LAP else (new, 0.0)
        ds = [r[1] for r in w if r[1] is not None]
        if not ds:
            continue
        dist = ds[-1] - first
        timer = (get(m, 8) or 0) / 1000
        vmax = max(r[2] for r in w)
        avg_f, max_f = (124, 125) if m.gnum == SESSION else (110, 111)
        put(m, 9, int(round(dist * 100)))
        put(m, max_f, int(round(vmax * 1000)))
        if timer:
            put(m, avg_f, int(round(dist / timer * 1000)))
        avg16, max16 = (14, 15) if m.gnum == SESSION else (13, 14)
        for f16, v in ((avg16, dist / timer if timer else 0), (max16, vmax)):   # legacy 16-bit speed fields, if present
            if _loc(m, f16) and get(m, f16) is not None:
                put(m, f16, min(int(round(v * 1000)), 0xFFFE))
        pos = [(r[3], r[4]) for r in w if r[3] is not None]
        if pos:
            ne_lat, ne_lon, sw_lat, sw_lon = (29, 30, 31, 32) if m.gnum == SESSION else (27, 28, 29, 30)
            put(m, ne_lat, max(p[0] for p in pos)); put(m, ne_lon, max(p[1] for p in pos))
            put(m, sw_lat, min(p[0] for p in pos)); put(m, sw_lon, min(p[1] for p in pos))

    write(header, msgs, a.dst)
    print(f'records: {len(recs)}   spoofed-speed records zeroed: {n_fake}')
    print(f'fake distance removed: {(removed - jit_m) / 1000:.2f} km   spoofed positions dropped: {n_pos}')
    if n_jit:
        print(f'standing-still GPS jitter removed: {jit_m / 1000:.2f} km in {n_jit} records (positions wiped; --keep-jitter to skip)')
    print(f'new total: {new[-1][1] / 1000:.2f} km   new max speed: {max(r[2] for r in new) * 3.6:.1f} km/h')


if __name__ == '__main__':
    main()
