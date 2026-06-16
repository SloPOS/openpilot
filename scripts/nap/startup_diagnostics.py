#!/usr/bin/env python3
"""Collect startup diagnostics without stopping openpilot.

This module is intentionally safe to run from the in-process raylib script
runner: it does not set NAPScriptRunning, kill tmux, restart manager, or reboot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any


PARAM_KEYS = [
  "DongleId",
  "HardwareSerial",
  "GitBranch",
  "GitCommit",
  "GitRemote",
  "IsReleaseBranch",
  "IsTestedBranch",
  "HasAcceptedTerms",
  "TermsVersion",
  "CompletedTrainingVersion",
  "OpenpilotEnabledToggle",
  "IsDriverViewEnabled",
  "IsTakingSnapshot",
  "DoUninstall",
  "DoReboot",
  "DoShutdown",
  "Offroad_ConnectivityNeeded",
  "SnoozeUpdate",
  "DisableUpdates",
  "NAPScriptRunning",
  "NAPForcePreAP",
  "NAPPedalEnabled",
  "NAPPedalCalibDone",
  "NAPPedalCanBus",
  "NAPRadarEnabled",
  "NAPRadarBehindNosecone",
]

PROCESS_PATTERNS = {
  "manager": ("manager.py", "system.manager.manager"),
  "hardwared": ("hardwared.py", "system.hardware.hardwared"),
  "pandad": ("selfdrive.pandad.pandad",),
  "card": ("selfdrive.car.card",),
  "controlsd": ("selfdrive.controls.controlsd",),
  "ui": ("selfdrive.ui.ui",),
  "camerad": ("./camerad", "system/camerad"),
  "dmonitoringmodeld": ("selfdrive.modeld.dmonitoringmodeld",),
}

CAMERA_SERVICES = ["roadCameraState", "driverCameraState", "wideRoadCameraState"]


def _print(line: str = "") -> None:
  print(line, flush=True)


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str]:
  try:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
  except Exception as e:
    return 1, f"{type(e).__name__}: {e}"
  output = (proc.stdout or "") + (proc.stderr or "")
  return proc.returncode, output.strip()


def _read_text(path: str) -> str | None:
  try:
    return Path(path).read_text(encoding="utf-8", errors="replace").strip("\x00\n ")
  except OSError:
    return None


def _decode_param_value(value: bytes | str | int | float | bool | None) -> str | None:
  if value is None:
    return None
  if isinstance(value, bytes):
    return value.decode("utf-8", errors="replace")
  return str(value)


def _capnp_to_dict(value: Any) -> Any:
  if value is None:
    return None
  if isinstance(value, (str, int, float, bool)):
    return value
  if isinstance(value, list):
    return [_capnp_to_dict(item) for item in value]
  if hasattr(value, "to_dict"):
    try:
      return value.to_dict()
    except Exception:
      return str(value)
  if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, dict)):
    try:
      return [_capnp_to_dict(item) for item in value]
    except Exception:
      return str(value)
  return str(value)


def _safe_get(mapping: dict[str, Any], *keys: str) -> Any:
  value: Any = mapping
  for key in keys:
    if not isinstance(value, dict):
      return None
    value = value.get(key)
  return value


def _normalize_panda_states(value: Any) -> list[dict[str, Any]]:
  if isinstance(value, dict) and "pandaStates" in value:
    value = value["pandaStates"]
  if isinstance(value, str) and value.strip() == "[]":
    return []
  if not isinstance(value, list):
    return []
  return [state for state in value if isinstance(state, dict)]


def classify_startup(snapshot: dict[str, Any]) -> list[str]:
  """Return concise startup-chain findings for video capture."""
  findings: list[str] = []
  processes = snapshot.get("processes") or {}
  device_state = snapshot.get("device_state") or {}
  panda_states = _normalize_panda_states(snapshot.get("panda_states"))
  can = snapshot.get("can") or {}

  if not processes.get("manager"):
    findings.append("MANAGER DOWN: manager.py is not running, so openpilot cannot enter onroad.")

  if processes.get("manager") and processes.get("pandad") and not panda_states:
    findings.append("NO PANDA STATE: manager is up, but pandaStates is empty.")

  ignition_true = any(
    bool(state.get("ignitionLine")) or bool(state.get("ignitionCan"))
    for state in panda_states
    if isinstance(state, dict)
  )

  if panda_states and not ignition_true:
    findings.append("NO IGNITION: panda is not reporting ignitionLine or ignitionCan.")

  gtw_348_by_bus = can.get("gtw_348_by_bus") or {}
  has_348_on_car_bus = any(int(bus) in (0, 1) and count for bus, count in gtw_348_by_bus.items())
  if can.get("total_frames", 0) > 0 and not has_348_on_car_bus:
    findings.append("NO GTW 0x348: live CAN did not include Tesla GTW_status on bus 0 or 1.")

  if ignition_true and not bool(device_state.get("started")):
    findings.append("STARTUP BLOCKED: ignition is true, but deviceState.started is false.")

  if snapshot.get("startup_blocked"):
    findings.append("STARTUP BLOCK LOG FOUND: check terms, training, registration, update, and driver-view params.")

  if snapshot.get("card_crashes", 0) > 0:
    findings.append("CARD CRASH: recent logs mention card/controlsd crashes.")

  if bool(device_state.get("started")):
    findings.append("STARTED TRUE: deviceState.started is true. If UI is still home, this is likely a UI/display issue.")

  cam_received = (snapshot.get("cameras") or {}).get("received") or {}
  if processes.get("camerad"):
    road_frames = cam_received.get("roadCameraState", 0)
    driver_frames = cam_received.get("driverCameraState", 0)
    if road_frames and not driver_frames:
      findings.append(
        "DRIVER CAMERA DOWN: camerad is up and the road camera is publishing, but driverCameraState "
        + "has no frames. The driver/interior sensor likely failed to probe (a covered lens does NOT cause this)."
      )
    elif not road_frames and not driver_frames:
      findings.append("NO CAMERA FRAMES: camerad is up but no camera states are publishing.")

  if not findings:
    findings.append("NO CLEAR FAILURE: send the video and saved diagnostics bundle path.")

  return findings


def collect_device_info() -> dict[str, str | None]:
  return {
    "datetime_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    "hostname": _read_text("/proc/sys/kernel/hostname"),
    "device_model": _read_text("/sys/firmware/devicetree/base/model"),
    "agnos": _read_text("/AGNOS"),
    "version": _read_text("/VERSION"),
    "build": _read_text("/BUILD"),
    "tici_marker": "present" if Path("/TICI").exists() else "missing",
  }


def collect_git_info() -> dict[str, str]:
  commands = {
    "branch": ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    "commit": ["git", "rev-parse", "HEAD"],
    "remote": ["git", "remote", "get-url", "origin"],
    "status": ["git", "status", "--short", "--branch"],
  }
  return {name: _run(cmd)[1] for name, cmd in commands.items()}


def collect_params() -> dict[str, str | None]:
  params: dict[str, str | None] = {}
  try:
    from openpilot.common.params import Params

    store = Params()
    for key in PARAM_KEYS:
      try:
        params[key] = _decode_param_value(store.get(key))
      except Exception as e:
        params[key] = f"ERROR: {type(e).__name__}: {e}"
  except Exception as e:
    params["ERROR"] = f"{type(e).__name__}: {e}"
  return params


def collect_processes() -> dict[str, bool]:
  code, output = _run(["ps", "-eo", "pid,args"])
  process_text = output if code == 0 else ""
  return {name: any(pattern in process_text for pattern in patterns) for name, patterns in PROCESS_PATTERNS.items()}


def collect_live_messages(sample_seconds: float) -> dict[str, Any]:
  result: dict[str, Any] = {
    "device_state": {},
    "panda_states": [],
    "manager_state": {},
    "peripheral_state": {},
    "message_errors": [],
  }
  try:
    import cereal.messaging as messaging

    sm = messaging.SubMaster(["deviceState", "pandaStates", "managerState", "peripheralState"], poll="deviceState")
    deadline = time.monotonic() + sample_seconds
    while time.monotonic() < deadline:
      sm.update(100)

    result["device_state"] = _capnp_to_dict(sm["deviceState"])
    result["panda_states"] = _normalize_panda_states(_capnp_to_dict(sm["pandaStates"]))
    result["manager_state"] = _capnp_to_dict(sm["managerState"])
    result["peripheral_state"] = _capnp_to_dict(sm["peripheralState"])
    result["alive"] = dict(sm.alive)
    result["updated"] = dict(sm.updated)
  except Exception as e:
    result["message_errors"].append(f"{type(e).__name__}: {e}")
  return result


def collect_can(sample_seconds: float) -> dict[str, Any]:
  can: dict[str, Any] = {
    "total_frames": 0,
    "frames_by_bus": {},
    "gtw_348_by_bus": {},
    "top_addrs_by_bus": {},
    "errors": [],
  }
  try:
    import cereal.messaging as messaging

    sock = messaging.sub_sock("can", timeout=100)
    deadline = time.monotonic() + sample_seconds
    frames_by_bus: Counter[int] = Counter()
    gtw_348_by_bus: Counter[int] = Counter()
    addrs_by_bus: dict[int, Counter[int]] = {}

    while time.monotonic() < deadline:
      msg = messaging.recv_one_or_none(sock)
      if msg is None:
        continue
      for frame in msg.can:
        bus = int(frame.src)
        addr = int(frame.address)
        can["total_frames"] += 1
        frames_by_bus[bus] += 1
        if addr == 0x348:
          gtw_348_by_bus[bus] += 1
        addrs_by_bus.setdefault(bus, Counter())[addr] += 1

    can["frames_by_bus"] = dict(sorted(frames_by_bus.items()))
    can["gtw_348_by_bus"] = dict(sorted(gtw_348_by_bus.items()))
    can["top_addrs_by_bus"] = {
      bus: [(f"0x{addr:x}", count) for addr, count in counter.most_common(12)]
      for bus, counter in sorted(addrs_by_bus.items())
    }
  except Exception as e:
    can["errors"].append(f"{type(e).__name__}: {e}")
  return can


def collect_cameras(sample_seconds: float) -> dict[str, Any]:
  """Sample the camera state messages to see which cameras are publishing frames.

  Only meaningful while camerad is running (onroad / driver view); offroad the
  cameras are off and this is expected to be empty. The persisted camerad log is
  the reliable signal when running offroad.
  """
  cameras: dict[str, Any] = {"received": {}, "sensor": {}, "alive": {}, "errors": []}
  try:
    import cereal.messaging as messaging

    sm = messaging.SubMaster(CAMERA_SERVICES)
    counts: Counter[str] = Counter()
    deadline = time.monotonic() + sample_seconds
    while time.monotonic() < deadline:
      sm.update(100)
      for service in CAMERA_SERVICES:
        if sm.updated[service]:
          counts[service] += 1

    for service in CAMERA_SERVICES:
      cameras["received"][service] = counts[service]
      if counts[service] > 0:
        try:
          cameras["sensor"][service] = str(sm[service].sensor)
        except Exception:
          pass
    cameras["alive"] = {service: bool(sm.alive[service]) for service in CAMERA_SERVICES}
  except Exception as e:
    cameras["errors"].append(f"{type(e).__name__}: {e}")
  return cameras


def collect_logs() -> dict[str, Any]:
  logs: dict[str, Any] = {}
  log_paths = ["/tmp/launch_log", "/tmp/nap_script_runner.log"]
  for path in log_paths:
    text = _read_text(path)
    if text:
      logs[path] = "\n".join(text.splitlines()[-120:])

  _, tmux_output = _run(["tmux", "capture-pane", "-pt", "comma", "-S", "-220"], timeout=2.0)
  if tmux_output:
    logs["tmux_comma_tail"] = tmux_output

  search_root = "/data/log"
  if Path(search_root).exists():
    patterns = [
      "Startup blocked",
      "Traceback",
      "No pandas",
      "panda",
      "card",
      "controlsd",
      "ignition",
      "Registration",
      "training",
      "terms",
    ]
    cmd = ["grep", "-RInaE", "|".join(patterns), search_root]
    _, grep_output = _run(cmd, timeout=8.0)
    if grep_output:
      lines = grep_output.splitlines()
      logs["data_log_matches_tail"] = "\n".join(lines[-160:])

    # camerad / sensor probe failures (why a camera is not outputting frames)
    cam_patterns = ["camerad", "sensor", "CSIError", "probe", "no devices", "v4l", "ife", "MIPI"]
    cam_cmd = ["grep", "-RInaiE", "|".join(cam_patterns), search_root]
    _, cam_output = _run(cam_cmd, timeout=8.0)
    if cam_output:
      logs["camera_log_matches_tail"] = "\n".join(cam_output.splitlines()[-100:])
  return logs


def _count_process_crashes(log_text: str) -> int:
  """Count log lines that name card/controlsd alongside a real crash marker.

  collect_logs greps /data/log for the literal words "card" and "controlsd",
  so a bare substring count is always positive on a live device. Require a
  crash marker on the same line so the finding means something.
  """
  crash_markers = ("Traceback", "crashed", "Segmentation", "core dumped", "exited with", "SIGSEGV", "SIGABRT")
  count = 0
  for line in log_text.splitlines():
    if ("card" in line or "controlsd" in line) and any(marker in line for marker in crash_markers):
      count += 1
  return count


def build_snapshot(sample_seconds: float) -> dict[str, Any]:
  _print("*** Collecting device and git info")
  snapshot: dict[str, Any] = {
    "device_info": collect_device_info(),
    "git": collect_git_info(),
  }

  _print("*** Reading params and processes")
  snapshot["params"] = collect_params()
  snapshot["processes"] = collect_processes()

  _print(f"*** Sampling live openpilot messages for {sample_seconds:.0f}s")
  live = collect_live_messages(sample_seconds)
  snapshot.update({
    "device_state": live.get("device_state") or {},
    "panda_states": live.get("panda_states") or [],
    "manager_state": live.get("manager_state") or {},
    "peripheral_state": live.get("peripheral_state") or {},
    "message_alive": live.get("alive") or {},
    "message_updated": live.get("updated") or {},
    "message_errors": live.get("message_errors") or [],
  })

  _print(f"*** Sampling live CAN for {sample_seconds:.0f}s")
  snapshot["can"] = collect_can(sample_seconds)

  _print(f"*** Sampling live cameras for {sample_seconds:.0f}s")
  snapshot["cameras"] = collect_cameras(sample_seconds)

  _print("*** Collecting recent logs")
  snapshot["logs"] = collect_logs()
  log_text = "\n".join(str(value) for value in snapshot["logs"].values())
  snapshot["startup_blocked"] = "Startup blocked" in log_text
  snapshot["card_crashes"] = _count_process_crashes(log_text)
  snapshot["findings"] = classify_startup(snapshot)
  return snapshot


def _format_mapping(mapping: dict[str, Any], indent: int = 2) -> list[str]:
  lines: list[str] = []
  pad = " " * indent
  for key in sorted(mapping):
    value = mapping[key]
    if isinstance(value, dict):
      lines.append(f"{pad}{key}:")
      lines.extend(_format_mapping(value, indent + 2))
    else:
      lines.append(f"{pad}{key}: {value}")
  return lines


def render_report(snapshot: dict[str, Any]) -> str:
  lines: list[str] = []
  lines.append("NAP STARTUP DIAGNOSTICS")
  lines.append("=" * 32)
  lines.append("")
  lines.append("FINDINGS:")
  lines.extend(f"  - {finding}" for finding in snapshot["findings"])
  lines.append("")

  for section in [
    "device_info",
    "git",
    "params",
    "processes",
    "device_state",
    "panda_states",
    "manager_state",
    "peripheral_state",
    "can",
    "cameras",
  ]:
    lines.append(section.upper())
    value = snapshot.get(section)
    if isinstance(value, dict):
      lines.extend(_format_mapping(value))
    else:
      lines.append(f"  {value}")
    lines.append("")

  if snapshot.get("message_alive"):
    lines.append("MESSAGE ALIVE:")
    lines.extend(_format_mapping(snapshot["message_alive"]))
    lines.append("")

  if snapshot.get("message_errors"):
    lines.append("MESSAGE ERRORS:")
    lines.extend(f"  - {error}" for error in snapshot["message_errors"])
    lines.append("")

  lines.append("LOGS:")
  for name, content in (snapshot.get("logs") or {}).items():
    lines.append(f"--- {name} ---")
    lines.append(str(content))
    lines.append("")

  return "\n".join(lines)


def save_report(report: str) -> Path:
  base = Path("/data/nap_diagnostics") if Path("/data").exists() else Path("/tmp/nap_diagnostics")
  base.mkdir(parents=True, exist_ok=True)
  stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
  path = base / f"startup_diag_{stamp}.txt"
  path.write_text(report, encoding="utf-8")
  return path


def print_video_summary(snapshot: dict[str, Any], report_path: Path) -> None:
  _print("")
  _print("*** RESULTS")
  for finding in snapshot["findings"]:
    _print(f"- {finding}")
  _print("")
  _print(f"Saved report: {report_path}")
  _print("")
  _print("Quick state:")
  _print(f"  branch: {_safe_get(snapshot, 'git', 'branch')}")
  _print(f"  commit: {_safe_get(snapshot, 'git', 'commit')}")
  _print(f"  manager: {_safe_get(snapshot, 'processes', 'manager')}")
  _print(f"  pandad: {_safe_get(snapshot, 'processes', 'pandad')}")
  _print(f"  card: {_safe_get(snapshot, 'processes', 'card')}")
  _print(f"  deviceState.started: {_safe_get(snapshot, 'device_state', 'started')}")
  _print(f"  pandaStates: {snapshot.get('panda_states')}")
  _print(f"  CAN frames by bus: {_safe_get(snapshot, 'can', 'frames_by_bus')}")
  _print(f"  GTW 0x348 by bus: {_safe_get(snapshot, 'can', 'gtw_348_by_bus')}")
  _print(f"  camerad: {_safe_get(snapshot, 'processes', 'camerad')}")
  _print(f"  camera frames: {_safe_get(snapshot, 'cameras', 'received')}")
  _print(f"  camera sensors: {_safe_get(snapshot, 'cameras', 'sensor')}")
  _print("")
  _print("Leave this screen visible and send a video of these results.")


def render_logs_view(snapshot: dict[str, Any], report_path: Path) -> str:
  """Findings followed by the collected logs — the on-screen 'why it failed' view.

  Findings go on top (plain-English reason); the raw logs go last so the screen,
  which auto-scrolls to the bottom, frames the most recent error lines for video.
  """
  lines: list[str] = []
  lines.append("*** RESULTS")
  lines.extend(f"- {finding}" for finding in snapshot["findings"])
  lines.append("")
  lines.append(f"Saved report: {report_path}")
  lines.append("")
  lines.append("*** LOGS")
  logs = snapshot.get("logs") or {}
  if not logs:
    lines.append("  (no logs collected)")
  for name, content in logs.items():
    lines.append("")
    lines.append(f"--- {name} ---")
    lines.append(str(content))
  lines.append("")
  lines.append("Leave this screen visible and send a video of these results.")
  return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--sample-seconds", type=float, default=6.0)
  parser.add_argument("--logs", action="store_true",
                      help="print collected error logs instead of the quick-state summary")
  args = parser.parse_args(argv)

  _print("NAP Startup Diagnostics")
  _print("Keep the car powered on and leave the device on this screen.")
  _print("")

  snapshot = build_snapshot(max(1.0, args.sample_seconds))
  report = render_report(snapshot)
  report_path = save_report(report)
  if args.logs:
    _print(render_logs_view(snapshot, report_path))
  else:
    print_video_summary(snapshot, report_path)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
