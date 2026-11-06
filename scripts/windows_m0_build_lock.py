"""Build the experimental Windows lock from pinned, locally verified wheels."""

import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts'
CACHE = ROOT / '.m0'
WHEELS = {
    'bleach': ('security-wheels/bleach-6.4.0-py3-none-any.whl', '6.4.0', '4b6b6a54fff2e69a3dde9d21cc6301220bee3c3cb792187d11403fd795031081'),
    'cryptography': ('security-wheels/cryptography-50.0.2-cp311-abi3-win_amd64.whl', '50.0.2', '7afa5a6602a9f29af1f3a2965f831bae7c9d5d597b7cbb716d41ab3b7d89879c'),
    'curl-cffi': ('security-wheels/curl_cffi-0.17.0-cp310-abi3-win_amd64.whl', '0.17.0', '4c3993c7df155d622869b82a56adb018440587fd3faffeaf8d01cc016d2e1357'),
    'httplib2': ('security-wheels/httplib2-0.32.1-py3-none-any.whl', '0.32.1', '82e7a6ab08fcb27879864ddf63dbb529fb032ca336c331cb1597d6dd8b47c92e'),
    'lxml': ('security-wheels/lxml-6.1.3-cp313-cp313-win_amd64.whl', '6.1.3', 'e477aca0bc0d19f3b4ae9e4f2a1cfd687c31bf772d78734910658186b40b2477'),
    'pillow': ('security-wheels/pillow-13.0.0-cp313-cp313-win_amd64.whl', '13.0.0', '9dda0788a9932776a75d8bcc020076edc308ce2508b51f6739a45e3158b3fc42'),
    'pyopenssl': ('security-wheels/pyopenssl-26.4.0-py3-none-any.whl', '26.4.0', 'f0eb0cb2d581d3ad2b9c489468485e7f2ab6727d08401bcf9d824c3caddf3c1c'),
    'requests': ('security-wheels/requests-2.34.2-py3-none-any.whl', '2.34.2', '2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0'),
    'tornado': ('security-wheels/tornado-6.6-cp311-abi3-win_amd64.whl', '6.6', '48f26ed9d7beb64e5f06adc864524e581ab6d62ba8ff46eaaa1ac2d0e6d753fd'),
    'oauth2client': ('drive-repaired/oauth2client-4.1.3+cwng.m0-py2.py3-none-any.whl', '4.1.3+cwng.m0', '7c9f71e1b3e027b523ba481ddae1ba9a28c58b039d1734e98ef18bddbcd777c9'),
    'pydrive2': ('drive-repaired/PyDrive2-1.21.3+cwng.m0-py3-none-any.whl', '1.21.3+cwng.m0', '069777feeea6474d88cfdb2dff7d56810c244ac25f078d1b4bd82c517eaa203b'),
    'python-magic-bin': ('magic-repaired/python_magic_standalone-0.4.29+cwng.m0-cp313-none-win_amd64.whl', '0.4.29+cwng.m0', 'f0eee050a262cd9b526b857bd2673e44ecacba36e5d21b5d6ce792dc80cf7cab'),
}


def main():
    for relative, _, expected in WHEELS.values():
        actual = hashlib.sha256((CACHE / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'wheel hash mismatch: {relative}')
    baseline = (SCRIPT / 'windows-m0-runtime-baseline-requirements.txt').read_text(encoding='utf-8-sig')
    if hashlib.sha256(baseline.encode()).hexdigest() != '56602639cac5c9c67c4e7ec2d6c04ae318dde1f6d218f4c60175093df3f99272':
        raise ValueError('unexpected baseline lock contents')
    lines = baseline.splitlines()
    replaced = set()
    result = ['# Experimental Windows M0 lock; see windows-m0-progress.md for limitations.']
    for line in lines:
        match = re.match(r'^([A-Za-z0-9_.-]+)==', line)
        if not match:
            if not line.startswith('#'):
                result.append(line)
            continue
        name = match.group(1).lower()
        if name in WHEELS:
            if name in replaced:
                raise ValueError(f'duplicate requirement: {name}')
            _, version, sha256 = WHEELS[name]
            output_name = 'python-magic-standalone' if name == 'python-magic-bin' else name
            line = f'{output_name}=={version} --hash=sha256:{sha256}'
            replaced.add(name)
        result.append(line)
    if replaced != set(WHEELS):
        raise ValueError(f'baseline is missing: {set(WHEELS) - replaced}')
    (SCRIPT / 'windows-m0-runtime-requirements.txt').write_text('\n'.join(result) + '\n', encoding='utf-8', newline='\n')
    print(f'Wrote {len(replaced)} revised locked wheels.')


if __name__ == '__main__':
    main()
