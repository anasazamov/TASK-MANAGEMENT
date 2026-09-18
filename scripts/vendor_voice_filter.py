"""Fetch pinned, local-only Silero/ONNX assets; never run package install scripts."""
import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'static' / 'vendor' / 'voice-filter-v1'
PACKAGES = {
    '@ricky0123/vad-web': ('0.0.31', ['dist/silero_vad_v5.onnx']),
    'onnxruntime-web': ('1.22.0', ['dist/ort.wasm.min.js', 'dist/ort.wasm.min.js.map',
        'dist/ort-wasm-simd-threaded.mjs', 'dist/ort-wasm-simd-threaded.wasm']),
}


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for package, (version, files) in PACKAGES.items():
        meta = json.loads(download('https://registry.npmjs.org/'+package+'/'+version))
        assert meta['dist']['tarball'].startswith('https://registry.npmjs.org/')
        raw = download(meta['dist']['tarball'])
        integrity = 'sha512-'+base64.b64encode(hashlib.sha512(raw).digest()).decode()
        if integrity != meta['dist']['integrity']: raise ValueError('Package integrity mismatch')
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
            for name in files:
                data = archive.extractfile('package/'+name).read()
                (ROOT / Path(name).name).write_bytes(data)
                manifest[Path(name).name] = {'package': package, 'version': version,
                    'sha256': hashlib.sha256(data).hexdigest(), 'npm_integrity': integrity}
    licenses = {
        'LICENSE-ONNX.txt': 'https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.0/LICENSE',
        'LICENSE-Silero.txt': 'https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE',
    }
    for name, url in licenses.items():
        data = download(url); (ROOT / name).write_bytes(data)
        manifest[name] = {'source': url, 'sha256': hashlib.sha256(data).hexdigest()}
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print('Vendored', len(manifest), 'verified files into', ROOT)


if __name__ == '__main__': main()
