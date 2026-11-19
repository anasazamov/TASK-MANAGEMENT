"""The door-station agent (scripts/attendance_agent.py) against a device that drops connections."""
import importlib.util
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import TestCase

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'attendance_agent.py'
spec = importlib.util.spec_from_file_location('attendance_agent', SCRIPT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class FlakyDevice(BaseHTTPRequestHandler):
    """Asks for Digest auth; hangs up on the next `drops` authenticated requests."""
    drops = 0

    def do_GET(self):
        if not self.headers.get('Authorization'):
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Digest realm="device", nonce="abc", qop="auth"')
            self.send_header('Content-Length', '0')
            self.end_headers()
        elif FlakyDevice.drops:
            FlakyDevice.drops -= 1
            self.close_connection = True
            self.connection.close()                               # no response at all
        else:
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')

    def log_message(self, *args):
        pass


class DigestRetryTests(TestCase):
    def setUp(self):
        self.server = HTTPServer(('127.0.0.1', 0), FlakyDevice)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.config = {'device_scheme': 'http', 'device_host': '127.0.0.1', 'device_port': self.server.server_port,
                       'device_user': 'user', 'device_password': 'x'}
        self.url = agent.device_base(self.config) + '/ISAPI/System/time'

    def test_reused_opener_sticks_after_dropped_connections(self):
        """Why the agent went silent: the same opener refuses a healthy device forever."""
        opener = agent.device_opener(self.config)
        FlakyDevice.drops = 6
        for _ in range(6):
            with self.assertRaises((urllib.error.URLError, OSError)):
                agent.fetch_bytes(opener, self.url)
        self.assertEqual(FlakyDevice.drops, 0)                    # the device is fine again
        with self.assertRaises(urllib.error.HTTPError) as raised:
            agent.fetch_bytes(opener, self.url)
        self.assertEqual((raised.exception.code, raised.exception.reason), (401, 'digest auth failed'))

    def test_new_opener_after_each_failure_recovers(self):
        """What the agent does now: a failed request costs it the opener, not the day."""
        opener = agent.device_opener(self.config)
        FlakyDevice.drops = 6
        failures = 0
        for _ in range(8):
            try:
                self.assertEqual(agent.fetch_bytes(opener, self.url), b'ok')
                break
            except (urllib.error.URLError, OSError):
                failures += 1
                opener = agent.device_opener(self.config)
        else:
            self.fail('never recovered')
        self.assertEqual(failures, 6)
