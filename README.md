# fit-gps-spoof-fix

Repair Garmin/Strava cycling activities corrupted by GPS spoofing or jamming — the track "teleports" to Lima (or elsewhere), speed spikes to 150–250 km/h, and the distance gains kilometres you never rode.

Packaged as a Claude skill (`SKILL.md`), but the scripts work on their own.

## How it works

While the wheel turns, the watch takes speed and distance from the wheel sensor, so riding under spoofing is recorded correctly — only lat/lon are wrong. The fake kilometres pile up **while stopped**: the sensor sleeps, the watch falls back to spoofed GPS speed, auto-pause lifts, and ~200 km/h "riding" gets logged with zero cadence and resting heart rate.

So instead of cutting the track, the fixer keeps every record and:

- zeroes speed on records above `--max-kmh` and removes their distance increment (shifting all later cumulative distance down);
- invalidates coordinates further than `--max-km` from the ride's median position (or `--center`);
- rewrites session/lap totals (distance, avg/max speed, bounding box) and recomputes the CRCs.

Heart rate, cadence, altitude, time and real sensor distance are preserved. The file is patched byte for byte — same size, all undocumented Garmin messages untouched.

## Usage

Python 3, no dependencies (except matplotlib for the preview).

```bash
python3 scripts/analyze_fit.py ride.fit                        # 1. diagnose, changes nothing
python3 scripts/fix_fit_spoof.py ride.fit ride_fixed.fit       # 2. fix (--max-kmh 60 --max-km 100 --center LAT,LON)
python3 scripts/verify_fit.py ride.fit ride_fixed.fit          # 3. independent check, exit 0 = OK
python3 scripts/preview_track.py ride.fit ride_fixed.fit preview.png   # 4. track preview
```

Read the `analyze_fit.py` output before fixing: the 60 km/h threshold is only safe when there's a clear gap between your real max speed and the fake speeds. See `SKILL.md` (Ukrainian) for the full decision guide and how to re-upload to Strava.

Get the **original FIT** (Strava → activity → ⋯ → Export Original, or Garmin Connect). GPX/TCX lack the raw fields this needs.

## Limits

Single (non-chained) FIT files; array fields are read by first element only.
