from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests


# ---------- AES 缓存加密 ----------

def _encrypt_cache(data: str, key: str) -> str:
    """AES-256-CBC 加密，随机 IV 存密文前 16 字节"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        import base64

        key_bytes = hashlib.sha256(key.encode()).digest()
        iv = os.urandom(16)

        pad_len = 16 - (len(data) % 16)
        padded_data = data + chr(pad_len) * pad_len

        cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded_data.encode()) + encryptor.finalize()

        return base64.b64encode(iv + encrypted).decode()
    except ImportError:
        return data


def _decrypt_cache(encrypted_data: str, key: str) -> str:
    """AES-256-CBC 解密"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        import base64

        key_bytes = hashlib.sha256(key.encode()).digest()

        raw = base64.b64decode(encrypted_data.encode())
        iv = raw[:16]
        encrypted = raw[16:]

        cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted) + decryptor.finalize()

        pad_len = decrypted[-1]
        return decrypted[:-pad_len].decode()
    except ImportError:
        return encrypted_data


# ---------- 数据类 ----------

@dataclass
class LicenseStatus:
    valid: bool
    license_type: str
    voice_enabled: bool
    deepseek_enabled: bool
    voice_daily_quota: int
    deepseek_monthly_quota: int
    voice_used_today: int
    deepseek_used_this_month: int
    expires_at: datetime | None
    offline_grace_until: datetime | None
    message: str = ""


# ---------- LicenseManager ----------

class LicenseManager:
    """
    授权管理器

    职责:
    1. 管理授权码激活/验证
    2. 缓存授权状态 (支持离线)
    3. 获取临时访问 Token
    4. 检查功能权限
    5. 心跳上报
    """

    CACHE_FILE = "license_cache.enc"
    OFFLINE_GRACE_DAYS = 7
    HEARTBEAT_INTERVAL_SEC = 1800  # 30 分钟

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_file = cache_dir / self.CACHE_FILE
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._cached_status: LicenseStatus | None = None
        self._machine_id: str | None = None
        self._heartbeat_enabled = False

        # 从环境变量读取服务器地址
        self.SERVER_URL = os.getenv("LICENSE_SERVER_URL", "http://localhost:8000")

        # 加载缓存
        self._load_cache()

    # ---------- 机器码 ----------

    def get_machine_id(self) -> str:
        """生成机器唯一标识（SHA256: CPU+主板+MAC）"""
        if self._machine_id:
            return self._machine_id

        components = []

        if platform.system() == "Windows":
            cpu_id = self._get_windows_cpu_id()
            if cpu_id:
                components.append(f"CPU:{cpu_id}")
            board_sn = self._get_windows_board_sn()
            if board_sn:
                components.append(f"BOARD:{board_sn}")
        elif platform.system() == "Linux":
            try:
                cpu_info = Path("/proc/cpuinfo").read_text()
                for line in cpu_info.split("\n"):
                    if "Serial" in line or "processor" in line:
                        components.append(line)
                        break
            except Exception:
                pass

            try:
                board_info = subprocess.run(
                    ["cat", "/sys/class/dmi/id/board_serial"],
                    capture_output=True, text=True, timeout=2
                )
                if board_info.returncode == 0:
                    components.append(f"BOARD:{board_info.stdout.strip()}")
            except Exception:
                pass

        mac = hex(uuid.getnode())[2:]
        components.append(f"MAC:{mac}")

        fingerprint = "|".join(components)
        self._machine_id = "SHA256:" + hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
        return self._machine_id

    def _get_windows_cpu_id(self) -> str:
        """获取 Windows CPU ID（三级降级）"""
        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "ProcessorId"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip().split("\n")[-1].strip()
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty ProcessorId"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "Identifier")[0]
        except Exception:
            pass

        return ""

    def _get_windows_board_sn(self) -> str:
        """获取 Windows 主板序列号（两级降级）"""
        try:
            result = subprocess.run(
                ["wmic", "baseboard", "get", "SerialNumber"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip().split("\n")[-1].strip()
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-CimInstance Win32_BaseBoard | Select-Object -ExpandProperty SerialNumber"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

        return ""

    # ---------- 激活/解绑 ----------

    def activate(self, license_code: str, machine_name: str = "") -> LicenseStatus:
        """激活授权码"""
        machine_id = self.get_machine_id()

        try:
            response = requests.post(
                f"{self.SERVER_URL}/api/v1/auth/activate",
                json={
                    "license_code": license_code,
                    "machine_id": machine_id,
                    "machine_name": machine_name or platform.node(),
                    "app_version": "1.0.0"
                },
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()["data"]
                self._access_token = data["access_token"]
                self._token_expires_at = datetime.utcnow() + timedelta(seconds=data["token_expires_in"])

                status = self._parse_license_response(data)
                self._save_cache(status, license_code)

                self._start_heartbeat()
                return status
            else:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                detail = error_data.get("detail", {})
                message = detail.get("message", error_data.get("message", "激活失败")) if isinstance(detail, dict) else str(detail)
                return self._create_invalid_status(message)
        except requests.RequestException as e:
            return self._create_invalid_status(f"网络错误: {e}")

    def deactivate(self) -> tuple[bool, str]:
        """解绑设备"""
        if not self._access_token:
            return False, "未激活"

        try:
            response = requests.post(
                f"{self.SERVER_URL}/api/v1/auth/deactivate",
                headers={"Authorization": f"Bearer {self._access_token}"},
                json={"machine_id": self.get_machine_id()},
                timeout=10
            )

            if response.status_code == 200:
                self._clear_cache()
                self._access_token = None
                self._token_expires_at = None
                self._cached_status = None
                self._stop_heartbeat()
                return True, "解绑成功"
            else:
                error = response.json().get("message", "解绑失败")
                return False, error
        except requests.RequestException as e:
            return False, f"网络错误: {e}"

    # ---------- 状态检查 ----------

    def check_status(self, force_online: bool = False) -> LicenseStatus:
        """检查授权状态"""
        if force_online or self._should_refresh_token():
            return self._online_check()

        if self._cached_status and self._cached_status.valid:
            if self._is_offline_grace_valid():
                return self._cached_status

        return self._online_check()

    def can_use_voice(self) -> tuple[bool, str]:
        """检查是否可以使用语音功能"""
        status = self.check_status()
        if not status.valid:
            return False, status.message
        if not status.voice_enabled:
            return False, "当前授权未启用语音功能"
        if status.voice_daily_quota > 0 and status.voice_used_today >= status.voice_daily_quota:
            return False, "今日语音配额已用尽"
        return True, ""

    def can_use_deepseek(self) -> tuple[bool, str]:
        """检查是否可以使用 DeepSeek"""
        status = self.check_status()
        if not status.valid:
            return False, status.message
        if not status.deepseek_enabled:
            return False, "当前授权未启用 DeepSeek 功能"
        if status.deepseek_monthly_quota > 0 and status.deepseek_used_this_month >= status.deepseek_monthly_quota:
            return False, "本月 DeepSeek 配额已用尽"
        return True, ""

    def get_access_token(self) -> str | None:
        """获取有效的访问 Token"""
        if self._access_token and self._token_expires_at:
            if datetime.utcnow() < self._token_expires_at:
                return self._access_token

        self._online_check()
        return self._access_token

    # ---------- 心跳 ----------

    def _start_heartbeat(self):
        """启动心跳线程"""
        import threading

        self._heartbeat_enabled = True

        def heartbeat_loop():
            import time
            while self._heartbeat_enabled:
                try:
                    self._send_heartbeat()
                except Exception:
                    pass

                for _ in range(self.HEARTBEAT_INTERVAL_SEC):
                    if not self._heartbeat_enabled:
                        break
                    time.sleep(1)

        thread = threading.Thread(target=heartbeat_loop, daemon=True)
        thread.start()

    def _stop_heartbeat(self):
        self._heartbeat_enabled = False

    def _send_heartbeat(self):
        if not self._access_token:
            return

        try:
            response = requests.post(
                f"{self.SERVER_URL}/api/v1/auth/heartbeat",
                headers={"Authorization": f"Bearer {self._access_token}"},
                json={
                    "machine_id": self.get_machine_id(),
                    "app_version": "1.0.0"
                },
                timeout=5
            )

            if response.status_code == 200:
                data = response.json()["data"]
                if data.get("quota") and self._cached_status:
                    self._cached_status.voice_used_today = data["quota"].get("voice_used_today", 0)
                    self._cached_status.deepseek_used_this_month = data["quota"].get("deepseek_used_this_month", 0)
        except requests.RequestException:
            pass

    # ---------- 内部方法 ----------

    def _should_refresh_token(self) -> bool:
        if not self._access_token or not self._token_expires_at:
            return True
        return datetime.utcnow() > self._token_expires_at - timedelta(hours=1)

    def _is_offline_grace_valid(self) -> bool:
        if not self._cached_status or not self._cached_status.offline_grace_until:
            return False
        return datetime.utcnow() < self._cached_status.offline_grace_until

    def _online_check(self) -> LicenseStatus:
        if not self._access_token:
            cached = self._load_cache()
            if cached:
                return cached
            return self._create_invalid_status("未激活")

        try:
            response = requests.get(
                f"{self.SERVER_URL}/api/v1/auth/status",
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()["data"]
                status = self._parse_license_response(data)
                self._cached_status = status
                self._save_cache(status)
                return status
            else:
                return self._create_invalid_status("授权验证失败")
        except requests.RequestException:
            if self._cached_status and self._is_offline_grace_valid():
                return self._cached_status
            return self._create_invalid_status("网络不可用")

    def _save_cache(self, status: LicenseStatus, license_code: str = ""):
        cache_data = {
            "status": {
                "valid": status.valid,
                "license_type": status.license_type,
                "voice_enabled": status.voice_enabled,
                "deepseek_enabled": status.deepseek_enabled,
                "voice_daily_quota": status.voice_daily_quota,
                "deepseek_monthly_quota": status.deepseek_monthly_quota,
                "voice_used_today": status.voice_used_today,
                "deepseek_used_this_month": status.deepseek_used_this_month,
                "expires_at": status.expires_at.isoformat() if status.expires_at else None,
                "offline_grace_until": (datetime.utcnow() + timedelta(days=self.OFFLINE_GRACE_DAYS)).isoformat(),
            },
            "license_code": license_code,
            "cached_at": datetime.utcnow().isoformat(),
            "machine_id": self.get_machine_id(),
            "access_token": self._access_token,
            "token_expires_at": self._token_expires_at.isoformat() if self._token_expires_at else None,
        }

        json_data = json.dumps(cache_data, ensure_ascii=False, indent=2)
        encrypted = _encrypt_cache(json_data, self.get_machine_id())
        self.cache_file.write_text(encrypted, encoding="utf-8")

    def _load_cache(self) -> LicenseStatus | None:
        if not self.cache_file.exists():
            return None
        try:
            encrypted = self.cache_file.read_text(encoding="utf-8")
            json_data = _decrypt_cache(encrypted, self.get_machine_id())
            data = json.loads(json_data)

            if data.get("machine_id") != self.get_machine_id():
                return None

            status_data = data["status"]
            self._cached_status = LicenseStatus(
                valid=status_data["valid"],
                license_type=status_data["license_type"],
                voice_enabled=status_data["voice_enabled"],
                deepseek_enabled=status_data["deepseek_enabled"],
                voice_daily_quota=status_data["voice_daily_quota"],
                deepseek_monthly_quota=status_data["deepseek_monthly_quota"],
                voice_used_today=status_data["voice_used_today"],
                deepseek_used_this_month=status_data["deepseek_used_this_month"],
                expires_at=datetime.fromisoformat(status_data["expires_at"]) if status_data.get("expires_at") else None,
                offline_grace_until=datetime.fromisoformat(status_data["offline_grace_until"]) if status_data.get("offline_grace_until") else None,
            )

            # 恢复 access token（子进程需要）
            if data.get("access_token") and not self._access_token:
                self._access_token = data["access_token"]
            if data.get("token_expires_at") and not self._token_expires_at:
                self._token_expires_at = datetime.fromisoformat(data["token_expires_at"])

            return self._cached_status
        except Exception:
            return None

    def _clear_cache(self):
        if self.cache_file.exists():
            self.cache_file.unlink()

    def _parse_license_response(self, data: dict) -> LicenseStatus:
        license_info = data.get("license", {})
        quota = data.get("quota", {})
        return LicenseStatus(
            valid=True,
            license_type=license_info.get("type", "unknown"),
            voice_enabled=license_info.get("voice_enabled", False),
            deepseek_enabled=license_info.get("deepseek_enabled", False),
            voice_daily_quota=license_info.get("voice_daily_quota", 0),
            deepseek_monthly_quota=license_info.get("deepseek_monthly_quota", 0),
            voice_used_today=quota.get("voice_used_today", 0),
            deepseek_used_this_month=quota.get("deepseek_used_this_month", 0),
            expires_at=datetime.fromisoformat(license_info["expires_at"].replace("Z", "+00:00")) if license_info.get("expires_at") else None,
            offline_grace_until=datetime.utcnow() + timedelta(days=self.OFFLINE_GRACE_DAYS),
        )

    def _create_invalid_status(self, message: str) -> LicenseStatus:
        return LicenseStatus(
            valid=False, license_type="none", voice_enabled=False,
            deepseek_enabled=False, voice_daily_quota=0,
            deepseek_monthly_quota=0, voice_used_today=0,
            deepseek_used_this_month=0, expires_at=None,
            offline_grace_until=None, message=message
        )
