from robot_modbus_lite.qt_gui import RobotQtWindow
from robot_modbus_lite.runtime_paths import resolve_runtime_data_file, resource_dir
from PySide6.QtWidgets import QApplication
from robot_modbus_lite.gui_ui_mixin import GuiUiMixin
from robot_modbus_lite.system_config import AxisRangeConfig


class DummyGui(GuiUiMixin):
    pass


def test_engineer_system_config_tab_is_scrollable():
    app = QApplication.instance() or QApplication([])
    json_path = resolve_runtime_data_file("query_table.json")
    system_config_path = resolve_runtime_data_file("system_config.json")
    csv_path = resource_dir() / "附件" / "机械臂AI地址表.csv"
    if not csv_path.exists():
        csv_path = json_path
    window = RobotQtWindow(json_path=json_path, csv_path=csv_path, system_config_path=system_config_path)

    right_tabs = window.engineer_right_tabs
    index = next(i for i in range(right_tabs.count()) if right_tabs.tabText(i) == "系统参数")
    widget = right_tabs.widget(index)

    assert widget.objectName() == "systemConfigScroll"
    assert widget.widget().objectName() == "subPanel"
    window.close()
    app.processEvents()


def test_ui_scale_auto_uses_screen_constraints():
    dummy = DummyGui()
    narrow = dummy._calculate_ui_scale("auto", available_width=1706, available_height=680)
    wide = dummy._calculate_ui_scale("auto", available_width=2560, available_height=1440)
    assert 0.6 <= narrow <= 1.0
    assert narrow < 1.0
    assert wide >= narrow
    assert wide <= 1.0


def test_ui_scale_numeric_is_clamped():
    dummy = DummyGui()
    assert dummy._calculate_ui_scale(0.5, available_width=9999, available_height=9999) == 0.6
    assert dummy._calculate_ui_scale(1.5, available_width=9999, available_height=9999) == 1.2


def test_target_window_size_does_not_exceed_available_area():
    dummy = DummyGui()
    dummy._ui_scale_factor = 0.87
    assert dummy._target_window_size(1380, 860, available_width=1706, available_height=680) == (1201, 680)


def test_scaled_min_width_preserves_button_readability():
    dummy = DummyGui()
    dummy._ui_scale_factor = 0.6
    assert dummy._scaled_min(100, 80) == 80
    dummy._ui_scale_factor = 0.9
    assert dummy._scaled_min(120, 80) == 108


def test_engineer_fixed_side_panels_use_ui_scale():
    app = QApplication.instance() or QApplication([])
    json_path = resolve_runtime_data_file("query_table.json")
    csv_path = resource_dir() / "附件" / "机械臂AI地址表.csv"
    if not csv_path.exists():
        csv_path = json_path
    window = RobotQtWindow(json_path=json_path, csv_path=csv_path, system_config_path=resolve_runtime_data_file("system_config.json"))
    window._ui_scale_factor = 0.8

    nav = window._build_nav()
    right_panel = window._build_system_panel()

    assert nav.minimumWidth() == 77
    assert nav.maximumWidth() == 77
    assert right_panel.minimumWidth() == 120
    assert right_panel.maximumWidth() == 120
    window.close()
    app.processEvents()


def test_engineer_login_resize_uses_available_screen_size():
    app = QApplication.instance() or QApplication([])
    json_path = resolve_runtime_data_file("query_table.json")
    csv_path = resource_dir() / "附件" / "机械臂AI地址表.csv"
    if not csv_path.exists():
        csv_path = json_path
    window = RobotQtWindow(json_path=json_path, csv_path=csv_path, system_config_path=resolve_runtime_data_file("system_config.json"))
    window.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), ui_scale="auto")
    window._screen_available_size = lambda: (1706, 680)
    window._set_login_role("engineer")
    window.login_host_edit.setText("127.0.0.1")
    window.login_pin_edit.setText("0000")

    window._authenticate_login()

    assert window.width() <= 1706
    assert window.height() <= 680
    assert round(window._ui_scale_factor, 2) == 0.87
    window.close()
    app.processEvents()


def test_engineer_workspace_minimum_size_fits_scaled_screen():
    app = QApplication.instance() or QApplication([])
    json_path = resolve_runtime_data_file("query_table.json")
    csv_path = resource_dir() / "附件" / "机械臂AI地址表.csv"
    if not csv_path.exists():
        csv_path = json_path
    window = RobotQtWindow(json_path=json_path, csv_path=csv_path, system_config_path=resolve_runtime_data_file("system_config.json"))
    window.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), ui_scale="auto")
    window._screen_available_size = lambda: (1706, 680)
    window._set_login_role("engineer")
    window.login_host_edit.setText("127.0.0.1")
    window.login_pin_edit.setText("0000")

    window._authenticate_login()

    minimum = window.minimumSizeHint()
    assert minimum.width() <= 1706
    assert minimum.height() <= 680
    window.close()
    app.processEvents()


def test_operator_compact_window_uses_scaled_screen_size():
    app = QApplication.instance() or QApplication([])
    json_path = resolve_runtime_data_file("query_table.json")
    csv_path = resource_dir() / "附件" / "机械臂AI地址表.csv"
    if not csv_path.exists():
        csv_path = json_path
    window = RobotQtWindow(json_path=json_path, csv_path=csv_path, system_config_path=resolve_runtime_data_file("system_config.json"))
    window.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), ui_scale="auto")
    window._screen_available_size = lambda: (500, 600)
    window._set_login_role("operator")
    window.login_host_edit.setText("127.0.0.1")
    window.login_pin_edit.setText("1234")
    window._authenticate_login()

    window._operator_toggle_compact()

    assert window.width() <= 500
    assert window.height() <= 600
    window.close()
    app.processEvents()
