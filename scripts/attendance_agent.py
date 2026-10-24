#!/usr/bin/env python3
"""Local bridge agent: door station (LAN) -> cloud attendance endpoint.

The Django app runs in the cloud and cannot reach the door station, and the door
station is never exposed to the internet. This agent closes the gap from the
inside: it runs on a machine on the same LAN as the DS-KV6124-WBE1 (e.g. the
telephony Windows Server 2012 box), reads the device's access-event log over the
local network, and forwards each new card event — with a face snapshot — to the
cloud app's /attendance/hook/<secret>/ endpoint over plain outbound HTTPS, which
a LAN normally allows.

Deliberately standalone and standard-library only (urllib + json), so it runs on
any Python 3.6+ without pip, Django or extra packages — important on old Windows.

    python attendance_agent.py --config attendance_agent.config.json

Configuration lives in a JSON file so the device password and the shared secret
never appear in the command line or Task Scheduler. See the .example.json file.
"""
import argparse
import base64
import json
import logging
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone

TASHKENT = timezone(timedelta(hours=5))
LOG = logging.getLogger('attendance_agent')
SNAPSHOT_PATH = '/ISAPI/Streaming/channels/101/picture?videoResolutionWidth=1920&videoResolutionHeight=1080'


def load_config(path):
    with open(path, 'r', encoding='utf-8') as handle:
        config = json.load(handle)
    required = ['device_host', 'device_user', 'device_password', 'server_url']
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise SystemExit('Config incomplete, missing: ' + ', '.join(missing))
    config.setdefault('device_scheme', 'http')
    # One second keeps the gap between the card read and the snapshot under ~1 s,
    # while the person is still at the door (tested on the DS-KV6124: match 0.63).
    config.setdefault('interval_seconds', 1)
    config.setdefault('lookback_minutes', 5)
    config.setdefault('max_results', 30)
    config.setdefault('state_file', 'attendance_agent.state.json')
    # Prefer the picture the device captured at the event moment (pictureURL). The
    # DS-KV6124 never attaches one to card events, so a live frame grabbed as soon
    # as the event is seen stands in for it; with 1 s polling the person is still
    # in front of the camera. Turn off if the poll interval must be long.
    config.setdefault('fallback_snapshot', True)
    # Ask for a full 16:9 frame: the bare /picture is 704x576, squeezed sideways from the
    # 16:9 sensor, which narrows every face and costs the match ~0.2.
    config.setdefault('snapshot_path', SNAPSHOT_PATH)
    config.setdefault('server_verify_tls', True)
    config.setdefault('door_major', 5)
    # The frame buffer: snapshots taken all the time and kept for a few seconds, so a
    # swipe is matched with frames from the moment it happened — the person at the
    # reader — instead of a frame taken after the event was noticed, by when they
    # have often walked through the door.
    config.setdefault('frame_buffer', True)
    config.setdefault('frame_buffer_seconds', 8)
    config.setdefault('frame_interval_seconds', 0.2)
    # Kept tight: the person swiping stands at the reader just before and at the swipe;
    # a wider window lets the next person in a queue into the frames.
    config.setdefault('frame_before_seconds', 1.5)
    config.setdefault('frame_after_seconds', 0.5)
    config.setdefault('max_frames', 4)
    return config


def device_base(config):
    return '{}://{}:{}'.format(config['device_scheme'], config['device_host'], config.get('device_port', 80))


def device_opener(config):
    """A urllib opener that answers the device's HTTP Digest challenge."""
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, device_base(config), config['device_user'], config['device_password'])
    return urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(manager))


def acs_search(opener, base, start_iso, end_iso, position, max_results):
    """One page of the device access-event log as parsed JSON."""
    body = json.dumps({'AcsEventCond': {
        'searchID': 'attendance-agent', 'searchResultPosition': position,
        'maxResults': max_results, 'major': 0, 'minor': 0, 'picEnable': True,
        'startTime': start_iso, 'endTime': end_iso}}).encode('utf-8')
    request = urllib.request.Request(base + '/ISAPI/AccessControl/AcsEvent?format=json',
                                     data=body, headers={'Content-Type': 'application/json'}, method='POST')
    with opener.open(request, timeout=20) as response:
        return json.loads(response.read().decode('utf-8', 'replace'))


def isapi_search(opener, base, path, cond_key, result_key, list_key):
    """Every row of a paged ISAPI search; the device returns at most 30 per page."""
    rows, position = [], 0
    while True:
        body = json.dumps({cond_key: {'searchID': 'access-sync', 'searchResultPosition': position,
                                      'maxResults': 30}}).encode('utf-8')
        request = urllib.request.Request(base + path, data=body, headers={'Content-Type': 'application/json'},
                                         method='POST')
        with opener.open(request, timeout=20) as response:
            result = json.loads(response.read().decode('utf-8', 'replace')).get(result_key, {})
        page = result.get(list_key) or []
        rows.extend(page)
        if result.get('responseStatusStrg') != 'MORE' or not page:
            return rows
        position += len(page)


def export_people(config, opener, path):
    """Write the device's persons and cards to a file for `manage.py sync_access_cards`.

    The cloud server cannot reach the device, so the list travels as a file. The
    PIN is never read; a PIN opening is matched by the person's employeeNo.
    """
    base = device_base(config)
    users = isapi_search(opener, base, '/ISAPI/AccessControl/UserInfo/Search?format=json',
                         'UserInfoSearchCond', 'UserInfoSearch', 'UserInfo')
    cards = isapi_search(opener, base, '/ISAPI/AccessControl/CardInfo/Search?format=json',
                         'CardInfoSearchCond', 'CardInfoSearch', 'CardInfo')
    by_person = {}
    for card in cards:
        by_person.setdefault(str(card.get('employeeNo', '')), []).append(str(card.get('cardNo', '')))
    people = [{'employee_no': str(user.get('employeeNo', '')), 'name': user.get('name', ''),
               'cards': [c for c in by_person.get(str(user.get('employeeNo', '')), []) if c]}
              for user in users if user.get('employeeNo')]
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'device': config['device_host'], 'exported_at': datetime.now(TASHKENT).isoformat(timespec='seconds'),
                   'people': people}, handle, ensure_ascii=False, indent=1)
    LOG.info('Exported %d person(s), %d card(s) to %s', len(people), len(cards), path)


def fetch_bytes(opener, url):
    with opener.open(url, timeout=20) as response:
        return response.read()


def event_picture(opener, base, url):
    """The picture the device captured at the event moment (correct face), or None."""
    if not url:
        return None
    full = url if url.lower().startswith('http') else base + ('' if url.startswith('/') else '/') + url
    try:
        return fetch_bytes(opener, full)
    except (urllib.error.URLError, OSError) as error:
        LOG.warning('Event picture failed: %s', type(error).__name__)
        return None


def snapshot(opener, config):
    """A live camera frame (NOT the event moment) — only a last-resort fallback."""
    path = config['snapshot_path']
    try:
        return fetch_bytes(opener, device_base(config) + path)
    except urllib.error.HTTPError as error:
        if error.code == 404 and '?' in path:
            # This firmware ignores the size options; take its default frame instead
            # (the server restores the squeezed aspect) and stop asking.
            LOG.warning('Snapshot size options not supported; using %s', path.split('?')[0])
            config['snapshot_path'] = path.split('?')[0]
            return snapshot(opener, config)
        LOG.warning('Snapshot failed: HTTPError %s', error.code)
        return None
    except (urllib.error.URLError, OSError) as error:
        LOG.warning('Snapshot failed: %s', type(error).__name__)
        return None


def parse_device_time(text):
    """'2026-10-24T08:38:27+05:00' (or with a space, or no zone) -> aware datetime."""
    text = (text or '').strip().replace(' ', 'T')
    moment = datetime.strptime(text[:19], '%Y-%m-%dT%H:%M:%S')
    zone = text[19:]
    if zone and zone[0] in '+-' and len(zone) >= 6:
        sign = 1 if zone[0] == '+' else -1
        offset = timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])) * sign
        return moment.replace(tzinfo=timezone(offset))
    return moment.replace(tzinfo=TASHKENT)


class FrameBuffer(threading.Thread):
    """Keeps the last few seconds of camera frames, stamped with this machine's clock.

    The device's own clock may be off by a second or more, and event times come
    from it, so the offset is measured and refreshed every ten minutes.
    """

    def __init__(self, config):
        threading.Thread.__init__(self, name='frame-buffer')
        self.daemon = True
        self.config = config
        self.opener = device_opener(config)   # its own opener: not shared across threads
        self.url = device_base(config) + config['snapshot_path']
        self.frames = deque()
        self.lock = threading.Lock()
        self.offset = 0.0                      # device clock minus this machine's clock
        self.next_clock_check = 0.0
        self.failing = False

    def measure_offset(self):
        started = time.time()
        with self.opener.open(device_base(self.config) + '/ISAPI/System/time', timeout=10) as response:
            text = response.read().decode('utf-8', 'replace')
        ended = time.time()
        local = text.split('<localTime>')[1].split('</localTime>')[0]
        self.offset = parse_device_time(local).timestamp() - (started + ended) / 2
        LOG.info('Device clock offset %.1fs', self.offset)

    def run(self):
        keep = float(self.config['frame_buffer_seconds'])
        pause = float(self.config['frame_interval_seconds'])
        while True:
            now = time.time()
            try:
                if now >= self.next_clock_check:
                    self.next_clock_check = now + 600
                    self.measure_offset()
                started = time.time()
                data = fetch_bytes(self.opener, self.url)
                stamp = (started + time.time()) / 2
                with self.lock:
                    self.frames.append((stamp, data))
                    while self.frames and self.frames[0][0] < stamp - keep:
                        self.frames.popleft()
                if self.failing:
                    LOG.info('Frame buffer recovered')
                    self.failing = False
                time.sleep(pause)
            except (urllib.error.URLError, OSError, ValueError, IndexError) as error:
                if not self.failing:
                    LOG.warning('Frame buffer failed (%s); retrying', type(error).__name__)
                    self.failing = True
                time.sleep(2)

    def around(self, device_time_text):
        """Frames from around a swipe stamped by the device; [] if none were kept."""
        moment = parse_device_time(device_time_text).timestamp() - self.offset
        # Event times are whole seconds: the swipe happened within [t, t+1).
        start = moment - float(self.config['frame_before_seconds'])
        end = moment + 1 + float(self.config['frame_after_seconds'])
        with self.lock:
            picked = [data for stamp, data in self.frames if start <= stamp <= end]
        limit = max(1, int(self.config['max_frames']))
        if len(picked) > limit:
            step = (len(picked) - 1) / float(limit - 1) if limit > 1 else 0
            picked = [picked[int(round(index * step))] for index in range(limit)]
        return picked


def event_key(event):
    serial = event.get('serialNo')
    if serial:
        return 's:' + str(serial)
    return '|'.join(str(event.get(field, '')) for field in ('time', 'cardNo', 'employeeNoString', 'minor', 'doorNo'))


def is_person_event(event, door_major):
    return event.get('major') == door_major and bool(event.get('employeeNoString') or event.get('cardNo'))


def forward(config, event, pictures):
    """POST one event (and its frames) to the cloud endpoint, like a device push."""
    url = config['server_url']
    context = None
    if url.startswith('https') and not config['server_verify_tls']:
        context = ssl._create_unverified_context()
    if pictures:
        boundary = '----attendanceagent' + base64.urlsafe_b64encode(os.urandom(9)).decode('ascii')
        parts = []
        parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="event"\r\n'
                      'Content-Type: application/json\r\n\r\n').encode('utf-8'))
        parts.append(json.dumps(event).encode('utf-8') + b'\r\n')
        for index, picture in enumerate(pictures):
            parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="picture"; '
                          'filename="face{}.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.format(index)).encode('utf-8'))
            parts.append(picture + b'\r\n')
        parts.append(('--' + boundary + '--\r\n').encode('utf-8'))
        data = b''.join(parts)
        headers = {'Content-Type': 'multipart/form-data; boundary=' + boundary}
    else:
        data = json.dumps(event).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
    request = urllib.request.Request(url, data=data, headers=headers, method='POST')
    with urllib.request.urlopen(request, timeout=20, context=context) as response:
        return response.status


def load_state(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            state = json.load(handle)
            return state.get('cursor'), set(state.get('seen', []))
    except (OSError, ValueError):
        return None, set()


def save_state(path, cursor, seen):
    recent = list(seen)[-2000:]
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump({'cursor': cursor, 'seen': recent}, handle)
    os.replace(tmp, path)


def pictures_for(config, opener, base, event, frames):
    """The frames to send with an event: the device's own picture, else buffered ones.

    With the buffer running, an event too old for it (the agent was down, or the
    event arrived late) gets no picture at all: a frame of the empty corridor taken
    now would only open a needless review. The entry still counts, card-only.
    """
    picture = event_picture(opener, base, event.get('pictureURL'))
    if picture:
        return [picture]
    if frames is not None:
        try:
            return frames.around(event.get('time'))
        except (ValueError, TypeError):
            return []
    if config['fallback_snapshot']:
        picture = snapshot(opener, config)
        return [picture] if picture else []
    return []


def poll_once(config, opener, cursor, seen, frames=None):
    """Fetch every new access event since the cursor and forward the person ones."""
    now = datetime.now(TASHKENT)
    start = cursor or (now - timedelta(minutes=config['lookback_minutes'])).strftime('%Y-%m-%d %H:%M:%S')
    # The device rejects fractional seconds in the time filter, so pin to seconds.
    start_iso = datetime.strptime(start, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TASHKENT).isoformat(timespec='seconds')
    end_iso = (now + timedelta(seconds=2)).isoformat(timespec='seconds')
    base = device_base(config)
    position, newest, forwarded = 0, cursor, 0
    while True:
        result = acs_search(opener, base, start_iso, end_iso, position, config['max_results']).get('AcsEvent', {})
        events = result.get('InfoList', []) or []
        for event in events:
            when = event.get('time')
            if when and (newest is None or when > newest):
                newest = when
            if not is_person_event(event, config['door_major']):
                continue
            key = event_key(event)
            if key in seen:
                continue
            pictures = pictures_for(config, opener, base, event, frames)
            try:
                status = forward(config, event, pictures)
            except (urllib.error.URLError, OSError) as error:
                # Leave it unmarked so the next poll retries it; stop this round.
                code = getattr(error, 'code', None)
                hint = ' (404: server_url secret does not match ATTENDANCE_WEBHOOK_SECRET, or IP not allowed)' \
                    if code == 404 else ''
                LOG.warning('Forward failed (%s%s)%s; will retry next poll', type(error).__name__,
                            ' %s' % code if code else '', hint)
                return newest, forwarded
            LOG.info('Forwarded %s card=%s emp=%s frames=%d -> %s', event.get('name') or '?',
                     event.get('cardNo'), event.get('employeeNoString'), len(pictures), status)
            seen.add(key)
            forwarded += 1
        if result.get('responseStatusStrg') == 'MORE' and events:
            position += len(events)
        else:
            break
    return newest, forwarded


def main():
    parser = argparse.ArgumentParser(description='Door station -> cloud attendance bridge.')
    parser.add_argument('--config', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         'attendance_agent.config.json'))
    parser.add_argument('--once', action='store_true', help='Poll a single time and exit (for testing).')
    parser.add_argument('--export-users', metavar='FILE',
                        help='Write the device persons and cards to FILE (for sync_access_cards) and exit.')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = load_config(args.config)
    opener = device_opener(config)
    if args.export_users:
        export_people(config, opener, args.export_users)
        return 0
    cursor, seen = load_state(config['state_file'])
    frames = None
    if config['frame_buffer'] and not args.once:   # a single poll has no time to fill a buffer
        frames = FrameBuffer(config)
        frames.start()
    LOG.info('Agent started. Device=%s Server=%s Interval=%ss Cursor=%s',
             config['device_host'], config['server_url'], config['interval_seconds'], cursor)
    while True:
        try:
            cursor, forwarded = poll_once(config, opener, cursor, seen, frames)
            save_state(config['state_file'], cursor, seen)
            if forwarded:
                LOG.info('%d new event(s) forwarded. Cursor=%s', forwarded, cursor)
        except KeyboardInterrupt:
            LOG.info('Stopped.')
            return 0
        except (urllib.error.URLError, OSError, ValueError) as error:
            LOG.warning('Poll failed (%s): %s', type(error).__name__, error)
        if args.once:
            return 0
        time.sleep(max(1, int(config['interval_seconds'])))


if __name__ == '__main__':
    sys.exit(main())
