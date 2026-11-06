"""Windows power, device and session notifications for the M0 scheduler.

The message thread only enqueues events. All scheduler and ledger work remains
on the scheduler's thread; durable running claims cover unexpected termination.
"""
import ctypes
from ctypes import wintypes
import queue
import threading
import uuid


_user = ctypes.WinDLL('user32', use_last_error=True)
_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _WNDCLASS(ctypes.Structure):
    _fields_ = [('style', wintypes.UINT), ('proc', _WNDPROC), ('class_extra', ctypes.c_int),
                ('window_extra', ctypes.c_int), ('instance', wintypes.HINSTANCE),
                ('icon', wintypes.HICON), ('cursor', wintypes.HANDLE),
                ('background', wintypes.HBRUSH), ('menu', wintypes.LPCWSTR),
                ('name', wintypes.LPCWSTR)]


_kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel.GetModuleHandleW.restype = wintypes.HINSTANCE
_user.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]
_user.RegisterClassW.restype = wintypes.ATOM
_user.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
_user.UnregisterClassW.restype = wintypes.BOOL
_user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
_user.CreateWindowExW.restype = wintypes.HWND
_user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user.DefWindowProcW.restype = _LRESULT
_user.DestroyWindow.argtypes = [wintypes.HWND]
_user.DestroyWindow.restype = wintypes.BOOL
_user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user.GetMessageW.restype = wintypes.BOOL
_user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user.DispatchMessageW.restype = _LRESULT
_user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user.PostMessageW.restype = wintypes.BOOL
_user.PostQuitMessage.argtypes = [ctypes.c_int]
_user.PostQuitMessage.restype = None


class WindowsLifecycle:
    """Receive broadcasts in a hidden top-level window without blocking Windows."""
    def __init__(self):
        self.events = queue.SimpleQueue()
        self.ready = threading.Event()
        self.thread = None
        self.window = None
        self.error = None
        self.name = 'CWNG-M0-' + uuid.uuid4().hex
        self.proc = _WNDPROC(self._window_proc)

    def _window_proc(self, window, message, wparam, lparam):
        if message == 0x218:  # WM_POWERBROADCAST
            if wparam == 4:  # PBT_APMSUSPEND
                self.events.put('suspend')
            elif wparam in (7, 18):  # PBT_APMRESUMESUSPEND / RESUMEAUTOMATIC
                self.events.put('resume')
            return 1
        if message == 0x219 and wparam in (0x8000, 0x8004, 0x8006):  # WM_DEVICECHANGE
            self.events.put('device')
            return 1
        if message == 0x11:  # WM_QUERYENDSESSION: already-durable work survives forced exit
            self.events.put('shutdown')
            return 1
        if message == 0x10:  # WM_CLOSE
            _user.DestroyWindow(window)
            return 0
        if message == 2:  # WM_DESTROY
            _user.PostQuitMessage(0)
            return 0
        return _user.DefWindowProcW(window, message, wparam, lparam)

    def _run(self):
        instance = _kernel.GetModuleHandleW(None)
        wc = _WNDCLASS()
        wc.proc, wc.instance, wc.name = self.proc, instance, self.name
        registered = False
        try:
            if not _user.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            self.window = _user.CreateWindowExW(0, self.name, '', 0, 0, 0, 0, 0, None, None, instance, None)
            if not self.window:
                raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set()
            message = wintypes.MSG()
            while True:
                result = _user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if not result:
                    break
                _user.DispatchMessageW(ctypes.byref(message))
        except BaseException as error:
            self.error = error
            self.events.put('error')
            self.ready.set()
        finally:
            self.window = None
            if registered:
                _user.UnregisterClassW(self.name, instance)

    def start(self):
        if self.thread:
            raise RuntimeError('lifecycle listener already started')
        self.thread = threading.Thread(target=self._run, name='cwng-native-lifecycle', daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise TimeoutError('Windows lifecycle listener did not start')
        if self.error:
            raise RuntimeError('Windows lifecycle listener failed') from self.error

    def drain(self):
        events = []
        while True:
            try:
                events.append(self.events.get_nowait())
            except queue.Empty:
                return events

    def close(self):
        if self.window and not _user.PostMessageW(self.window, 0x10, 0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if self.thread:
            self.thread.join(5)
            if self.thread.is_alive():
                raise TimeoutError('Windows lifecycle listener did not stop')
            self.thread = None
