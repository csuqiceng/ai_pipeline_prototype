from robot_modbus_lite.qt_gui import RobotQtWindow
from robot_modbus_lite.runtime_paths import resolve_runtime_data_file, resource_dir
from PySide6.QtWidgets import QApplication


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
