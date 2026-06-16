from pathlib import Path

from scripts.nap.startup_diagnostics import _count_process_crashes, classify_startup, render_logs_view


def test_classify_no_panda_states_points_at_panda_layer():
  findings = classify_startup({
    "processes": {"manager": True, "pandad": True},
    "device_state": {"started": False},
    "panda_states": [],
    "can": {"total_frames": 0, "gtw_348_by_bus": {}},
    "startup_blocked": False,
    "card_crashes": 0,
  })

  assert "NO PANDA STATE: manager is up, but pandaStates is empty." in findings


def test_classify_no_ignition_points_at_can_348():
  findings = classify_startup({
    "processes": {"manager": True, "pandad": True},
    "device_state": {"started": False},
    "panda_states": [
      {"ignitionLine": False, "ignitionCan": False, "pandaType": "tres"}
    ],
    "can": {"total_frames": 1200, "gtw_348_by_bus": {}},
    "startup_blocked": False,
    "card_crashes": 0,
  })

  assert "NO IGNITION: panda is not reporting ignitionLine or ignitionCan." in findings
  assert "NO GTW 0x348: live CAN did not include Tesla GTW_status on bus 0 or 1." in findings


def test_classify_ignition_without_started_points_at_startup_gate():
  findings = classify_startup({
    "processes": {"manager": True, "pandad": True, "card": False},
    "device_state": {"started": False},
    "panda_states": [
      {"ignitionLine": False, "ignitionCan": True, "pandaType": "tres"}
    ],
    "can": {"total_frames": 800, "gtw_348_by_bus": {0: 100}},
    "startup_blocked": True,
    "card_crashes": 0,
  })

  assert "STARTUP BLOCKED: ignition is true, but deviceState.started is false." in findings


def test_count_process_crashes_ignores_benign_mentions():
  # collect_logs greps for the words "card"/"controlsd", so normal lines that
  # merely name the process must not be counted as crashes.
  benign = "\n".join([
    "card: published carState",
    "controlsd: lateral active",
    "boardd: card heartbeat ok",
  ])
  assert _count_process_crashes(benign) == 0


def test_count_process_crashes_counts_real_crash_markers():
  crashing = "\n".join([
    "card: published carState",
    "Traceback (most recent call last): in card",
    "controlsd exited with code 1",
  ])
  assert _count_process_crashes(crashing) == 2


def test_render_logs_view_shows_findings_and_error_logs():
  snapshot = {
    "findings": ["NO PANDA STATE: manager is up, but pandaStates is empty."],
    "logs": {
      "/tmp/launch_log": "booting...",
      "data_log_matches_tail": "Traceback (most recent call last):\n  card crashed",
    },
  }
  out = render_logs_view(snapshot, Path("/data/nap_diagnostics/startup_diag_x.txt"))

  assert "NO PANDA STATE: manager is up, but pandaStates is empty." in out
  assert "--- data_log_matches_tail ---" in out
  assert "Traceback" in out
  assert "/data/nap_diagnostics/startup_diag_x.txt" in out


def test_classify_driver_camera_down_when_road_ok_but_driver_dead():
  findings = classify_startup({
    "processes": {"manager": True, "pandad": True, "camerad": True},
    "device_state": {"started": True},
    "panda_states": [{"ignitionCan": True, "pandaType": "tres"}],
    "can": {"total_frames": 100, "gtw_348_by_bus": {0: 10}},
    "cameras": {"received": {"roadCameraState": 120, "driverCameraState": 0, "wideRoadCameraState": 120}},
    "startup_blocked": False,
    "card_crashes": 0,
  })
  assert any("DRIVER CAMERA DOWN" in f for f in findings)


def test_classify_no_camera_finding_when_camerad_not_running():
  # Offroad camerad is off; absent camera frames are normal, not a malfunction.
  findings = classify_startup({
    "processes": {"manager": True, "pandad": True, "camerad": False},
    "device_state": {"started": False},
    "panda_states": [{"ignitionCan": True, "pandaType": "tres"}],
    "can": {"total_frames": 100, "gtw_348_by_bus": {0: 10}},
    "cameras": {"received": {}},
    "startup_blocked": False,
    "card_crashes": 0,
  })
  assert not any("CAMERA" in f for f in findings)


def test_render_logs_view_handles_no_logs():
  out = render_logs_view({"findings": ["NO CLEAR FAILURE"], "logs": {}}, Path("/tmp/x.txt"))
  assert "(no logs collected)" in out
