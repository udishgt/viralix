import requests
import time
BASE='http://127.0.0.1:8000'

email='http-smoke@example.com'
password='Sm0keTest!'

# Ensure clean state by attempting signup (may fail if exists)
print('signup...')
r = requests.post(f'{BASE}/auth/signup', json={'email': email, 'password': password, 'displayName':'HTTP Smoke'})
print('signup status', r.status_code, r.text[:200])
if r.status_code not in (200,201):
    # try login instead
    r2 = requests.post(f'{BASE}/auth/login', json={'email': email, 'password': password})
    print('login status', r2.status_code, r2.text[:200])
    assert r2.status_code==200
    data = r2.json()
else:
    data = r.json()

access = data['access_token']
refresh = data['refresh_token']
print('access len', len(access))

# me
h = {'Authorization': f'Bearer {access}'}
r = requests.get(f'{BASE}/auth/me', headers=h)
print('/auth/me', r.status_code, r.json())
assert r.status_code==200

# history protected
r = requests.get(f'{BASE}/history')
print('/history no auth', r.status_code)
assert r.status_code==401

r = requests.get(f'{BASE}/history', headers=h)
print('/history with auth', r.status_code, r.json())
assert r.status_code==200

# refresh
r = requests.post(f'{BASE}/auth/refresh', json={'refresh_token': refresh})
print('/auth/refresh', r.status_code, r.text[:200])
assert r.status_code==200
new_access = r.json().get('access_token')
print('new access len', len(new_access))

print('http smoke tests passed')
