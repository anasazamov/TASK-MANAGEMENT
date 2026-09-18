"""Install the pinned local speaker model; no user data or credentials are used."""
import hashlib
import sys
from pathlib import Path

import httpx

NAME = '3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx'
SHA256 = 'aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2'
URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/' + NAME


def main():
    target = Path(__file__).resolve().parents[1] / 'private_models' / NAME
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == SHA256:
        print('Speaker model is ready.')
        return
    response = httpx.get(URL, follow_redirects=True, timeout=120)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != SHA256:
        sys.exit('Model checksum mismatch; file was not installed.')
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_suffix('.download')
    temporary.write_bytes(response.content)
    temporary.replace(target)
    print('Speaker model installed and checksum verified.')


if __name__ == '__main__':
    main()
