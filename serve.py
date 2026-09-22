"""One Django ASGI process serves pages, HTTP APIs and realtime voice."""
import argparse
import asyncio
import ipaddress
import re
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn

CERTS = Path(__file__).resolve().parent / 'certs'


def local_names():
    names = {'localhost', '127.0.0.1', socket.gethostname()}
    try:
        names.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return names


def self_signed(extra):
    """Browsers allow the microphone only in secure contexts, so LAN access needs HTTPS."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key_path, cert_path = CERTS / 'server.key', CERTS / 'server.crt'
    names = sorted(local_names() | set(extra))
    marker = CERTS / 'names.txt'
    if key_path.exists() and cert_path.exists() and marker.exists() and marker.read_text() == '\n'.join(names):
        return key_path, cert_path
    CERTS.mkdir(exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    alt = []
    for name in names:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alt.append(x509.DNSName(name))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Topshiriq nazorati (lokal)')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    marker.write_text('\n'.join(names))
    return key_path, cert_path


HOST = re.compile(r'^[A-Za-z0-9.\-]+$|^\[[0-9A-Fa-f:.]+\]$')


def http_redirect(https_port):
    """Answer plain HTTP with a redirect, so typing the bare IP reaches the HTTPS site."""
    suffix = '' if https_port == 443 else f':{https_port}'

    async def handle(reader, writer):
        try:
            head = (await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)).decode('latin-1').split('\r\n')
            parts = head[0].split(' ')
            target = parts[1] if len(parts) == 3 and parts[1].startswith('/') else '/'
            headers = dict(line.split(':', 1) for line in head[1:] if ':' in line)
            host = next((v.strip() for k, v in headers.items() if k.strip().lower() == 'host'), '')
            host = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
            if not HOST.match(host):
                host = writer.get_extra_info('sockname')[0]
            writer.write(f'HTTP/1.1 307 Temporary Redirect\r\nLocation: https://{host}{suffix}{target}\r\n'
                         'Content-Length: 0\r\nConnection: close\r\n\r\n'.encode('latin-1'))
            await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, OSError, ValueError):
            pass
        finally:
            writer.close()
    return handle


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int)
    parser.add_argument('--https', action='store_true', help='Serve HTTPS with a local self-signed certificate.')
    parser.add_argument('--ssl-keyfile', help='Use this key instead of the generated one.')
    parser.add_argument('--ssl-certfile', help='Use this certificate instead of the generated one.')
    parser.add_argument('--san', action='append', default=[], help='Extra IP/domain for the generated certificate.')
    parser.add_argument('--proxy', action='store_true',
                        help='Behind a reverse proxy: trust its X-Forwarded-For/Proto headers.')
    args = parser.parse_args()
    secure = args.https or bool(args.ssl_certfile)
    port = args.port or (443 if secure else 8000)
    if secure and port == 80:
        parser.error('80-port oddiy HTTP uchun. HTTPS uchun --port ni olib tashlang (443 ishlatiladi, 80 esa unga yo‘naltiradi).')
    ssl = {}
    if secure:
        key, cert = (args.ssl_keyfile, args.ssl_certfile) if args.ssl_certfile else self_signed(args.san)
        ssl = {'ssl_keyfile': str(key), 'ssl_certfile': str(cert)}
    shown = sorted(local_names()) if args.host == '0.0.0.0' else [args.host]
    suffix = '' if port == (443 if secure else 80) else f':{port}'
    print('Topshiriq nazorati: ' + ', '.join(f"{'https' if secure else 'http'}://{name}{suffix}" for name in shown), flush=True)
    config = uvicorn.Config('config.asgi:application', host=args.host, port=port,
                            ws='websockets-sansio', ws_max_size=65536, lifespan='off',
                            proxy_headers=args.proxy, forwarded_allow_ips='*' if args.proxy else None,
                            access_log=False, **ssl)

    def ignore_client_drops(loop, context):
        # A browser that navigates away mid-response leaves two harmless traces:
        # Windows Proactor resets the socket, and the cancelled request task
        # reports a CancelledError nobody is left to await. Neither is a fault
        # of the application, and both would otherwise be logged as errors.
        if isinstance(context.get('exception'), (ConnectionResetError, asyncio.CancelledError)):
            return
        loop.default_exception_handler(context)

    async def main():
        asyncio.get_running_loop().set_exception_handler(ignore_client_drops)
        if secure and port == 443:
            try:
                await asyncio.start_server(http_redirect(port), args.host, 80)
                print('http:// (80-port) so‘rovlari https:// ga yo‘naltiriladi.', flush=True)
            except OSError:
                print('80-port band: http:// manzil yo‘naltirilmaydi, https:// ni o‘zingiz yozing.', flush=True)
        await uvicorn.Server(config).serve()

    asyncio.run(main())
