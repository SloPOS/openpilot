import queue
import subprocess
import sys

from openpilot.system.ui.widgets.script_runner import drain_stream_to_queue, follow_bottom_offset


def _drain(proc):
  q: queue.Queue[str] = queue.Queue()
  drain_stream_to_queue(proc.stdout, q)
  proc.wait()
  out = []
  while not q.empty():
    out.append(q.get_nowait())
  return out


def test_drain_captures_final_burst_after_process_exits():
  # Regression for the startup-diagnostics RESULTS truncation: the script prints
  # a burst of output right before exiting. Wait for the process to fully exit
  # first, then drain — the reader must still capture everything in the pipe.
  code = "for i in range(50): print(f'line {i}')\nprint('*** RESULTS')\nprint('- finding')"
  proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, bufsize=1)
  proc.wait()  # process is gone; output sits buffered in the pipe

  out = _drain(proc)

  assert "*** RESULTS" in out
  assert out[-1] == "- finding"
  assert len(out) == 52


def test_follow_bottom_pins_only_while_new_output_arrives():
  # Overflowing content with a new line: pin to the bottom (tail live output).
  assert follow_bottom_offset(1000, 600, line_count=20, last_count=19) == -400
  # Same content, no new line: leave the scroll alone so the user can scroll up.
  assert follow_bottom_offset(1000, 600, line_count=20, last_count=20) is None
  # Content fits on screen: nothing to follow.
  assert follow_bottom_offset(400, 600, line_count=8, last_count=7) is None


def test_drain_preserves_blank_lines():
  # Blank lines are kept (as "") — the diagnostics use them to space out the
  # RESULTS section, so they must survive to the screen.
  proc = subprocess.Popen([sys.executable, "-c", "print('a'); print(''); print('b')"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
  out = _drain(proc)
  assert out == ["a", "", "b"]
