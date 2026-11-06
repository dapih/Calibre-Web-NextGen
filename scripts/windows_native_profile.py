"""Fail-closed validation of an existing private Windows profile directory."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import os


def _require_private_path(path):
    """Require owner access and reject DACL grants to other Windows principals.

    SYSTEM and the local Administrators group are allowed, as Windows profile
    security normally relies on these principals. This checks the directory's
    effective DACL; it does not create or repair permissions.
    """
    path = Path(path)
    if not path.is_absolute() or not path.exists():
        raise ValueError('private profile path must exist and be absolute')
    if path.stat(follow_symlinks=False).st_file_attributes & 0x400:
        raise ValueError('private profile cannot be a reparse point')
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    ptr = ctypes.c_void_p
    kernel.GetCurrentProcess.restype = ptr
    kernel.CloseHandle.argtypes = [ptr]
    kernel.LocalFree.argtypes = [ptr]
    adv.OpenProcessToken.argtypes = [ptr, wintypes.DWORD, ctypes.POINTER(ptr)]
    adv.GetTokenInformation.argtypes = [ptr, ctypes.c_int, ptr, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                                         ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr),
                                         ctypes.POINTER(ptr), ctypes.POINTER(ptr)]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.EqualSid.argtypes = [ptr, ptr]
    adv.EqualSid.restype = wintypes.BOOL
    adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ptr)]
    adv.GetAclInformation.argtypes = [ptr, ptr, wintypes.DWORD, ctypes.c_int]
    adv.GetAce.argtypes = [ptr, wintypes.DWORD, ctypes.POINTER(ptr)]
    token, owner, dacl, descriptor = ptr(), ptr(), ptr(), ptr()
    allowed = []
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        needed = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise ctypes.WinError(ctypes.get_last_error())
        token_data = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, token_data, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        current = ctypes.cast(token_data, ctypes.POINTER(ptr)).contents
        code = adv.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                         ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if code:
            raise ctypes.WinError(code)
        if not owner.value or not dacl.value or not adv.EqualSid(owner, current):
            raise PermissionError('profile must be owned by this user and have a restricted DACL')
        # OWNER RIGHTS refers to the checked owner of each individual object.
        for text in ('S-1-5-18', 'S-1-5-32-544', 'S-1-3-4'):
            sid = ptr()
            if not adv.ConvertStringSidToSidW(text, ctypes.byref(sid)):
                raise ctypes.WinError(ctypes.get_last_error())
            allowed.append(sid)

        class AclSize(ctypes.Structure):
            _fields_ = [('count', wintypes.DWORD), ('in_use', wintypes.DWORD), ('free', wintypes.DWORD)]

        size = AclSize()
        if not adv.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        for index in range(size.count):
            ace = ptr()
            if not adv.GetAce(dacl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            header = (ctypes.c_ubyte * 4).from_address(ace.value)
            if header[0] == 1:  # Deny ACE cannot add access.
                continue
            if header[0] != 0:
                raise PermissionError('unsupported profile ACL entry')
            sid = ptr(ace.value + 8)
            if not any(adv.EqualSid(sid, candidate) for candidate in (current, *allowed)):
                raise PermissionError('profile is accessible by another principal')
        return path.resolve(strict=True)
    finally:
        for sid in allowed:
            kernel.LocalFree(sid)
        if descriptor.value:
            kernel.LocalFree(descriptor)
        if token.value:
            kernel.CloseHandle(token)


def require_private_directory(path):
    """Verify the profile and every existing descendant, without following links.

    Inherit-only grants are checked because new files can inherit them. Existing
    descendants can have independently changed ACLs and must also be checked.
    """
    root = Path(path)
    if not root.is_dir():
        raise ValueError('private profile must be an existing directory')
    _require_private_path(root)
    def fail(error):
        raise error
    for current, directories, files in os.walk(root, followlinks=False, onerror=fail):
        for name in (*directories, *files):
            _require_private_path(Path(current) / name)
    return root.resolve(strict=True)
