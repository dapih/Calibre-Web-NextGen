"""Nonblocking, explicit user controls for native home-network access."""
import ctypes
from pathlib import Path
import threading

from windows_native_network import private_ipv4_addresses, read_settings, set_lan_enabled


def _message(text, flags=0):
    return ctypes.windll.user32.MessageBoxW(None, text, 'CWNG home network', flags)


class NetworkControls:
    """Run discovery and consent dialogs outside the controller event loop.

    The controller holds the profile owner lock until ``close`` returns. The
    background dialog cannot persist consent after shutdown has begun.
    """

    def __init__(self, state, port, *, message=_message, discover=private_ipv4_addresses,
                 listener_status=lambda: None):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid local port')
        self.state = Path(state)
        self.port = port
        self.message, self.discover = message, discover
        self.listener_status = listener_status
        self._stop = threading.Event()
        self._dialog = threading.Lock()
        self._write = threading.Lock()

    def _run(self):
        try:
            settings = read_settings(self.state)
            current = ('Paused after password recovery' if settings['recovery_paused'] else
                       'On, when a Private network is available' if settings['lan_enabled'] else 'Off')
            try:
                addresses = self.discover()
            except (OSError, ValueError):
                addresses = []
                notice = 'Windows could not verify the current network profile. Sharing stays paused.'
            else:
                if addresses:
                    urls = '\n'.join(f'http://{address}:{self.port}/' for address, _index in addresses)
                    notice = ('Potential device addresses on this Private network:\n' + urls +
                              '\nWindows Firewall or network rules may still block access.')
                else:
                    notice = 'No supported Private network is currently available. Sharing stays paused.'
            try:
                status = self.listener_status()
            except Exception:
                status = None
            if status is None:
                notice += '\nListener status is unavailable; the server may be stopped.'
            elif status['listening']:
                bound = '\n'.join(f'http://{address}:{self.port}/' for address in status['listening'])
                notice += ('\n\nListener bound on:\n' + bound +
                           '\nActual device reachability and firewall permission are not established.')
            elif status['error']:
                notice += '\nThe device listener is not active: ' + status['error'][:240]
            else:
                notice += '\nThe device listener is not currently active.'
            if self._stop.is_set():
                return
            choice = self.message('Home-network access: ' + current + '\n\n' + notice +
                                  '\n\nHome-network HTTP is not encrypted; passwords and reading data '
                                  'can be observed by others on the network. Use only a trusted network.'
                                  '\n\nYes: allow access on a Private home network.'
                                  '\nNo: disable home-network access.'
                                  '\nCancel: keep the current setting.', 0x3 | 0x20)
            if choice not in (6, 7):
                return
            with self._write:
                if self._stop.is_set():
                    return
                set_lan_enabled(self.state, choice == 6, consent=choice == 6)
            if not self._stop.is_set():
                self.message('Home-network permission saved. Access starts only on a verified Private network; '
                             'firewall permission may still be required.' if choice == 6 else
                             'Home-network access is disabled.')
        except Exception as error:
            if not self._stop.is_set():
                self.message('Unable to change home-network access: ' + str(error), 0x10)
        finally:
            self._dialog.release()

    def open(self):
        if not self._stop.is_set() and self._dialog.acquire(blocking=False):
            threading.Thread(target=self._run, name='cwng-network-dialog', daemon=True).start()

    def close(self):
        # Synchronize with the short atomic state write, not with any modal UI.
        with self._write:
            self._stop.set()
