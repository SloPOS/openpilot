from scripts.nap.startup_diagnostics import _count_process_crashes, classify_startup


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
