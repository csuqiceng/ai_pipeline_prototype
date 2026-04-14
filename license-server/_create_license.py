import requests
# 1. Admin login
r = requests.post('http://localhost:8000/api/v1/admin/login', json={'username':'admin','password':'admin123'})
token = r.json()['data']['access_token']
print('Admin token OK')
# 2. Create license
r = requests.post('http://localhost:8000/api/v1/admin/licenses/create',
    headers={'Authorization': f'Bearer {token}'},
    json={
        'license_type': 'monthly',
        'voice_enabled': True,
        'deepseek_enabled': True,
        'voice_daily_quota': 100,
        'deepseek_monthly_quota': 1000,
        'duration_days': 30
    })
print('Status:', r.status_code)
data = r.json()
print(data)
if 'data' in data and 'license_code' in data['data']:
    print('\n=== LICENSE CODE ===')
    print(data['data']['license_code'])
    print('====================')
