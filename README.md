# fit-gps-spoof-fix

Repair Garmin/Strava cycling activities corrupted by GPS spoofing or jamming — the track "teleports" to Lima (or elsewhere), speed spikes to 150–250 km/h, and the distance gains kilometres you never rode.

It's an [Agent Skill](https://agentskills.io): drop it into Claude Code or any skills-compatible agent, upload your `.fit`, and ask it to fix the ride. The scripts also work on their own.

## Install

**Claude Code**

```bash
claude plugin marketplace add msemitkin/fit-gps-spoof-fix
claude plugin install fit-gps-spoof-fix@msemitkin
```

Or from inside a session: `/plugin install fit-gps-spoof-fix --marketplace msemitkin/fit-gps-spoof-fix`

**Other agents (Codex, Cursor, Gemini CLI, etc.)**

Copy the `skills/fit-gps-spoof-fix/` folder into your agent's skills directory (for example `~/.claude/skills/`, `~/.codex/skills/` or the project's skills folder — check your agent's docs).

**claude.ai**

Zip the `skills/fit-gps-spoof-fix/` folder and upload it as a custom skill in your claude.ai settings.

## How it works

While the wheel turns, the watch takes speed and distance from the wheel sensor, so riding under spoofing is recorded correctly — only lat/lon are wrong. The fake kilometres pile up **while stopped**: the sensor sleeps, the watch falls back to spoofed GPS speed, auto-pause lifts, and ~200 km/h "riding" gets logged with zero cadence and resting heart rate.

So instead of cutting the track, the fixer keeps every record and:

- zeroes speed on records above `--max-kmh` and removes their distance increment (shifting all later cumulative distance down);
- invalidates coordinates further than `--max-km` from the ride's median position (or `--center`);
- rewrites session/lap totals (distance, avg/max speed, bounding box) and recomputes the CRCs.

Heart rate, cadence, altitude, time and real sensor distance are preserved. The file is patched byte for byte — same size, all undocumented Garmin messages untouched.

## Run the scripts directly

Python 3, no dependencies (matplotlib only for the preview).

```bash
S=skills/fit-gps-spoof-fix/scripts
python3 $S/analyze_fit.py ride.fit                             # 1. diagnose, changes nothing
python3 $S/fix_fit_spoof.py ride.fit ride_fixed.fit            # 2. fix (--max-kmh 60 --max-km 100 --center LAT,LON)
python3 $S/verify_fit.py ride.fit ride_fixed.fit               # 3. independent check, exit 0 = OK
python3 $S/preview_track.py ride.fit ride_fixed.fit preview.png   # 4. track preview
```

Read the `analyze_fit.py` output before fixing: the 60 km/h threshold is only safe when there's a clear gap between your real max speed and the fake speeds. See [`SKILL.md`](skills/fit-gps-spoof-fix/SKILL.md) (Ukrainian) for the full decision guide and how to re-upload to Strava.

Get the **original FIT** (Strava → activity → ⋯ → Export Original, or Garmin Connect). GPX/TCX lack the raw fields this needs.

## Limits

Single (non-chained) FIT files; array fields are read by first element only. Without a wheel speed sensor, real distance ridden during spoofing can't be recovered.

## License

MIT
