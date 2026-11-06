"""Local network consent and Private-profile address discovery.

State changes require the caller to hold the native profile owner lock. This
module does not open a network listener or change Windows firewall settings.
"""

import base64
import ctypes
from ctypes import wintypes
import ipaddress
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import uuid

from windows_m0_inbox import _flush_directory


_DEFAULT = {'schema': 1, 'lan_enabled': False, 'recovery_paused': False}
_PRIVATE_SUBNETS = tuple(ipaddress.IPv4Network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
_PROFILE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$results = @(
    foreach ($profile in @(Get-NetConnectionProfile -ErrorAction Stop)) {
        if ($profile.NetworkCategory -ne 'Private') { continue }
        foreach ($address in @(Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $profile.InterfaceIndex -ErrorAction Stop)) {
            if ($address.AddressState -ne 'Preferred') { continue }
            [ordered]@{ address = [string]$address.IPAddress; interface = [int]$profile.InterfaceIndex; prefix = [int]$address.PrefixLength }
        }
    }
)
ConvertTo-Json -InputObject $results -Compress
"""


class _GUID(ctypes.Structure):
    _fields_ = [('data', ctypes.c_ubyte * 16)]


class _IPAddrRow(ctypes.Structure):
    _fields_ = [('address', wintypes.DWORD), ('index', wintypes.DWORD),
                ('mask', wintypes.DWORD), ('broadcast', wintypes.DWORD),
                ('reassembly', wintypes.DWORD), ('unused', wintypes.WORD),
                ('kind', wintypes.WORD)]


def _guid(value):
    return _GUID((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))


def _com_call(instance, index, argument_types, *arguments):
    table = ctypes.cast(instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argument_types)(table[index])
    return function(instance, *arguments)


def _release(instance):
    if instance:
        _com_call(instance, 2, [])


def _current_ipv4(address, prefix, index):
    """Check an address and subnet against the current Windows IP table."""
    api = ctypes.WinDLL('iphlpapi', use_last_error=True)
    api.GetIpAddrTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL]
    api.GetIpAddrTable.restype = wintypes.ULONG
    size = wintypes.ULONG()
    result = api.GetIpAddrTable(None, ctypes.byref(size), False)
    if result != 122 or not 4 <= size.value <= 1024 * 1024:
        raise OSError('Windows IPv4 interface table is unavailable')
    buffer = ctypes.create_string_buffer(size.value)
    result = api.GetIpAddrTable(buffer, ctypes.byref(size), False)
    if result:
        raise OSError('Windows IPv4 interface table changed during query')
    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    offset = ctypes.sizeof(wintypes.DWORD)
    if count > (len(buffer) - offset) // ctypes.sizeof(_IPAddrRow):
        raise OSError('invalid Windows IPv4 interface table')
    rows = (_IPAddrRow * count).from_buffer(buffer, offset)
    for row in rows:
        if row.index != index:
            continue
        current = socket.inet_ntoa(int(row.address).to_bytes(4, 'little'))
        mask = socket.inet_ntoa(int(row.mask).to_bytes(4, 'little'))
        try:
            current_prefix = ipaddress.IPv4Network('0.0.0.0/' + mask).prefixlen
        except ValueError:
            continue
        if current == address and current_prefix == prefix:
            return True
    return False


def _private_interface_category(index):
    """Query Windows Network List Manager for an interface's live category."""
    ip = ctypes.WinDLL('iphlpapi', use_last_error=True)
    ip.ConvertInterfaceIndexToLuid.argtypes = [wintypes.ULONG, ctypes.POINTER(ctypes.c_ulonglong)]
    ip.ConvertInterfaceIndexToLuid.restype = wintypes.ULONG
    ip.ConvertInterfaceLuidToGuid.argtypes = [ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(_GUID)]
    ip.ConvertInterfaceLuidToGuid.restype = wintypes.ULONG
    luid, expected = ctypes.c_ulonglong(), _GUID()
    if ip.ConvertInterfaceIndexToLuid(index, ctypes.byref(luid)) or ip.ConvertInterfaceLuidToGuid(
            ctypes.byref(luid), ctypes.byref(expected)):
        raise OSError('network interface identity is unavailable')

    ole = ctypes.WinDLL('ole32', use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    if ole.CoInitializeEx(None, 0) < 0:
        raise OSError('network profile query could not initialize COM')
    manager = ctypes.c_void_p()
    enumerator = ctypes.c_void_p()
    try:
        clsid = _guid('dcb00c01-570f-4a9b-8d69-199fdba5723b')
        iid = _guid('dcb00000-570f-4a9b-8d69-199fdba5723b')
        if ole.CoCreateInstance(ctypes.byref(clsid), None, 23, ctypes.byref(iid), ctypes.byref(manager)) < 0 or not manager:
            raise OSError('network profile manager is unavailable')
        if _com_call(manager, 9, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(enumerator)) < 0 or not enumerator:
            raise OSError('network connections are unavailable')
        found = False
        while True:
            connection, fetched = ctypes.c_void_p(), wintypes.ULONG()
            result = _com_call(enumerator, 8, [wintypes.ULONG, ctypes.POINTER(ctypes.c_void_p),
                                               ctypes.POINTER(wintypes.ULONG)],
                               1, ctypes.byref(connection), ctypes.byref(fetched))
            if result == 1:
                return found
            if result != 0 or fetched.value != 1 or not connection:
                raise OSError('network connection enumeration failed')
            network = ctypes.c_void_p()
            try:
                adapter, connected = _GUID(), ctypes.c_short()
                if (_com_call(connection, 12, [ctypes.POINTER(_GUID)], ctypes.byref(adapter)) < 0 or
                        _com_call(connection, 9, [ctypes.POINTER(ctypes.c_short)], ctypes.byref(connected)) < 0):
                    raise OSError('network connection properties are unavailable')
                if bytes(adapter.data) != bytes(expected.data) or not connected.value:
                    continue
                if _com_call(connection, 7, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(network)) < 0 or not network:
                    raise OSError('network profile is unavailable')
                category = ctypes.c_int()
                if _com_call(network, 18, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(category)) < 0:
                    raise OSError('network category is unavailable')
                if category.value != 1:  # NLM_NETWORK_CATEGORY_PRIVATE
                    return False
                found = True
            finally:
                _release(network)
                _release(connection)
    finally:
        _release(enumerator)
        _release(manager)
        ole.CoUninitialize()


def is_live_private_interface(address, prefix, index):
    """Confirm current IP/subnet and Private category without a subprocess."""
    if (not isinstance(address, str) or type(prefix) is not int or not 1 <= prefix <= 32
            or type(index) is not int or index <= 0):
        raise ValueError('invalid network interface')
    ipaddress.IPv4Address(address)
    return _current_ipv4(address, prefix, index) and _private_interface_category(index)


def is_home_subnet(address, prefix):
    """Accept RFC1918 subnets without treating a broad public route as local."""
    subnet = ipaddress.IPv4Network(f'{address}/{prefix}', strict=False)
    return any(subnet.subnet_of(private) for private in _PRIVATE_SUBNETS)


def _state_file(state):
    directory = Path(state).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError('existing private profile directory required')
    return directory / 'network.json'


def read_settings(state):
    """Return saved consent; absent settings are local-only by default.

    Unreadable or invalid settings fail closed instead of granting consent.
    The profile directory must already be private to the owning Windows user.
    """
    path = _state_file(state)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return dict(_DEFAULT)
    if (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or info.st_size > 4096):
        raise ValueError('unsafe network settings file')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError, ValueError) as error:
        raise ValueError('invalid network settings') from error
    if (not isinstance(data, dict) or set(data) != set(_DEFAULT) or type(data['schema']) is not int
            or data['schema'] != 1 or type(data['lan_enabled']) is not bool
            or type(data['recovery_paused']) is not bool
            or (data['lan_enabled'] and data['recovery_paused'])):
        raise ValueError('invalid network settings')
    return data


def _store(state, data):
    path = _state_file(state)
    # Refuse reparse points or malformed existing configuration before replace.
    read_settings(state)
    fd, filename = tempfile.mkstemp(prefix='.network-', dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _flush_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def set_lan_enabled(state, enabled, *, consent=False):
    """Persist explicit home-network consent, or disable it immediately.

    This records a preference only; connectivity must not start until a caller
    independently enforces Private-profile access and local-control separation.
    """
    if type(enabled) is not bool or (enabled and consent is not True):
        raise ValueError('explicit network consent is required')
    current = read_settings(state)
    current.update(lan_enabled=enabled, recovery_paused=False if enabled else current['recovery_paused'])
    _store(state, current)
    return current


def pause_for_recovery(state):
    """Durably revoke LAN consent before local credential recovery mutates data.

    Caller holds the owner lock. A failed write or flush raises before recovery
    may proceed; later startup cannot silently restore the previous consent.
    """
    current = read_settings(state)
    current.update(lan_enabled=False, recovery_paused=True)
    _store(state, current)
    return current


def private_ipv4_interfaces(*, timeout=10):
    """Discover IPv4 interfaces and subnet prefixes on Windows Private profiles.

    Query failures or unparseable data raise, so callers can pause sharing.
    These addresses are candidates, not proof of reachability or firewall policy.
    """
    if not 0 < timeout <= 60:
        raise ValueError('invalid network discovery timeout')
    system_root = os.environ.get('SystemRoot')
    if not system_root:
        raise OSError('Windows system directory is unavailable')
    executable = Path(system_root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    if not executable.is_file():
        raise OSError('Windows PowerShell is unavailable')
    encoded = base64.b64encode(_PROFILE_SCRIPT.encode('utf-16le')).decode('ascii')
    try:
        result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                capture_output=True, timeout=timeout, check=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(result.stdout.decode('utf-8-sig'))
    except (subprocess.SubprocessError, OSError, UnicodeError, ValueError) as error:
        raise OSError('could not determine the Windows network profile') from error
    if not isinstance(data, list):
        raise OSError('invalid Windows network profile response')
    output = []
    for row in data:
        if (not isinstance(row, dict) or set(row) != {'address', 'interface', 'prefix'}
                or type(row['interface']) is not int or row['interface'] <= 0
                or type(row['prefix']) is not int or not 1 <= row['prefix'] <= 32
                or not isinstance(row['address'], str)):
            raise OSError('invalid Windows network profile response')
        try:
            address = ipaddress.IPv4Address(row['address'])
        except ipaddress.AddressValueError as error:
            raise OSError('invalid Windows network profile address') from error
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            continue
        if not is_home_subnet(address, row['prefix']):
            continue
        candidate = (str(address), row['prefix'], row['interface'])
        if candidate not in output:
            output.append(candidate)
    return output


def private_ipv4_addresses(*, timeout=10):
    """Return usable address/interface pairs for presentation to the user."""
    return [(address, interface) for address, _prefix, interface in private_ipv4_interfaces(timeout=timeout)]


def check_loopback_port(port):
    """Test whether a local port can be bound without adopting its owner.

    This is a diagnostic only: the actual web listener must still bind the port
    and establish owned readiness, since another process can race this check.
    """
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('invalid port')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(('127.0.0.1', port))
        except OSError:
            return False
    return True
