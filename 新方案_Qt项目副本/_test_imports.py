import sys
sys.path.insert(0, '.')
from robot_modbus_lite.license_manager import LicenseManager, LicenseStatus
from robot_modbus_lite.deepseek_client import DeepSeekClient
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter
print('All imports OK')
adapter = VoiceNlpAdapter({}, [])
assert hasattr(adapter, 'set_deepseek_client')
assert hasattr(adapter, '_external_deepseek_client')
print('VoiceNlpAdapter OK')
assert hasattr(DeepSeekClient, 'from_license')
assert hasattr(DeepSeekClient, 'from_env')
print('DeepSeekClient OK')
