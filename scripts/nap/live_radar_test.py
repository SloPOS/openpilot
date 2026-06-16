#!/usr/bin/env python3
"""Full-track wrapper for the live radar monitor."""

from scripts.nap.live_radar_monitor import main


if __name__ == "__main__":
  raise SystemExit(main(["--mode", "test"]))
