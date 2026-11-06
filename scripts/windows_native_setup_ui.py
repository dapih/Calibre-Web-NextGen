"""Local first-run setup window; credentials never appear in process arguments."""
import argparse
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

# Isolated Python deliberately removes the script directory from sys.path.
# Load only code beside this verified private entrypoint and the program root.
_scripts = Path(__file__).resolve().parent
sys.path[:0] = [str(_scripts), str(_scripts.parent)]
from windows_native_setup import SetupCancelled


class SetupWindow:
    def __init__(self, root, assets, *, copy_setup, new_setup, on_success=None):
        self.root, self.assets = root, dict(assets)
        self.copy_setup, self.new_setup = copy_setup, new_setup
        self.on_success = on_success
        self.events = queue.SimpleQueue()
        self.busy = False
        self.done = False
        self.cancel_event = threading.Event()
        self.mode = tk.StringVar(value='new')
        self.source = tk.StringVar()
        self.library = tk.StringVar()
        self.ingest = tk.StringVar()
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.confirm_password = tk.StringVar()
        self.port = tk.StringVar(value='8083')
        self.source_stopped = tk.BooleanVar(value=False)
        self.storage_not_synced = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='Choose locations and create an administrator.')

        root.title('Set up Calibre-Web-NextGen')
        root.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(root, padding=16)
        frame.grid(sticky='nsew')
        root.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text='Set up your library', font=('Segoe UI', 14, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w')
        choices = ttk.Frame(frame)
        choices.grid(row=1, column=0, columnspan=3, sticky='w', pady=(8, 4))
        ttk.Radiobutton(choices, text='Create a new library', variable=self.mode, value='new', command=self._mode_changed).pack(side='left')
        ttk.Radiobutton(choices, text='Copy a stopped Calibre library', variable=self.mode, value='copy', command=self._mode_changed).pack(side='left', padx=(16, 0))
        self.source_entry, self.source_button = self._path_row(frame, 2, 'Source library', self.source, 'source')
        self._path_row(frame, 3, 'New library location', self.library, 'library')
        self._path_row(frame, 4, 'Ingest folder', self.ingest, 'ingest')
        self._entry_row(frame, 5, 'Administrator name', self.username)
        self._entry_row(frame, 6, 'Password', self.password, secret=True)
        self._entry_row(frame, 7, 'Confirm password', self.confirm_password, secret=True)
        self._entry_row(frame, 8, 'Local port', self.port)
        self.stopped_check = ttk.Checkbutton(frame, text='The source library and other applications using it are stopped.', variable=self.source_stopped)
        self.stopped_check.grid(row=9, column=0, columnspan=3, sticky='w', pady=(8, 0))
        ttk.Checkbutton(frame, text='The selected folders are not synchronized by a cloud service.', variable=self.storage_not_synced).grid(row=10, column=0, columnspan=3, sticky='w')
        ttk.Label(frame, textvariable=self.status, wraplength=620).grid(row=11, column=0, columnspan=3, sticky='w', pady=(12, 8))
        actions = ttk.Frame(frame)
        actions.grid(row=12, column=0, columnspan=3, sticky='e')
        self.submit_button = ttk.Button(actions, text='Set up', command=self.submit)
        self.submit_button.pack(side='left')
        self.close_button = ttk.Button(actions, text='Cancel', command=self.close)
        self.close_button.pack(side='left', padx=(8, 0))
        self._mode_changed()
        self._poll_id = root.after(100, self._poll)

    def _entry_row(self, parent, row, label, variable, *, secret=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=3)
        entry = ttk.Entry(parent, textvariable=variable, show='*' if secret else '')
        entry.grid(row=row, column=1, columnspan=2, sticky='ew', pady=3)
        return entry

    def _path_row(self, parent, row, label, variable, kind):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=3)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky='ew', pady=3)
        button = ttk.Button(parent, text='Browse', command=lambda: self._browse(variable, kind))
        button.grid(row=row, column=2, padx=(8, 0))
        return entry, button

    def _browse(self, variable, kind):
        prompt = 'Choose a parent folder for the new Library folder' if kind == 'library' else 'Choose a folder'
        selected = filedialog.askdirectory(parent=self.root, title=prompt, mustexist=True)
        if selected:
            variable.set(str(Path(selected) / 'Library') if kind == 'library' else selected)

    def _mode_changed(self):
        enabled = self.mode.get() == 'copy'
        state = 'normal' if enabled else 'disabled'
        self.source_entry.configure(state=state)
        self.source_button.configure(state=state)
        self.stopped_check.configure(state=state)

    def submit(self):
        if self.busy or self.done:
            return
        if not self.storage_not_synced.get():
            self.status.set('Confirm that the selected folders are not cloud-synchronized.')
            return
        if self.mode.get() == 'copy' and not self.source_stopped.get():
            self.status.set('Stop all applications using the source library and confirm before copying.')
            return
        secret = self.password.get()
        if not secret or secret != self.confirm_password.get():
            self.status.set('Enter matching administrator passwords.')
            return
        try:
            port = int(self.port.get())
        except ValueError:
            self.status.set('Enter a valid local port number.')
            return
        arguments = {**self.assets, 'library': self.library.get().strip(), 'ingest': self.ingest.get().strip(),
                     'username': self.username.get(), 'password': secret, 'port': port, 'storage_not_synced': True,
                     'cancel_event': self.cancel_event}
        operation = self.new_setup
        if self.mode.get() == 'copy':
            arguments.update(source=self.source.get().strip(), source_stopped=True)
            operation = self.copy_setup
        self.password.set('')
        self.confirm_password.set('')
        self.cancel_event.clear()
        self.busy = True
        self.submit_button.configure(state='disabled')
        self.close_button.configure(text='Stop setup')
        self.status.set('Setting up your library. You can stop safely and resume later.')
        threading.Thread(target=self._run, args=(operation, arguments), daemon=True).start()

    def _run(self, operation, arguments):
        try:
            result = operation(**arguments)
        except SetupCancelled:
            self.events.put(('cancelled', None))
        except Exception as error:
            # Do not display a credential even if a dependency includes it in an error.
            message = str(error).replace(arguments['password'], '[redacted]')
            self.events.put(('error', message[:500]))
        else:
            self.events.put(('done', result))
        finally:
            arguments['password'] = ''

    def _poll(self):
        self._poll_id = None
        try:
            event, value = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.close_button.configure(state='normal')
            if event == 'cancelled':
                self.close()
                return
            if event == 'done':
                self.done = True
                self.close_button.configure(text='Close')
                self.status.set('Setup complete. Close this window and start CWNG.')
                if self.on_success:
                    self.on_success(value)
            else:
                self.submit_button.configure(state='normal')
                self.close_button.configure(text='Cancel')
                self.status.set('Setup could not finish: ' + value + '. Enter the password again to retry.')
        if self.root.winfo_exists():
            self._poll_id = self.root.after(100, self._poll)

    def close(self):
        if self.busy:
            self.cancel_event.set()
            self.close_button.configure(state='disabled')
            self.status.set('Stopping safely; unfinished setup can be resumed later.')
            return
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Open local CWNG first-run setup')
    for name in ('state', 'helper', 'runtime', 'calibre', 'kepubify', 'magick'):
        parser.add_argument('--' + name, required=True)
    args = vars(parser.parse_args(argv))
    # These values are process-local; provisioning independently checks ACLs.
    config = str(Path(args['state']) / 'config')
    os.environ['CWA_DB_PATH'] = config
    os.environ['CALIBRE_DBPATH'] = config
    from windows_native_first_run import complete_copy_setup
    from windows_native_new_library import complete_new_setup
    from windows_native_setup_db import setup_ownership
    # Hold ownership from before the window is displayed until it closes. The
    # same live token is passed into provisioning; it must not reacquire the lock.
    with setup_ownership(args['state'], args['helper']) as ownership:
        root = tk.Tk()
        SetupWindow(root, {**args, 'ownership': ownership},
                    copy_setup=complete_copy_setup, new_setup=complete_new_setup)
        root.mainloop()


if __name__ == '__main__':
    main()
