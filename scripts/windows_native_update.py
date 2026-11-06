"""Validate the native Windows update feed and a user-selected installer.

This module can fetch the configured fork's HTTPS feed. It never downloads or
executes an installer; the user chooses whether to download and install it.
"""

from dataclasses import dataclass
import argparse
import hashlib
import hmac
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from packaging.version import InvalidVersion, Version


class InvalidUpdate(ValueError):
    """The feed or candidate cannot be used for this installation."""


@dataclass(frozen=True)
class UpdateCandidate:
    version: str
    revision: int
    channel: str
    url: str
    sha256: str


def validate_update(manifest, *, repository, channel, current_version,
                    current_revision, windows_build):
    """Validate a selected-channel manifest; return None when up to date.

    Manifest fields: repository, channel, version, revision, architecture,
    minimum_windows_build, asset_name, url and sha256. Versions use PEP 440;
    revision orders rebuilds of the same application version.
    """
    if not isinstance(manifest, dict) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise InvalidUpdate('invalid manifest or repository')
    if channel not in ('stable', 'prerelease') or manifest.get('repository') != repository or manifest.get('channel') != channel:
        raise InvalidUpdate('incorrect repository or channel')
    if manifest.get('architecture') != 'x64':
        raise InvalidUpdate('unsupported architecture')
    build = manifest.get('minimum_windows_build')
    revision = manifest.get('revision')
    if (type(build) is not int or build < 22000 or type(windows_build) is not int or windows_build < build
            or type(revision) is not int or revision < 0 or type(current_revision) is not int or current_revision < 0):
        raise InvalidUpdate('unsupported Windows build or invalid revision')
    try:
        raw_version = manifest['version']
        if not isinstance(raw_version, str) or not isinstance(current_version, str):
            raise InvalidUpdate('invalid version')
        version, current = Version(raw_version), Version(current_version)
    except (KeyError, InvalidVersion) as exc:
        raise InvalidUpdate('invalid version') from exc
    if version.is_devrelease or version.is_postrelease or version.local is not None:
        raise InvalidUpdate('unsupported version')
    if channel == 'stable' and version.is_prerelease:
        raise InvalidUpdate('prerelease in stable channel')
    asset, url, digest = manifest.get('asset_name'), manifest.get('url'), manifest.get('sha256')
    if not isinstance(asset, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.exe', asset, re.IGNORECASE):
        raise InvalidUpdate('invalid installer asset')
    if not isinstance(url, str):
        raise InvalidUpdate('invalid installer URL')
    parsed = urlsplit(url)
    prefix = '/' + repository + '/releases/download/'
    parts = unquote(parsed.path).removeprefix(prefix).split('/') if parsed.path.startswith(prefix) else []
    if (parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com' or parsed.query or parsed.fragment
            or len(parts) != 2 or not parts[0] or parts[0] in ('.', '..') or parts[1] != asset):
        raise InvalidUpdate('installer is not the expected repository release asset')
    try:
        if Version(parts[0].removeprefix('v')) != version:
            raise InvalidUpdate('release tag does not match version')
    except InvalidVersion as exc:
        raise InvalidUpdate('invalid release tag') from exc
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
        raise InvalidUpdate('invalid installer hash')
    if (version, revision) <= (current, current_revision):
        return None
    return UpdateCandidate(raw_version, revision, channel, url, digest.lower())


def verify_installer(path, candidate):
    """Verify selected installer bytes; return False on a hash mismatch."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return hmac.compare_digest(digest.hexdigest(), candidate.sha256)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_update(*, repository, channel, current_version, current_revision, windows_build):
    """Fetch the configured fork's selected feed; never download an installer.

    The proposed feed convention is stable.json and prerelease.json on a
    windows-updates branch; a release process must publish it before use.
    Redirects are refused to retain source ownership.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or channel not in ('stable', 'prerelease'):
        raise InvalidUpdate('invalid repository or channel')
    url = f'https://raw.githubusercontent.com/{repository}/windows-updates/{channel}.json'
    with build_opener(_NoRedirect()).open(url, timeout=10) as response:
        data = response.read(65537)
    if len(data) > 65536:
        raise InvalidUpdate('manifest too large')
    try:
        manifest = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidUpdate('invalid manifest JSON') from exc
    return validate_update(manifest, repository=repository, channel=channel,
                           current_version=current_version, current_revision=current_revision,
                           windows_build=windows_build)


def main():
    parser = argparse.ArgumentParser(description='Check the configured native Windows update feed')
    parser.add_argument('--repository', required=True)
    parser.add_argument('--channel', choices=('stable', 'prerelease'), default='stable')
    parser.add_argument('--current-version', required=True)
    parser.add_argument('--current-revision', required=True, type=int)
    parser.add_argument('--windows-build', required=True, type=int)
    parser.add_argument('--verify-installer', type=Path)
    args = parser.parse_args()
    candidate = fetch_update(repository=args.repository, channel=args.channel,
                             current_version=args.current_version, current_revision=args.current_revision,
                             windows_build=args.windows_build)
    if candidate is None:
        if args.verify_installer:
            raise SystemExit('No newer candidate is available for installer verification')
        print(json.dumps({'status': 'up-to-date'}))
        return
    if args.verify_installer and not verify_installer(args.verify_installer, candidate):
        raise SystemExit('Installer SHA-256 does not match the feed')
    print(json.dumps(candidate.__dict__))


if __name__ == '__main__':
    main()
