import os
import subprocess
import pyray as rl
from openpilot.common.params import Params
from openpilot.common.basedir import BASEDIR
from openpilot.system.ui.widgets import Widget, DialogResult
from openpilot.system.ui.widgets.script_runner import ScriptAction, ScriptActionRunner
from openpilot.system.ui.widgets.button import ButtonStyle
from openpilot.system.ui.widgets.keyboard import Keyboard
from openpilot.system.ui.widgets.list_view import (
  toggle_item, multiple_button_item, button_item, text_item,
  ITEM_PADDING,
)
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets.html_render import HtmlRenderer, ElementType
from openpilot.selfdrive.ui.layouts.settings.nap_content import (
  BRAKE_FACTOR_PRESETS, CALIBRATE_PEDAL_INSTRUCTIONS,
  PEDAL_CAN_BUS_VALUES, RADAR_OFFSET_MAX, RADAR_OFFSET_MIN,
  STARTUP_DIAGNOSTICS_INSTRUCTIONS, acknowledgments_html, find_preset_index,
)
from opendbc.car.tesla.preap.nap_params import NAPParamKeys, DEFAULTS
from openpilot.selfdrive.ui.ui_state import ui_state


class SectionHeader(Widget):
  """Lightweight section label to visually separate groups of settings."""

  HEADER_HEIGHT = 82

  def __init__(self, title: str):
    super().__init__()
    self._title = title
    self._font = gui_app.font(FontWeight.BOLD)
    self.set_rect(rl.Rectangle(0, 0, 0, self.HEADER_HEIGHT))

  def set_parent_rect(self, parent_rect: rl.Rectangle):
    super().set_parent_rect(parent_rect)
    self._rect.width = parent_rect.width

  def _render(self, rect):
    text_size = measure_text_cached(self._font, self._title, 42)
    text_y = self._rect.y + (self._rect.height - text_size.y) / 2
    text_x = self._rect.x + ITEM_PADDING
    rl.draw_text_ex(
      self._font, self._title,
      rl.Vector2(text_x, text_y),
      42, 0, rl.Color(190, 190, 190, 255),
    )
    line_x = text_x + text_size.x + 30
    line_y = self._rect.y + self._rect.height / 2
    rl.draw_line_ex(
      rl.Vector2(line_x, line_y),
      rl.Vector2(self._rect.x + self._rect.width - ITEM_PADDING, line_y),
      2,
      rl.Color(92, 92, 92, 255),
    )


class CreditsBlock(Widget):
  """Static block of HTML-rendered credits text with auto-sized height."""

  def __init__(self, html: str):
    super().__init__()
    self._html = HtmlRenderer(
      text=html,
      text_size={ElementType.P: 40},
      text_color=rl.Color(140, 140, 140, 255),
    )
    self.set_rect(rl.Rectangle(0, 0, 0, 200))  # placeholder height

  def set_parent_rect(self, parent_rect: rl.Rectangle):
    super().set_parent_rect(parent_rect)
    self._rect.width = parent_rect.width
    content_w = int(self._rect.width - ITEM_PADDING * 2)
    self._rect.height = self._html.get_total_height(content_w) + ITEM_PADDING

  def _render(self, rect):
    content_w = int(self._rect.width - ITEM_PADDING * 2)
    h = self._html.get_total_height(content_w)
    html_rect = rl.Rectangle(
      self._rect.x + ITEM_PADDING, self._rect.y,
      content_w, h,
    )
    self._html.set_rect(html_rect)
    self._html.render(html_rect)


def section_header_item(title: str) -> SectionHeader:
  return SectionHeader(title)


class NAPLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._build_items()
    self._scroller = Scroller(self._all_items, line_separator=True, spacing=0)

  def _build_items(self):
    """Build all list items organized into sections."""
    self._all_items = []
    self._toggle_map = {}  # param_key -> ListItem (for refresh)

    # ── Section 1: Longitudinal Control ──
    self._all_items.append(section_header_item("Longitudinal Control"))

    self._add_toggle(
      NAPParamKeys.PEDAL_ENABLED,
      "Pedal Interceptor",
      "Enable Comma Pedal hardware for direct throttle control. Requires reboot.",
      enabled=ui_state.is_offroad,
      needs_reboot=True,
    )

    self._add_toggle(
      NAPParamKeys.ADAPTIVE_ACCEL,
      "Adaptive Accel Limits",
      "Reduces acceleration authority when close to a lead car to prevent overshoot. Full accel on open road or when closing a large gap.",
    )

    follow_dist = self._params.get(NAPParamKeys.FOLLOW_DISTANCE, return_default=True)
    self._follow_buttons = multiple_button_item(
      "Follow Distance",
      "Follow distance (1=closest, 7=farthest). Overridden by cruise stalk if present.",
      buttons=["1", "2", "3", "4", "5", "6", "7"],
      button_width=80,
      selected_index=max(0, min(6, follow_dist - 1)),
      callback=self._on_follow_distance,
    )
    self._all_items.append(self._follow_buttons)

    # ── Section 2: Pedal Hardware ──
    self._all_items.append(section_header_item("Pedal Hardware"))


    pedal_bus = self._params.get(NAPParamKeys.PEDAL_CAN_BUS, return_default=True)
    self._pedal_bus_buttons = multiple_button_item(
      "Pedal CAN Bus",
      "Select which CAN bus the Comma Pedal is connected to. Requires reboot.",
      buttons=["Bus 0", "Bus 2"],
      button_width=150,
      selected_index=0 if pedal_bus == 0 else 1,
      callback=self._on_pedal_can_bus,
    )
    self._pedal_bus_buttons.action_item.set_enabled(ui_state.is_offroad)
    self._all_items.append(self._pedal_bus_buttons)

    self._pedal_calib_status = text_item(
      "Pedal Calibration",
      lambda: "Calibrated" if self._params.get_bool(NAPParamKeys.PEDAL_CALIB_DONE) else "Not Calibrated",
      description="Shows whether the pedal interceptor has been calibrated.",
    )
    self._all_items.append(self._pedal_calib_status)

    self._calibrate_pedal_btn = button_item(
      "Calibrate Pedal",
      "Start",
      description="Run the pedal calibration routine. Vehicle must be stationary with ignition on.",
      callback=self._on_calibrate_pedal,
    )
    self._calibrate_pedal_btn.action_item.set_enabled(ui_state.is_offroad)
    self._all_items.append(self._calibrate_pedal_btn)

    # ── Section 3: Radar ──
    self._all_items.append(section_header_item("Radar"))

    self._add_toggle(
      NAPParamKeys.RADAR_ENABLED,
      "Radar Enabled",
      "Enable the stock Bosch radar for lead car detection. Requires reboot.",
      enabled=ui_state.is_offroad,
      needs_reboot=True,
    )

    self._add_toggle(
      NAPParamKeys.RADAR_BEHIND_NOSECONE,
      "Radar Behind Nosecone",
      "Apply signal attenuation adjustment for radar mounted behind the nosecone. Requires reboot.",
      enabled=ui_state.is_offroad,
      needs_reboot=True,
    )

    self._radar_offset_keyboard = Keyboard(max_text_size=10)
    self._radar_offset_btn = button_item(
      "Radar Lateral Offset",
      self._get_radar_offset_text,
      description=(
        "Lateral offset in meters added to radar yRel. Negative shifts leads toward the left of current radar "
        + "reading; positive shifts right. Example: -0.27 for the 3D-printed factory-location mount."
      ),
      callback=self._on_radar_offset_click,
    )
    self._all_items.append(self._radar_offset_btn)

    self._radar_tools_btn = button_item(
      "Radar Tools",
      "Open",
      description=(
        "Open live radar calibration and test views. These read openpilot's live radar output "
        + "without stopping manager, pandad, card, or controlsd."
      ),
      callback=self._on_radar_tools,
    )
    self._all_items.append(self._radar_tools_btn)

    # ── Section 4: iBooster / Braking (not yet implemented — grayed out) ──
    self._all_items.append(section_header_item("iBooster / Braking"))

    self._add_toggle(
      NAPParamKeys.IBOOSTER_ENABLED,
      "iBooster Enabled",
      "Enable the iBooster brake-by-wire system for electronic braking. (Not yet implemented)",
      enabled=False,
    )

    brake_factor = self._params.get(NAPParamKeys.BRAKE_FACTOR, return_default=True)
    self._brake_factor_buttons = multiple_button_item(
      "Brake Factor",
      "Multiplier for brake force. Higher values brake more aggressively. (Not yet implemented)",
      buttons=["0.5x", "1.0x", "1.5x", "2.0x"],
      button_width=130,
      selected_index=find_preset_index(BRAKE_FACTOR_PRESETS, brake_factor),
      callback=self._on_brake_factor,
    )
    self._brake_factor_buttons.action_item.set_enabled(False)
    self._all_items.append(self._brake_factor_buttons)

    # ── Section 5: Advanced ──
    self._all_items.append(section_header_item("Advanced"))

    # Force Pre-AP is always on for now — grayed out in the ON position
    self._params.put_bool(NAPParamKeys.FORCE_PRE_AP, True)
    self._add_toggle(
      NAPParamKeys.FORCE_PRE_AP,
      "Force Pre-AP Mode",
      "Force the system to treat this vehicle as a Pre-Autopilot Tesla.",
      enabled=False,
    )

    # ── Section 6: Actions ──
    self._all_items.append(section_header_item("Actions"))

    self._startup_diagnostics_btn = button_item(
      "Startup Diagnostics",
      "Run",
      description="Collect startup/onroad diagnostics without stopping openpilot or rebooting.",
      callback=self._on_startup_diagnostics,
    )
    self._startup_diagnostics_btn.action_item.set_enabled(ui_state.is_offroad)
    self._all_items.append(self._startup_diagnostics_btn)

    self._epas_firmware_btn = button_item(
      "EPAS Firmware",
      "Open",
      description=(
        "Open EPAS firmware tools. Backup, flash, and restore run in one screen "
        + "and return here when finished; reboot is available as a fallback."
      ),
      callback=self._on_epas_firmware,
    )
    self._all_items.append(self._epas_firmware_btn)

    self._emergency_disable_btn = button_item(
      "Emergency Disable",
      "Disable",
      description="Immediately disable pedal interceptor and clear calibration. Restart required.",
      callback=self._on_emergency_disable,
    )
    self._all_items.append(self._emergency_disable_btn)

    self._reset_defaults_btn = button_item(
      "Reset to Defaults",
      "Reset",
      description="Reset all NAP settings to factory defaults. This cannot be undone.",
      callback=self._on_reset_defaults,
    )
    self._reset_defaults_btn.action_item.set_enabled(ui_state.is_offroad)
    self._all_items.append(self._reset_defaults_btn)

    # ── Acknowledgments ──
    self._all_items.append(section_header_item("Acknowledgments"))
    self._all_items.append(CreditsBlock(acknowledgments_html()))

  def _add_toggle(self, param_key: str, title: str, description: str,
                   enabled: bool | None = None, needs_reboot: bool = False):
    """Helper to add a toggle item and register it for state refresh."""
    kwargs = {}
    if enabled is not None:
      kwargs['enabled'] = enabled

    def on_toggle(state, k=param_key):
      self._params.put_bool(k, state)
      if needs_reboot:
        self._show_reboot_modal()

    item = toggle_item(
      title,
      description=description,
      initial_state=self._params.get_bool(param_key),
      callback=on_toggle,
      **kwargs,
    )
    self._toggle_map[param_key] = item
    self._all_items.append(item)

  # ── Multiple-button callbacks ──

  def _on_follow_distance(self, index: int):
    self._params.put(NAPParamKeys.FOLLOW_DISTANCE, index + 1)

  def _on_pedal_can_bus(self, index: int):
    self._params.put(NAPParamKeys.PEDAL_CAN_BUS, PEDAL_CAN_BUS_VALUES[index])
    self._show_reboot_modal()

  def _on_brake_factor(self, index: int):
    self._params.put(NAPParamKeys.BRAKE_FACTOR, BRAKE_FACTOR_PRESETS[index])

  def _get_radar_offset(self) -> float:
    raw = self._params.get(NAPParamKeys.RADAR_OFFSET, return_default=True)
    try:
      return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
      return 0.0

  def _get_radar_offset_text(self) -> str:
    return f"{self._get_radar_offset():+.2f}m"

  def _on_radar_offset_click(self):
    self._radar_offset_keyboard.reset(min_text_size=1)
    self._radar_offset_keyboard.set_title("Radar Lateral Offset (m)")
    self._radar_offset_keyboard.set_text(f"{self._get_radar_offset():.2f}")
    self._radar_offset_keyboard.set_callback(self._on_radar_offset_submit)
    gui_app.push_widget(self._radar_offset_keyboard)

  def _on_radar_offset_submit(self, result: DialogResult):
    if result != DialogResult.CONFIRM:
      return
    try:
      text = (self._radar_offset_keyboard.text or "").strip()
      value = float(text)
    except (TypeError, ValueError, AttributeError):
      return  # silently reject blank/non-numeric input; label stays at prior value
    value = max(RADAR_OFFSET_MIN, min(RADAR_OFFSET_MAX, value))
    # Pass raw float (matches the BRAKE_FACTOR put pattern). Params
    # previously got str(value) which threw inside Params for FLOAT-typed
    # keys; exception propagated out of the Keyboard Enter callback,
    # crashing the UI and leaving the Params value unset (label stuck at
    # the 0.0 default). Keep the try/except so no future value ever kills
    # the UI.
    try:
      self._params.put(NAPParamKeys.RADAR_OFFSET, value)
    except Exception:
      pass

  # ── Script runner ──

  def _show_script_runner(self, title: str, instructions: str, script_module: str):
    """Launch the script runner as a separate process that takes over the screen."""
    script_path = os.path.join(BASEDIR, "scripts", "nap", "run_script.py")
    log_path = "/tmp/nap_script_runner.log"

    with open(log_path, "w") as log_file:
      subprocess.Popen(
        ["python", script_path, title, script_module, instructions],
        cwd=BASEDIR,
        start_new_session=True,
        stdout=log_file,
        stderr=log_file,
      )

  # ── Action button callbacks ──

  def _on_startup_diagnostics(self):
    # Read-only: no manage_openpilot, so manager/pandad/card keep running while
    # this samples live messages and CAN. Summary shows the quick state; Logs
    # shows the same findings plus the recent error logs to capture the reason.
    gui_app.push_widget(ScriptActionRunner(
      title="Startup Diagnostics",
      instructions=STARTUP_DIAGNOSTICS_INSTRUCTIONS,
      actions=[
        ScriptAction("Summary", "scripts.nap.startup_diagnostics", allow_stop=True),
        ScriptAction("Logs", "scripts.nap.startup_diagnostics", args=("--logs",), allow_stop=True),
      ],
      on_close=gui_app.pop_widget,
      cwd=BASEDIR,
    ))

  def _on_calibrate_pedal(self):
    self._show_script_runner(
      title="Pedal Calibration",
      instructions=CALIBRATE_PEDAL_INSTRUCTIONS,
      script_module="scripts.nap.calibrate_pedal",
    )

  def _on_radar_tools(self):
    instructions = "\n\n".join((
      "These tools read openpilot's live radar track output and do not take direct control of the Panda.",
      "Calibration shows only tracks in the target window near the front centerline. Test shows all live tracks.",
      (
        "Because openpilot keeps running, this can be used during development with the car powered on. "
        + "Do not let the driver watch or operate this while driving."
      ),
    ))
    gui_app.push_widget(ScriptActionRunner(
      title="Radar Tools",
      instructions=instructions,
      actions=[
        ScriptAction("Calibrate", "scripts.nap.live_radar_calibrate", allow_stop=True),
        ScriptAction("Test", "scripts.nap.live_radar_test", allow_stop=True),
      ],
      on_close=gui_app.pop_widget,
      cwd=BASEDIR,
    ))

  def _on_epas_firmware(self):
    instructions = "\n\n".join((
      "Use this only while parked with the car powered on and stable 12V power available.",
      "Backup extracts the current EPAS firmware and does not write to the ECU. Flash patches "
      + "the EPAS firmware for steering control. Restore writes the saved stock firmware image "
      + "back to the EPAS.",
      "Run Backup before Flash or Restore. While a tool runs, openpilot releases the Panda so "
      + "the script can talk to the EPAS directly. When the tool exits, openpilot resumes without "
      + "requiring a device reboot. Use Reboot only if the vehicle or device does not recover cleanly.",
    ))
    gui_app.push_widget(ScriptActionRunner(
      title="EPAS Firmware",
      instructions=instructions,
      actions=[
        ScriptAction("Backup", "scripts.nap.extract_epas", manage_openpilot=True),
        ScriptAction(
          "Flash",
          "scripts.nap.flash_epas",
          button_style=ButtonStyle.DANGER,
          manage_openpilot=True,
          accept_epas_risk=True,
        ),
        ScriptAction(
          "Restore",
          "scripts.nap.restore_epas",
          button_style=ButtonStyle.DANGER,
          manage_openpilot=True,
          accept_epas_risk=True,
        ),
      ],
      on_close=gui_app.pop_widget,
      cwd=BASEDIR,
    ))

  def _show_reboot_modal(self):
    """Show a modal prompting the user to reboot for the change to take effect."""
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        self._params.put_bool("DoReboot", True)

    content = "<h1>Reboot Required</h1><br><p>This change requires a reboot to take effect.</p>"
    dlg = ConfirmDialog(content, "Reboot", cancel_text="Ignore", rich=True, callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_emergency_disable(self):
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        self._params.put_bool(NAPParamKeys.PEDAL_ENABLED, False)
        self._params.put_bool(NAPParamKeys.PEDAL_CALIB_DONE, False)
        self._refresh_toggles()

    content = (
      "<h1>Emergency Disable</h1><br>"
      + "<p>This will disable the pedal interceptor and clear calibration. "
      + "You will need to restart the device for changes to take effect.</p>"
    )
    dlg = ConfirmDialog(content, "Disable", rich=True, callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_reset_defaults(self):
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        self._reset_all_to_defaults()
        self._refresh_toggles()

    content = (
      "<h1>Reset to Defaults</h1><br>"
      + "<p>This will reset all NAP settings to their factory default values. "
      + "This action cannot be undone.</p>"
    )
    dlg = ConfirmDialog(content, "Reset All", rich=True, callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _reset_all_to_defaults(self):
    """Write default value for each NAP param."""
    for key, default in DEFAULTS.items():
      if isinstance(default, bool):
        self._params.put_bool(key, default)
      elif isinstance(default, (int, float)):
        self._params.put(key, default)
    # Force Pre-AP is locked on in the panel but DEFAULTS keeps it off
    # for non-UI consumers. Re-apply the lock after the wholesale loop
    # so reset doesn't silently flip the invariant.
    self._params.put_bool(NAPParamKeys.FORCE_PRE_AP, True)

  # ── Render / lifecycle ──

  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    self._scroller.show_event()
    self._refresh_toggles()

  def _refresh_toggles(self):
    """Sync all toggle states from params (handles external changes)."""
    for key, item in self._toggle_map.items():
      item.action_item.set_state(self._params.get_bool(key))

    # Refresh multiple-button selections
    follow_dist = self._params.get(NAPParamKeys.FOLLOW_DISTANCE, return_default=True)
    self._follow_buttons.action_item.set_selected_button(
      max(0, min(6, follow_dist - 1)))

    pedal_bus = self._params.get(NAPParamKeys.PEDAL_CAN_BUS, return_default=True)
    self._pedal_bus_buttons.action_item.set_selected_button(
      0 if pedal_bus == 0 else 1)

    brake_factor = self._params.get(NAPParamKeys.BRAKE_FACTOR, return_default=True)
    self._brake_factor_buttons.action_item.set_selected_button(
      find_preset_index(BRAKE_FACTOR_PRESETS, brake_factor))
