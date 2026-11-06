"""Small Windows notification-area adapter for the native controller.

The controller owns application state and consumes commands from ``commands``.
The Windows message thread never runs controller or storage operations.
"""
import ctypes
from ctypes import wintypes
import queue
import threading
import uuid


_user = ctypes.WinDLL('user32', use_last_error=True)
_shell = ctypes.WinDLL('shell32', use_last_error=True)
_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_result = ctypes.c_ssize_t
_wndproc = ctypes.WINFUNCTYPE(_result, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _WindowClass(ctypes.Structure):
    _fields_ = [('style', wintypes.UINT), ('proc', _wndproc), ('class_extra', ctypes.c_int),
                ('window_extra', ctypes.c_int), ('instance', wintypes.HINSTANCE),
                ('icon', wintypes.HICON), ('cursor', wintypes.HANDLE),
                ('background', wintypes.HBRUSH), ('menu', wintypes.LPCWSTR), ('name', wintypes.LPCWSTR)]


class _NotifyIcon(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('window', wintypes.HWND), ('identifier', wintypes.UINT),
                ('flags', wintypes.UINT), ('callback', wintypes.UINT), ('icon', wintypes.HICON),
                ('tip', wintypes.WCHAR * 128), ('state', wintypes.DWORD), ('state_mask', wintypes.DWORD),
                ('info', wintypes.WCHAR * 256), ('timeout', wintypes.UINT), ('title', wintypes.WCHAR * 64),
                ('info_flags', wintypes.DWORD), ('guid', ctypes.c_byte * 16), ('balloon_icon', wintypes.HICON)]


def _signature(dll, name, args, result):
    function = getattr(dll, name)
    function.argtypes, function.restype = args, result
    return function


_instance = _signature(_kernel, 'GetModuleHandleW', [wintypes.LPCWSTR], wintypes.HINSTANCE)
_register = _signature(_user, 'RegisterClassW', [ctypes.POINTER(_WindowClass)], wintypes.ATOM)
_unregister = _signature(_user, 'UnregisterClassW', [wintypes.LPCWSTR, wintypes.HINSTANCE], wintypes.BOOL)
_create = _signature(_user, 'CreateWindowExW', [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
    wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HWND)
_default = _signature(_user, 'DefWindowProcW', [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], _result)
_destroy = _signature(_user, 'DestroyWindow', [wintypes.HWND], wintypes.BOOL)
_post = _signature(_user, 'PostMessageW', [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL)
_get_message = _signature(_user, 'GetMessageW', [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL)
_dispatch = _signature(_user, 'DispatchMessageW', [ctypes.POINTER(wintypes.MSG)], _result)
_quit = _signature(_user, 'PostQuitMessage', [ctypes.c_int], None)
_load_icon = _signature(_user, 'LoadIconW', [wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HICON)
_notify = _signature(_shell, 'Shell_NotifyIconW', [wintypes.DWORD, ctypes.POINTER(_NotifyIcon)], wintypes.BOOL)
_popup = _signature(_user, 'CreatePopupMenu', [], wintypes.HMENU)
_append = _signature(_user, 'AppendMenuW', [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR], wintypes.BOOL)
_track = _signature(_user, 'TrackPopupMenu', [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, ctypes.c_void_p], wintypes.UINT)
_destroy_menu = _signature(_user, 'DestroyMenu', [wintypes.HMENU], wintypes.BOOL)
_cursor = _signature(_user, 'GetCursorPos', [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL)
_foreground = _signature(_user, 'SetForegroundWindow', [wintypes.HWND], wintypes.BOOL)
_register_message = _signature(_user, 'RegisterWindowMessageW', [wintypes.LPCWSTR], wintypes.UINT)


class NativeTray:
    """Receive tray actions without blocking the Windows message loop."""

    _CALLBACK = 0x8001
    _UPDATE = 0x8002
    _ITEMS = ((1, 'Open library', 'open'), (2, 'Start or retry', 'start'),
              (3, 'Stop', 'stop'), (4, 'Data locations', 'locations'),
              (5, 'Diagnostics', 'diagnostics'), (6, 'Home-network settings', 'network'),
              (7, 'Check for updates', 'updates'), (8, 'Exit', 'exit'),
              (9, 'Start at sign-in', 'autostart'),
              (10, 'Recover administrator', 'recover-password'))

    def __init__(self):
        self.commands = queue.SimpleQueue()
        self._ready = threading.Event()
        self._thread = None
        self._window = None
        self._error = None
        self._startup_error = None
        self._icon_present = False
        self._status = 'CWNG starting'
        self._class_name = 'CWNGTray-' + uuid.uuid4().hex
        self._callback = _wndproc(self._window_proc)
        self._taskbar_created = _register_message('TaskbarCreated')

    def start(self):
        if self._thread is not None:
            raise RuntimeError('tray already started')
        self._thread = threading.Thread(target=self._run, name='cwng-tray', daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise TimeoutError('tray did not start')
        if self._startup_error:
            raise RuntimeError('tray failed to start') from self._startup_error

    def set_status(self, status):
        if not isinstance(status, str) or not status.strip():
            raise ValueError('status must be a non-empty string')
        # NOTIFYICONDATAW.szTip holds 128 UTF-16 code units including NUL.
        # Truncate bytes and discard an incomplete surrogate pair, rather than
        # slicing Python characters (which may each use two code units).
        self._status = status.encode('utf-16-le', errors='replace')[:254].decode('utf-16-le', errors='ignore')
        if self._window and not _post(self._window, self._UPDATE, 0, 0):
            raise ctypes.WinError(ctypes.get_last_error())

    @property
    def error(self):
        """Return the last message-loop error, if a tray-error is reported."""
        return self._error

    def close(self):
        if self._window and not _post(self._window, 0x10, 0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if self._thread:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise TimeoutError('tray did not stop')

    def _icon(self, operation):
        data = _NotifyIcon()
        data.size, data.window, data.identifier = ctypes.sizeof(data), self._window, 1
        data.flags, data.callback, data.icon = 0x7, self._CALLBACK, _load_icon(None, ctypes.c_void_p(32512))
        data.tip = self._status
        if not _notify(operation, ctypes.byref(data)):
            raise OSError('notification area rejected icon operation')
        self._icon_present = operation != 2

    def _menu(self):
        menu = _popup()
        if not menu:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _append(menu, 0x1, 0, self._status)  # disabled status label
            _append(menu, 0x800, 0, None)
            for identifier, label, _ in self._ITEMS:
                if not _append(menu, 0, identifier, label):
                    raise ctypes.WinError(ctypes.get_last_error())
            point = wintypes.POINT()
            if not _cursor(ctypes.byref(point)):
                raise ctypes.WinError(ctypes.get_last_error())
            _foreground(self._window)
            selected = _track(menu, 0x100 | 0x2, point.x, point.y, 0, self._window, None)
            for identifier, _, command in self._ITEMS:
                if selected == identifier:
                    self.commands.put(command)
                    break
        finally:
            _destroy_menu(menu)

    def _window_proc(self, window, message, wparam, lparam):
        try:
            if message == self._taskbar_created:
                self._icon(0)  # Explorer recreated its taskbar.
                self._error = None
                self.commands.put('tray-recovered')
                return 0
            if message == self._CALLBACK:
                if lparam in (0x205, 0x7b):  # right click / context menu
                    self._menu()
                elif lparam == 0x203:  # double click
                    self.commands.put('open')
                return 0
            if message == self._UPDATE:
                self._icon(1 if self._icon_present else 0)
                self._error = None
                return 0
            if message == 0x10:
                _destroy(window)
                return 0
            if message == 2:
                _quit(0)
                return 0
        except Exception as error:
            self._error = error
            self._icon_present = False
            self.commands.put('tray-error')
        return _default(window, message, wparam, lparam)

    def _run(self):
        instance, registered = _instance(None), False
        try:
            wc = _WindowClass()
            wc.proc, wc.instance, wc.name = self._callback, instance, self._class_name
            if not _register(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            self._window = _create(0, self._class_name, 'CWNG', 0, 0, 0, 0, 0, None, None, instance, None)
            if not self._window:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                self._icon(0)
            except OSError as error:
                # Explorer may not be running yet. Keep listening for its
                # TaskbarCreated broadcast so the icon can be registered later.
                self._error = error
                self.commands.put('tray-error')
            self._ready.set()
            message = wintypes.MSG()
            while True:
                result = _get_message(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    break
                _dispatch(ctypes.byref(message))
        except Exception as error:
            self._error = error
            if self._ready.is_set():
                self.commands.put('tray-error')
            else:
                self._startup_error = error
            self._ready.set()
        finally:
            if self._icon_present:
                try:
                    self._icon(2)
                except OSError:
                    pass
            if self._window:
                _destroy(self._window)
                self._window = None
            if registered:
                _unregister(self._class_name, instance)
