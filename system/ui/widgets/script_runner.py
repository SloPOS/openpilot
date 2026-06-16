"""
Script Runner Widget

Full-screen overlay for executing and displaying script output.
Provides instructions, start button, live output, and exit functionality.
"""

import subprocess
import threading
import queue
import pyray as rl
from dataclasses import dataclass
from collections.abc import Callable
from openpilot.common.params import Params
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.scroll_panel import GuiScrollPanel
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle

MARGIN = 50
TITLE_FONT_SIZE = 80
TEXT_FONT_SIZE = 50
OUTPUT_FONT_SIZE = 40
LINE_HEIGHT = 50
BUTTON_WIDTH = 400
BUTTON_HEIGHT = 120
BUTTON_SPACING = 30
GROUPED_BUTTON_WIDTH = 330
EPAS_RISK_ACK_PARAM = "NAPEpasRiskAccepted"


class ScriptState:
  """Script execution states"""
  READY = 0      # Showing instructions, waiting for Start
  RUNNING = 1    # Script is executing
  COMPLETED = 2  # Script finished successfully
  ERROR = 3      # Script encountered an error


class ScriptRunner(Widget):
  """
  Full-screen widget for running scripts with live output display.

  Usage:
      runner = ScriptRunner(
          title="Pedal Calibration",
          instructions="Instructions for the user...",
          script_module="scripts.nap.calibrate_pedal",
          on_close=lambda: gui_app.set_modal_overlay(None)
      )
      gui_app.set_modal_overlay(runner)
  """

  def __init__(
      self,
      title: str,
      instructions: str,
      script_module: str,
      on_close: Callable | None = None,
      cwd: str = "/data/openpilot"
  ):
    super().__init__()
    self._title = title
    self._instructions = instructions
    self._script_module = script_module
    self._on_close = on_close
    self._cwd = cwd

    self._state = ScriptState.READY
    self._output_lines: list[str] = []
    self._output_queue: queue.Queue[str] = queue.Queue()
    self._process: subprocess.Popen | None = None
    self._reader_thread: threading.Thread | None = None

    # UI components
    self._scroll_panel = GuiScrollPanel()
    self._font = gui_app.font(FontWeight.NORMAL)
    self._title_font = gui_app.font(FontWeight.BOLD)

    # Buttons
    self._start_button = Button(
      "Start",
      click_callback=self._on_start_clicked,
      button_style=ButtonStyle.PRIMARY,
      font_size=TEXT_FONT_SIZE
    )
    self._exit_button = Button(
      "Exit",
      click_callback=self._on_exit_clicked,
      button_style=ButtonStyle.TRANSPARENT_WHITE_BORDER,
      font_size=TEXT_FONT_SIZE
    )

    # Wrap instructions text
    self._instruction_lines: list[str] = []

  def _wrap_text(self, text: str, max_width: float, font_size: int) -> list[str]:
    """Wrap text to fit within max_width"""
    lines = []
    for paragraph in text.split("\n"):
      if not paragraph.strip():
        lines.append("")
        continue

      words = paragraph.split()
      current_line = ""

      for word in words:
        test_line = f"{current_line} {word}".strip()
        text_width = measure_text_cached(self._font, test_line, font_size).x

        if text_width <= max_width:
          current_line = test_line
        else:
          if current_line:
            lines.append(current_line)
          current_line = word

      if current_line:
        lines.append(current_line)

    return lines

  def _on_start_clicked(self):
    """Start script execution"""
    if self._state != ScriptState.READY:
      return

    self._state = ScriptState.RUNNING
    self._output_lines = ["Starting script...", ""]

    # Start the script in a subprocess
    try:
      self._process = subprocess.Popen(
        ["python", "-m", self._script_module],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=self._cwd,
        text=True,
        bufsize=1  # Line buffered
      )

      # Start reader thread
      self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
      self._reader_thread.start()

    except Exception as e:
      self._output_lines.append(f"Error starting script: {e}")
      self._state = ScriptState.ERROR

  def _read_output(self):
    """Read script output in background thread"""
    try:
      if self._process and self._process.stdout:
        for line in iter(self._process.stdout.readline, ''):
          if line:
            self._output_queue.put(line.rstrip())
          if self._process.poll() is not None:
            break

      # Wait for process to complete
      if self._process:
        return_code = self._process.wait()
        if return_code == 0:
          self._output_queue.put("\n[Script completed successfully]")
          self._state = ScriptState.COMPLETED
        else:
          self._output_queue.put(f"\n[Script exited with code {return_code}]")
          self._state = ScriptState.ERROR

    except Exception as e:
      self._output_queue.put(f"\n[Error reading output: {e}]")
      self._state = ScriptState.ERROR

  def _on_exit_clicked(self):
    """Handle exit button click"""
    # Kill process if still running
    if self._process and self._process.poll() is None:
      self._process.terminate()
      try:
        self._process.wait(timeout=2)
      except subprocess.TimeoutExpired:
        self._process.kill()

    if self._on_close:
      self._on_close()

  def _process_output_queue(self):
    """Process any pending output from the queue"""
    while True:
      try:
        line = self._output_queue.get_nowait()
        self._output_lines.append(line)
      except queue.Empty:
        break

  def _render(self, rect: rl.Rectangle):
    # Draw background
    rl.draw_rectangle_rec(rect, rl.Color(20, 20, 20, 255))

    # Process any pending output
    self._process_output_queue()

    content_x = rect.x + MARGIN
    content_width = rect.width - MARGIN * 2
    current_y = rect.y + MARGIN

    # Draw title
    title_size = measure_text_cached(self._title_font, self._title, TITLE_FONT_SIZE)
    rl.draw_text_ex(
      self._title_font,
      self._title,
      rl.Vector2(content_x, current_y),
      TITLE_FONT_SIZE,
      0,
      rl.WHITE
    )
    current_y += title_size.y + MARGIN

    # Draw separator line
    rl.draw_line_ex(
      rl.Vector2(content_x, current_y),
      rl.Vector2(content_x + content_width, current_y),
      2,
      rl.Color(80, 80, 80, 255)
    )
    current_y += MARGIN

    if self._state == ScriptState.READY:
      # Show instructions
      self._render_instructions(rect, content_x, content_width, current_y)
    else:
      # Show output
      self._render_output(rect, content_x, content_width, current_y)

    # Draw buttons at bottom
    self._render_buttons(rect)

    return None

  def _render_instructions(self, rect: rl.Rectangle, content_x: float, content_width: float, start_y: float):
    """Render instruction text"""
    # Wrap instructions if not already done
    if not self._instruction_lines:
      self._instruction_lines = self._wrap_text(self._instructions, content_width, TEXT_FONT_SIZE)

    # Calculate text area
    button_area_height = BUTTON_HEIGHT + MARGIN * 2
    text_area_height = rect.height - start_y - button_area_height - rect.y

    # Draw instructions
    current_y = start_y
    for line in self._instruction_lines:
      if current_y + LINE_HEIGHT > start_y + text_area_height:
        break
      rl.draw_text_ex(
        self._font,
        line,
        rl.Vector2(content_x, current_y),
        TEXT_FONT_SIZE,
        0,
        rl.Color(200, 200, 200, 255)
      )
      current_y += LINE_HEIGHT

  def _render_output(self, rect: rl.Rectangle, content_x: float, content_width: float, start_y: float):
    """Render script output with scrolling"""
    button_area_height = BUTTON_HEIGHT + MARGIN * 2
    output_area_height = rect.height - start_y - button_area_height - rect.y

    # Create output area rect
    output_rect = rl.Rectangle(
      content_x,
      start_y,
      content_width,
      output_area_height
    )

    # Calculate content height
    content_height = len(self._output_lines) * LINE_HEIGHT
    content_rect = rl.Rectangle(0, 0, content_width, content_height)

    # Auto-scroll to bottom when new content arrives
    if content_height > output_area_height:
      self._scroll_panel._offset_filter_y.x = -(content_height - output_area_height)

    # Get scroll offset
    scroll = self._scroll_panel.update(output_rect, content_rect)

    # Draw output with scissor mode
    rl.begin_scissor_mode(
      int(output_rect.x),
      int(output_rect.y),
      int(output_rect.width),
      int(output_rect.height)
    )

    for i, line in enumerate(self._output_lines):
      line_y = output_rect.y + scroll + i * LINE_HEIGHT

      # Skip lines outside visible area
      if line_y + LINE_HEIGHT < output_rect.y or line_y > output_rect.y + output_rect.height:
        continue

      # Color code based on content
      if "[Error" in line or "Error:" in line:
        color = rl.Color(255, 100, 100, 255)  # Red for errors
      elif "[Script completed" in line:
        color = rl.Color(100, 255, 100, 255)  # Green for success
      elif line.startswith("***"):
        color = rl.Color(100, 200, 255, 255)  # Blue for milestones
      else:
        color = rl.WHITE

      rl.draw_text_ex(
        self._font,
        line,
        rl.Vector2(output_rect.x, line_y),
        OUTPUT_FONT_SIZE,
        0,
        color
      )

    rl.end_scissor_mode()

    # Draw status indicator
    status_text = ""
    status_color = rl.WHITE
    if self._state == ScriptState.RUNNING:
      status_text = "Running..."
      status_color = rl.Color(255, 200, 100, 255)
    elif self._state == ScriptState.COMPLETED:
      status_text = "Completed"
      status_color = rl.Color(100, 255, 100, 255)
    elif self._state == ScriptState.ERROR:
      status_text = "Error"
      status_color = rl.Color(255, 100, 100, 255)

    if status_text:
      status_size = measure_text_cached(self._font, status_text, TEXT_FONT_SIZE)
      rl.draw_text_ex(
        self._font,
        status_text,
        rl.Vector2(rect.x + rect.width - MARGIN - status_size.x, start_y - LINE_HEIGHT),
        TEXT_FONT_SIZE,
        0,
        status_color
      )

  def _render_buttons(self, rect: rl.Rectangle):
    """Render action buttons at bottom"""
    button_y = rect.y + rect.height - MARGIN - BUTTON_HEIGHT

    if self._state == ScriptState.READY:
      # Show Start and Exit buttons
      start_rect = rl.Rectangle(
        rect.x + rect.width - MARGIN - BUTTON_WIDTH * 2 - BUTTON_SPACING,
        button_y,
        BUTTON_WIDTH,
        BUTTON_HEIGHT
      )
      exit_rect = rl.Rectangle(
        rect.x + rect.width - MARGIN - BUTTON_WIDTH,
        button_y,
        BUTTON_WIDTH,
        BUTTON_HEIGHT
      )
      self._start_button.render(start_rect)
      self._exit_button.render(exit_rect)
    else:
      # Show only Exit button (disabled while running)
      exit_rect = rl.Rectangle(
        rect.x + rect.width - MARGIN - BUTTON_WIDTH,
        button_y,
        BUTTON_WIDTH,
        BUTTON_HEIGHT
      )
      # Enable exit button only when not running
      self._exit_button.set_enabled(self._state != ScriptState.RUNNING)
      self._exit_button.render(exit_rect)


@dataclass(frozen=True)
class ScriptAction:
  label: str
  script_module: str
  button_style: ButtonStyle = ButtonStyle.NORMAL
  manage_openpilot: bool = False
  accept_epas_risk: bool = False
  allow_stop: bool = False


class ScriptActionRunner(Widget):
  """
  Full-screen runner for a small group of related scripts.

  Only one script can run at a time. Actions that need direct Panda access
  can set NAPScriptRunning while they execute so manager stops conflicting
  processes and resumes them when the script exits.
  """

  def __init__(
      self,
      title: str,
      instructions: str,
      actions: list[ScriptAction],
      on_close: Callable | None = None,
      cwd: str = "/data/openpilot",
  ):
    super().__init__()
    self._title = title
    self._instructions = instructions
    self._actions = actions
    self._on_close = on_close
    self._cwd = cwd
    self._params = Params()

    self._state = ScriptState.READY
    self._active_action: ScriptAction | None = None
    self._output_lines: list[str] = []
    self._output_queue: queue.Queue[str] = queue.Queue()
    self._process: subprocess.Popen | None = None
    self._reader_thread: threading.Thread | None = None

    self._scroll_panel = GuiScrollPanel()
    self._font = gui_app.font(FontWeight.NORMAL)
    self._title_font = gui_app.font(FontWeight.BOLD)
    self._instruction_lines: list[str] = []

    self._action_buttons = [
      Button(
        action.label,
        click_callback=lambda a=action: self._start_action(a),
        button_style=action.button_style,
        font_size=42,
      )
      for action in self._actions
    ]
    self._reboot_button = Button(
      "Reboot",
      click_callback=lambda: self._params.put_bool("DoReboot", True),
      button_style=ButtonStyle.DANGER,
      font_size=42,
    )
    self._stop_button = Button(
      "Stop",
      click_callback=self._on_stop_clicked,
      button_style=ButtonStyle.DANGER,
      font_size=42,
    )
    self._close_button = Button(
      "Close",
      click_callback=self._on_close_clicked,
      button_style=ButtonStyle.TRANSPARENT_WHITE_BORDER,
      font_size=42,
    )

  def _wrap_text(self, text: str, max_width: float, font_size: int) -> list[str]:
    lines = []
    for paragraph in text.split("\n"):
      if not paragraph.strip():
        lines.append("")
        continue

      words = paragraph.split()
      current_line = ""
      for word in words:
        test_line = f"{current_line} {word}".strip()
        text_width = measure_text_cached(self._font, test_line, font_size).x
        if text_width <= max_width:
          current_line = test_line
        else:
          if current_line:
            lines.append(current_line)
          current_line = word

      if current_line:
        lines.append(current_line)

    return lines

  def _start_action(self, action: ScriptAction):
    if self._state == ScriptState.RUNNING:
      return

    self._state = ScriptState.RUNNING
    self._active_action = action
    self._output_lines = [f"Starting {action.label}...", ""]

    try:
      if action.manage_openpilot:
        self._params.put_bool("NAPScriptRunning", True)
      if action.accept_epas_risk:
        self._params.put_bool(EPAS_RISK_ACK_PARAM, True)

      self._process = subprocess.Popen(
        ["python", "-m", action.script_module],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=self._cwd,
        text=True,
        bufsize=1,
      )
      self._reader_thread = threading.Thread(target=self._read_output, args=(action,), daemon=True)
      self._reader_thread.start()
    except Exception as e:
      self._finish_action(action, ScriptState.ERROR, f"\n[Error starting script: {e}]")

  def _read_output(self, action: ScriptAction):
    try:
      if self._process and self._process.stdout:
        for line in iter(self._process.stdout.readline, ''):
          if line:
            self._output_queue.put(line.rstrip())
          if self._process.poll() is not None:
            break

      if self._process:
        return_code = self._process.wait()
        if return_code == 0:
          self._finish_action(action, ScriptState.COMPLETED, "\n[Script completed successfully]")
        else:
          self._finish_action(action, ScriptState.ERROR, f"\n[Script exited with code {return_code}]")
    except Exception as e:
      self._finish_action(action, ScriptState.ERROR, f"\n[Error reading output: {e}]")

  def _finish_action(self, action: ScriptAction, state: int, line: str):
    if action.manage_openpilot:
      self._params.put_bool("NAPScriptRunning", False)
    if action.accept_epas_risk:
      self._params.put_bool(EPAS_RISK_ACK_PARAM, False)
    self._output_queue.put(line)
    self._state = state

  def _on_close_clicked(self):
    if self._state == ScriptState.RUNNING:
      return
    if self._on_close:
      self._on_close()

  def _on_stop_clicked(self):
    if self._state != ScriptState.RUNNING or self._active_action is None:
      return
    if not self._active_action.allow_stop:
      return
    if self._process and self._process.poll() is None:
      self._output_queue.put("\n[Stopping script...]")
      self._process.terminate()

  def _process_output_queue(self):
    while True:
      try:
        self._output_lines.append(self._output_queue.get_nowait())
      except queue.Empty:
        break

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.Color(20, 20, 20, 255))
    self._process_output_queue()

    content_x = rect.x + MARGIN
    content_width = rect.width - MARGIN * 2
    current_y = rect.y + MARGIN

    rl.draw_text_ex(
      self._title_font,
      self._title,
      rl.Vector2(content_x, current_y),
      TITLE_FONT_SIZE,
      0,
      rl.WHITE,
    )
    title_size = measure_text_cached(self._title_font, self._title, TITLE_FONT_SIZE)
    current_y += title_size.y + MARGIN

    rl.draw_line_ex(
      rl.Vector2(content_x, current_y),
      rl.Vector2(content_x + content_width, current_y),
      2,
      rl.Color(80, 80, 80, 255),
    )
    current_y += MARGIN

    if self._state == ScriptState.READY and not self._output_lines:
      self._render_instructions(rect, content_x, content_width, current_y)
    else:
      self._render_output(rect, content_x, content_width, current_y)

    self._render_buttons(rect)
    return None

  def _render_instructions(self, rect: rl.Rectangle, content_x: float, content_width: float, start_y: float):
    if not self._instruction_lines:
      self._instruction_lines = self._wrap_text(self._instructions, content_width, TEXT_FONT_SIZE)

    button_area_height = BUTTON_HEIGHT + MARGIN * 2
    text_area_height = rect.height - start_y - button_area_height - rect.y

    current_y = start_y
    for line in self._instruction_lines:
      if current_y + LINE_HEIGHT > start_y + text_area_height:
        break
      rl.draw_text_ex(
        self._font,
        line,
        rl.Vector2(content_x, current_y),
        TEXT_FONT_SIZE,
        0,
        rl.Color(200, 200, 200, 255),
      )
      current_y += LINE_HEIGHT

  def _render_output(self, rect: rl.Rectangle, content_x: float, content_width: float, start_y: float):
    button_area_height = BUTTON_HEIGHT + MARGIN * 2
    output_area_height = rect.height - start_y - button_area_height - rect.y
    output_rect = rl.Rectangle(content_x, start_y, content_width, output_area_height)

    content_height = len(self._output_lines) * LINE_HEIGHT
    content_rect = rl.Rectangle(0, 0, content_width, content_height)
    if content_height > output_area_height:
      self._scroll_panel._offset_filter_y.x = -(content_height - output_area_height)

    scroll = self._scroll_panel.update(output_rect, content_rect)

    rl.begin_scissor_mode(
      int(output_rect.x),
      int(output_rect.y),
      int(output_rect.width),
      int(output_rect.height),
    )

    for i, line in enumerate(self._output_lines):
      line_y = output_rect.y + scroll + i * LINE_HEIGHT
      if line_y + LINE_HEIGHT < output_rect.y or line_y > output_rect.y + output_rect.height:
        continue

      if "[Error" in line or "ERROR:" in line or "Error:" in line:
        color = rl.Color(255, 100, 100, 255)
      elif "[Script completed" in line:
        color = rl.Color(100, 255, 100, 255)
      elif line.startswith(("=", "***")):
        color = rl.Color(100, 200, 255, 255)
      else:
        color = rl.WHITE

      rl.draw_text_ex(
        self._font,
        line,
        rl.Vector2(output_rect.x, line_y),
        OUTPUT_FONT_SIZE,
        0,
        color,
      )

    rl.end_scissor_mode()
    self._render_status(rect, start_y)

  def _render_status(self, rect: rl.Rectangle, start_y: float):
    status_text = ""
    status_color = rl.WHITE
    if self._state == ScriptState.RUNNING:
      action = self._active_action.label if self._active_action else "script"
      status_text = f"Running {action}..."
      status_color = rl.Color(255, 200, 100, 255)
    elif self._state == ScriptState.COMPLETED:
      status_text = "Completed"
      status_color = rl.Color(100, 255, 100, 255)
    elif self._state == ScriptState.ERROR:
      status_text = "Error"
      status_color = rl.Color(255, 100, 100, 255)

    if not status_text:
      return

    status_size = measure_text_cached(self._font, status_text, TEXT_FONT_SIZE)
    rl.draw_text_ex(
      self._font,
      status_text,
      rl.Vector2(rect.x + rect.width - MARGIN - status_size.x, start_y - LINE_HEIGHT),
      TEXT_FONT_SIZE,
      0,
      status_color,
    )

  def _render_buttons(self, rect: rl.Rectangle):
    button_y = rect.y + rect.height - MARGIN - BUTTON_HEIGHT
    actions_enabled = self._state != ScriptState.RUNNING

    x = rect.x + MARGIN
    for button in self._action_buttons:
      button.set_enabled(actions_enabled)
      button.render(rl.Rectangle(x, button_y, GROUPED_BUTTON_WIDTH, BUTTON_HEIGHT))
      x += GROUPED_BUTTON_WIDTH + BUTTON_SPACING

    close_rect = rl.Rectangle(
      rect.x + rect.width - MARGIN - GROUPED_BUTTON_WIDTH,
      button_y,
      GROUPED_BUTTON_WIDTH,
      BUTTON_HEIGHT,
    )
    reboot_rect = rl.Rectangle(
      close_rect.x - GROUPED_BUTTON_WIDTH - BUTTON_SPACING,
      button_y,
      GROUPED_BUTTON_WIDTH,
      BUTTON_HEIGHT,
    )

    can_stop = self._state == ScriptState.RUNNING and self._active_action is not None and self._active_action.allow_stop
    if can_stop:
      self._stop_button.set_enabled(True)
      self._stop_button.render(reboot_rect)
    else:
      self._reboot_button.set_enabled(actions_enabled)
      self._reboot_button.render(reboot_rect)

    self._close_button.set_enabled(actions_enabled)
    self._close_button.render(close_rect)
