#!/usr/bin/env python3
"""Monitor published Tesla Pre-AP radar tracks without taking Panda ownership."""

import argparse
import math
import sys
import time

import cereal.messaging as messaging

CALIBRATION_MIN_LONG = 2.5
CALIBRATION_MAX_LONG = 14.5
CALIBRATION_MIN_LAT = -1.0
CALIBRATION_MAX_LAT = 1.0
DISPLAY_INTERVAL = 0.5


def p(msg: str = ""):
  print(msg, flush=True)


def parse_args(cli_args=None):
  parser = argparse.ArgumentParser(description="Monitor liveTracks radar output")
  parser.add_argument("--mode", choices=("calibrate", "test"), default="test")
  return parser.parse_args(cli_args)


def _finite(value: float) -> bool:
  return not math.isnan(value) and not math.isinf(value)


def _track_rows(points, calibrate: bool):
  rows = []
  for point in points:
    if calibrate and not (
      CALIBRATION_MIN_LONG <= point.dRel <= CALIBRATION_MAX_LONG
      and CALIBRATION_MIN_LAT <= point.yRel <= CALIBRATION_MAX_LAT
    ):
      continue
    if point.dRel <= 0:
      continue
    rows.append(point)
  rows.sort(key=lambda point: point.dRel)
  return rows


def _print_header(mode: str):
  p("=" * 60)
  p("LIVE RADAR CALIBRATION" if mode == "calibrate" else "LIVE RADAR TEST")
  p("=" * 60)
  p("Reads openpilot's liveTracks output. It does not stop manager, pandad, card, or controlsd.")
  p("Use this for development only while the vehicle is under direct driver control.")
  if mode == "calibrate":
    p("")
    p("Calibration view shows tracks in the target window:")
    p(f"  distance: {CALIBRATION_MIN_LONG}-{CALIBRATION_MAX_LONG}m")
    p(f"  lateral:  {CALIBRATION_MIN_LAT} to {CALIBRATION_MAX_LAT}m")
  p("=" * 60)


def _print_tracks(points, elapsed: float, mode: str, valid: bool):
  rows = _track_rows(points, calibrate=(mode == "calibrate"))
  status = "valid" if valid else "stale/invalid"
  label = "in calibration window" if mode == "calibrate" else "tracked"
  p(f"\n--- [{elapsed:.0f}s] {len(rows)} {label} ({status}) ---")

  if not rows:
    p("  (no radar tracks)")
    return

  p(f"  {'id':>4s}  {'dist(m)':>8s}  {'lat(m)':>8s}  {'vRel':>8s}  {'aRel':>8s}  {'meas':>5s}")
  p(f"  {'----':>4s}  {'-------':>8s}  {'------':>8s}  {'----':>8s}  {'----':>8s}  {'----':>5s}")
  for point in rows[:16]:
    a_rel = f"{point.aRel:8.2f}" if _finite(point.aRel) else "     nan"
    p(
      f"  {point.trackId:4d}  {point.dRel:8.2f}  {point.yRel:8.2f}  "
      + f"{point.vRel:8.2f}  {a_rel}  {str(point.measured):>5s}"
    )
  if len(rows) > 16:
    p(f"  ... {len(rows) - 16} more")


def main(cli_args=None):
  args = parse_args(cli_args)
  _print_header(args.mode)

  sm = messaging.SubMaster(["liveTracks"])
  start_time = time.monotonic()
  last_display = 0.0

  p("\nWaiting for liveTracks...")
  while True:
    try:
      sm.update(100)
      now = time.monotonic()
      if now - last_display < DISPLAY_INTERVAL:
        continue
      last_display = now

      if not sm.updated["liveTracks"]:
        p(f"  [{now - start_time:.0f}s] no liveTracks update")
        continue

      tracks = sm["liveTracks"]
      _print_tracks(tracks.points, now - start_time, args.mode, sm.valid["liveTracks"])
    except KeyboardInterrupt:
      p("\nStopped.")
      return 0
    except Exception as e:
      p(f"\nERROR: {e}")
      import traceback
      traceback.print_exc()
      return 1


if __name__ == "__main__":
  sys.exit(main())
