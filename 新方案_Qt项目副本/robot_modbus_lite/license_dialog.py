from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QMessageBox,
    QProgressBar,
)


class ActivateWorker(QThread):
    """激活工作线程"""
    finished = Signal(object)  # LicenseStatus
    error = Signal(str)

    def __init__(self, license_manager, license_code, machine_name):
        super().__init__()
        self.license_manager = license_manager
        self.license_code = license_code
        self.machine_name = machine_name

    def run(self):
        try:
            status = self.license_manager.activate(self.license_code, self.machine_name)
            self.finished.emit(status)
        except Exception as e:
            self.error.emit(str(e))


class DeactivateWorker(QThread):
    """解绑工作线程"""
    finished = Signal(bool, str)

    def __init__(self, license_manager):
        super().__init__()
        self.license_manager = license_manager

    def run(self):
        try:
            success, message = self.license_manager.deactivate()
            self.finished.emit(success, message)
        except Exception as e:
            self.finished.emit(False, str(e))


class LicenseDialog(QDialog):
    """授权管理对话框"""

    license_activated = Signal()
    license_deactivated = Signal()

    def __init__(self, license_manager, parent=None):
        super().__init__(parent)
        self.license_manager = license_manager
        self.setWindowTitle("授权管理")
        self.setMinimumWidth(450)
        self._worker = None
        self._setup_ui()
        self._refresh_status()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 授权状态组
        status_group = QGroupBox("授权状态")
        status_layout = QFormLayout(status_group)

        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-weight: bold;")
        status_layout.addRow("状态:", self.status_label)

        self.type_label = QLabel()
        status_layout.addRow("授权类型:", self.type_label)

        self.expires_label = QLabel()
        status_layout.addRow("到期时间:", self.expires_label)

        self.voice_status_label = QLabel()
        status_layout.addRow("语音功能:", self.voice_status_label)

        self.deepseek_status_label = QLabel()
        status_layout.addRow("DeepSeek:", self.deepseek_status_label)

        layout.addWidget(status_group)

        # 配额使用组
        quota_group = QGroupBox("配额使用")
        quota_layout = QVBoxLayout(quota_group)

        self.voice_quota_label = QLabel()
        quota_layout.addWidget(self.voice_quota_label)

        self.voice_progress = QProgressBar()
        self.voice_progress.setMaximumHeight(16)
        quota_layout.addWidget(self.voice_progress)

        self.deepseek_quota_label = QLabel()
        quota_layout.addWidget(self.deepseek_quota_label)

        self.deepseek_progress = QProgressBar()
        self.deepseek_progress.setMaximumHeight(16)
        quota_layout.addWidget(self.deepseek_progress)

        layout.addWidget(quota_group)

        # 激活授权组
        activate_group = QGroupBox("激活授权")
        activate_layout = QFormLayout(activate_group)

        self.license_input = QLineEdit()
        self.license_input.setPlaceholderText("输入授权码: RMLT-XXXX-XXXX-XXXX-XXXX-XXXX")
        activate_layout.addRow("授权码:", self.license_input)

        self.machine_name_input = QLineEdit()
        self.machine_name_input.setPlaceholderText("(可选) 设备名称")
        activate_layout.addRow("设备名称:", self.machine_name_input)

        btn_layout = QHBoxLayout()

        self.activate_btn = QPushButton("激活")
        self.activate_btn.clicked.connect(self._on_activate)
        btn_layout.addWidget(self.activate_btn)

        self.deactivate_btn = QPushButton("解绑设备")
        self.deactivate_btn.clicked.connect(self._on_deactivate)
        btn_layout.addWidget(self.deactivate_btn)

        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.clicked.connect(self._refresh_status)
        btn_layout.addWidget(self.refresh_btn)

        activate_layout.addRow(btn_layout)
        layout.addWidget(activate_group)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(4)
        self.progress_bar.setRange(0, 0)  # 不确定进度
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 使用模式说明
        mode_group = QGroupBox("使用模式")
        mode_layout = QVBoxLayout(mode_group)

        mode_info = QLabel(
            "<b>订阅模式:</b> 使用授权码，由服务方提供 API 配额<br>"
            "<b>自带 Key:</b> 在 .env 文件配置自己的 API Key"
        )
        mode_info.setWordWrap(True)
        mode_layout.addWidget(mode_info)

        layout.addWidget(mode_group)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _refresh_status(self):
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("刷新中...")

        try:
            status = self.license_manager.check_status(force_online=True)
            self._update_status_display(status)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"获取授权状态失败: {e}")
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("刷新状态")

    def _update_status_display(self, status):
        if status.valid:
            self.status_label.setText("已授权")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.deactivate_btn.setEnabled(True)
        else:
            self.status_label.setText(f"未授权 ({status.message})")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            self.deactivate_btn.setEnabled(False)

        type_names = {
            "trial": "试用版",
            "monthly": "月度订阅",
            "yearly": "年度订阅",
            "lifetime": "永久授权",
        }
        self.type_label.setText(type_names.get(status.license_type, status.license_type))

        if status.expires_at:
            self.expires_label.setText(status.expires_at.strftime("%Y-%m-%d %H:%M"))
        else:
            self.expires_label.setText("永久")

        self.voice_status_label.setText(
            "已启用" if status.voice_enabled else "未启用"
        )
        self.deepseek_status_label.setText(
            "已启用" if status.deepseek_enabled else "未启用"
        )

        if status.voice_daily_quota > 0:
            self.voice_quota_label.setText(
                f"语音: 今日 {status.voice_used_today} / {status.voice_daily_quota} 次"
            )
            self.voice_progress.setMaximum(status.voice_daily_quota)
            self.voice_progress.setValue(status.voice_used_today)
            self.voice_progress.setVisible(True)
        else:
            self.voice_quota_label.setText("语音: 无限制")
            self.voice_progress.setVisible(False)

        if status.deepseek_monthly_quota > 0:
            self.deepseek_quota_label.setText(
                f"DeepSeek: 本月 {status.deepseek_used_this_month} / {status.deepseek_monthly_quota} 次"
            )
            self.deepseek_progress.setMaximum(status.deepseek_monthly_quota)
            self.deepseek_progress.setValue(status.deepseek_used_this_month)
            self.deepseek_progress.setVisible(True)
        else:
            self.deepseek_quota_label.setText("DeepSeek: 无限制")
            self.deepseek_progress.setVisible(False)

    def _on_activate(self):
        code = self.license_input.text().strip()
        if not code:
            QMessageBox.warning(self, "提示", "请输入授权码")
            return

        self._set_ui_busy(True)
        self.progress_bar.setVisible(True)

        self._worker = ActivateWorker(
            self.license_manager,
            code,
            self.machine_name_input.text().strip()
        )
        self._worker.finished.connect(self._on_activate_finished)
        self._worker.error.connect(self._on_activate_error)
        self._worker.start()

    def _on_activate_finished(self, status):
        self._set_ui_busy(False)
        self.progress_bar.setVisible(False)

        if status.valid:
            QMessageBox.information(self, "成功", "授权激活成功！")
            self.license_activated.emit()
            self._update_status_display(status)
        else:
            QMessageBox.warning(self, "激活失败", status.message)

    def _on_activate_error(self, error):
        self._set_ui_busy(False)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "错误", f"激活失败: {error}")

    def _on_deactivate(self):
        reply = QMessageBox.question(
            self, "确认解绑",
            "解绑后当前设备将无法使用订阅功能，确定要解绑吗？",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        self._set_ui_busy(True)
        self.progress_bar.setVisible(True)

        self._worker = DeactivateWorker(self.license_manager)
        self._worker.finished.connect(self._on_deactivate_finished)
        self._worker.start()

    def _on_deactivate_finished(self, success, message):
        self._set_ui_busy(False)
        self.progress_bar.setVisible(False)

        if success:
            QMessageBox.information(self, "成功", message)
            self.license_deactivated.emit()
            self._update_status_display(
                self.license_manager._create_invalid_status("未激活")
            )
        else:
            QMessageBox.warning(self, "解绑失败", message)

    def _set_ui_busy(self, busy: bool):
        self.activate_btn.setEnabled(not busy)
        self.deactivate_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)

        if busy:
            self.activate_btn.setText("处理中...")
        else:
            self.activate_btn.setText("激活")
