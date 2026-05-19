import requests
BASE='http://127.0.0.1:8000'

# Create user A
r = requests.post(f'{BASE}/auth/signup', json={'email':'ownerA@example.com','password':'OwnerA!23','displayName':'Owner A'})
assert r.status_code==200
data = r.json()
accessA = data['access_token']
headersA = {'Authorization': f'Bearer {accessA}'}

# Create job as user A (use youtube_url to avoid file upload)
r = requests.post(f'{BASE}/upload', data={'youtube_url':'https://example.com/video.mp4'}, headers=headersA)
print('upload status', r.status_code)
assert r.status_code==200
job = r.json()
job_id = job['id']
print('job created', job_id, 'userId', job.get('userId'))
assert job.get('userId')

# Create user B
r = requests.post(f'{BASE}/auth/signup', json={'email':'ownerB@example.com','password':'OwnerB!23','displayName':'Owner B'})
assert r.status_code==200
accessB = r.json()['access_token']
headersB = {'Authorization': f'Bearer {accessB}'}

# User A can access status
r = requests.get(f'{BASE}/status/{job_id}', headers=headersA)
print('A status', r.status_code)
assert r.status_code==200

# User B cannot access status
r = requests.get(f'{BASE}/status/{job_id}', headers=headersB)
print('B status', r.status_code, r.text[:200])
assert r.status_code==403

print('job ownership enforced')
