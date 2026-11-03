"""Exercise both APIs through the frontend gateway. Uses only Python's standard library."""
from pathlib import Path
import argparse
import http.cookiejar
import json
import struct
import time
import urllib.error
import urllib.request
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.run' / 'smoke'


def png():
    def chunk(kind, payload):
        return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 16, 16, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\xc0\x00' * 16) * 16)) + chunk(b'IEND', b'')


class Client:
    def __init__(self, base, cookie_file=None):
        self.base = base.rstrip('/')
        self.jar = http.cookiejar.MozillaCookieJar(str(cookie_file)) if cookie_file else http.cookiejar.CookieJar()
        if cookie_file and cookie_file.exists():
            self.jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, path, method='GET', payload=None, headers=None, expected=200):
        request_headers = {'X-Requested-With': 'VolnaApp', **(headers or {})}
        data = payload
        if isinstance(payload, dict):
            data = json.dumps(payload).encode()
            request_headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=request_headers)
        try:
            response = self.opener.open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        content = response.read()
        assert response.code == expected, f'{method} {path}: expected {expected}, got {response.code}: {content[:300]!r}'
        if content and 'application/json' in response.headers.get('Content-Type', ''):
            return json.loads(content)
        return content

    def upload(self, path, fields, expected=200):
        boundary = 'volleyball' + uuid.uuid4().hex
        parts = []
        for key, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="check.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + png() + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        return self.request(path, 'POST', b''.join(parts), {'Content-Type': 'multipart/form-data; boundary=' + boundary}, expected)

    def wait_ready(self):
        # EndpointSlice updates can briefly lag behind Pod readiness after a rollout.
        for path in ('/api/team', '/api/auth/teams'):
            deadline = time.monotonic() + 30
            while True:
                try:
                    self.request(path)
                    break
                except (AssertionError, urllib.error.URLError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://localhost:8088')
    parser.add_argument('--keep-state', action='store_true')
    parser.add_argument('--verify-persistence', action='store_true')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    cookie_path = STATE / 'cookies.txt'
    state_path = STATE / 'expected.json'
    client = Client(args.base, cookie_path if args.verify_persistence else None)
    client.wait_ready()
    if args.verify_persistence:
        state = json.loads(state_path.read_text())
        user = client.request('/api/auth/me')
        assert user['id'] == state['user_id'] and user['team_id'] == 'karasuno'
        assert user['avatar_url'] == state['avatar_url']
        client.request(user['avatar_url'])
        players = client.request('/api/players')
        player = next(p for p in players if p['id'] == state['player_id'])
        if 'description' in state:
            assert player['description'] == state['description']
        client.request(player['photo_url'])
        print('PASS: account, session, preference, avatar, player and photo survived Pod recreation.')
    else:
        html = client.request('/')
        assert b'id="root"' in html
        client.request('/account')
        client.request('/api/team')
        assert len(client.request('/api/auth/teams')) == 6
        anonymous = Client(args.base)
        anonymous.request('/api/auth/me', expected=401)
        anonymous.request('/api/players', 'POST', {}, expected=401)
        username = 'check_' + uuid.uuid4().hex[:12]
        credentials = {'username': username, 'password': uuid.uuid4().hex}
        client.request('/api/auth/register', 'POST', credentials, {'X-Requested-With': ''}, 403)
        user = client.request('/api/auth/register', 'POST', credentials, expected=201)
        client.request('/api/auth/register', 'POST', credentials, expected=409)
        client.request('/api/auth/login', 'POST', {**credentials, 'password': 'incorrect-password'}, expected=401)
        client.request('/api/auth/login', 'POST', credentials)
        assert client.request('/api/auth/me')['id'] == user['id']
        for team_id in ('nekoma', 'fukurodani', 'inarizaki', 'aoba', 'shiratorizawa', 'karasuno'):
            client.request('/api/auth/profile', 'PATCH', {'team_id': team_id})
            assert client.request('/api/auth/me')['team_id'] == team_id
        avatar = client.upload('/api/auth/avatar', {})
        client.request(avatar['avatar_url'])
        used = {p['number'] for p in client.request('/api/players')}
        number = next(n for n in range(99, 0, -1) if n not in used)
        description = 'Капитан команды.\nСильная подача и точный пас.'
        player = client.upload('/api/players/with-photo', {'number': number, 'full_name': 'Проверка контейнеров', 'position': 'Связующий', 'height_cm': 190, 'country': 'Россия', 'description': description}, 201)
        assert player['description'] == description
        info_path = f'/api/players/{player["id"]}'
        anonymous.request(info_path, 'PATCH', {'description': 'Unauthorized'}, expected=401)
        client.request(info_path, 'PATCH', {'description': 'Missing header'}, {'X-Requested-With': ''}, 403)
        client.request(info_path, 'PATCH', {'description': 'x' * 2001}, expected=422)
        client.upload('/api/players/with-photo', {'number': number, 'full_name': 'Проверка', 'position': 'Либеро', 'height_cm': 180, 'country': 'Россия', 'description': 'x' * 2001}, 422)
        description = '  Игрок «Волны».\nОпыт: 5 лет. <strong>Текст, не HTML</strong>  '
        updated = client.request(info_path, 'PATCH', {'description': description})
        description = description.strip()
        assert updated['description'] == description
        listed = next(p for p in client.request('/api/players') if p['id'] == player['id'])
        assert listed['description'] == description and listed['photo_url'] == player['photo_url']
        assert client.request(info_path, 'PATCH', {'description': ''})['description'] == ''
        client.request(info_path, 'PATCH', {'description': description})
        client.request('/api/players/2147483647', 'PATCH', {'description': 'Missing player'}, expected=404)
        print('PASS: player description creation, editing, clearing, persistence, length validation and authorization.')
        client.request(player['photo_url'])
        client.request('/api/players', 'POST', {k: player[k] for k in ('number', 'full_name', 'position', 'height_cm', 'country')}, expected=409)
        state = {'user_id': user['id'], 'username': username, 'player_id': player['id'], 'avatar_url': avatar['avatar_url'], 'description': description}
        print('PASS: frontend, registration, session, rejected password, CSRF, six teams, avatar, cross-service authorization, player creation, duplicate protection and photo.')
        if args.keep_state:
            saved = http.cookiejar.MozillaCookieJar(str(cookie_path))
            for cookie in client.jar:
                saved.set_cookie(cookie)
            saved.save(ignore_discard=True, ignore_expires=True)
            state_path.write_text(json.dumps(state), encoding='utf-8')
            print('Persistence fixture saved in .run/smoke (contains a test session; excluded from Git).')
            return
    client.request(f'/api/players/{state["player_id"]}', 'DELETE', expected=204)
    client.request('/api/auth/logout', 'POST', expected=204)
    client.request('/api/auth/me', expected=401)
    client.request('/api/players', 'POST', {}, expected=401)
    cookie_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    print('PASS: test player removed, logout invalidates the session.')


if __name__ == '__main__':
    main()
