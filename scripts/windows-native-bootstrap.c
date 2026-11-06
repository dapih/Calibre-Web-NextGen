/* Stable per-user launcher for a versioned private Windows payload.
 * The bootstrap itself has no Python or shell dependency. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <shellapi.h>
#include <sddl.h>
#include <wchar.h>
#include <stdlib.h>
#include <stdio.h>

#define CAP 32768
#ifdef CWNG_INSTALLER_PROOF
#define DEFAULT_PROFILE L"CWNG-Installer-Proof"
#define INSTALL_GATE L"CWNG-Installer-Proof-install.lock"
#else
#define DEFAULT_PROFILE L"CWNG"
#define INSTALL_GATE L"CWNG-install.lock"
#endif

static void fail(const wchar_t *message) {
#ifdef CWNG_BOOTSTRAP_TEST
    (void)message;
#else
    MessageBoxW(NULL, message, L"CWNG for Windows", MB_OK | MB_ICONERROR);
#endif
}

/* Quote one argument using the Windows C runtime command-line rules. */
static int append_arg(wchar_t *dst, size_t *used, const wchar_t *arg) {
    size_t slashes = 0;
    if (*used + 3 >= CAP) return 0;
    if (*used) dst[(*used)++] = L' ';
    dst[(*used)++] = L'"';
    for (; *arg; ++arg) {
        if (*arg == L'\\') { ++slashes; continue; }
        if (*used + 2 * slashes + 3 >= CAP) return 0;
        while (slashes) { dst[(*used)++] = L'\\'; --slashes; }
        if (*arg == L'"') {
            /* Repeat the preceding backslashes before an embedded quote. */
            size_t i = *used;
            while (i && dst[i - 1] == L'\\') { dst[(*used)++] = L'\\'; --i; }
            dst[(*used)++] = L'\\';
        }
        dst[(*used)++] = *arg;
    }
    if (*used + 2 * slashes + 2 >= CAP) return 0;
    while (slashes--) { dst[(*used)++] = L'\\'; dst[(*used)++] = L'\\'; }
    dst[(*used)++] = L'"';
    dst[*used] = 0;
    return 1;
}

static int combine(wchar_t *out, const wchar_t *base, const wchar_t *suffix) {
    return swprintf(out, CAP, L"%ls\\%ls", base, suffix) > 0 && wcslen(out) < CAP - 1;
}

/* Never change the permissions of an existing directory. A newly created
   profile and its config directory inherit only owner/system/admin access. */
static int make_profile(const wchar_t *state) {
    SECURITY_ATTRIBUTES attributes = {0};
    PSECURITY_DESCRIPTOR descriptor = NULL;
    wchar_t config[CAP]; DWORD error;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
            L"D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
            SDDL_REVISION_1, &descriptor, NULL)) return 0;
    attributes.nLength = sizeof(attributes);
    attributes.lpSecurityDescriptor = descriptor;
    if (!CreateDirectoryW(state, &attributes)) {
        error = GetLastError(); LocalFree(descriptor);
        return error == ERROR_ALREADY_EXISTS;
    }
    if (!combine(config, state, L"config") || !CreateDirectoryW(config, &attributes)) {
        /* Remove only the new, still-empty directory; if anything appeared in
           it concurrently RemoveDirectory fails and leaves that data intact. */
        RemoveDirectoryW(state);
        LocalFree(descriptor); return 0;
    }
    LocalFree(descriptor);
    return 1;
}

/* Reject drive roots, network paths and junction/symlink ancestors before
   creating either profile directory or the maintenance gate. */
static int validate_state_path(const wchar_t *input, wchar_t *full) {
    DWORD n = GetFullPathNameW(input, CAP, full, NULL);
    if (!n || n >= CAP || n <= 3 || full[1] != L':' || full[2] != L'\\') return 0;
    while (n > 3 && (full[n - 1] == L'\\' || full[n - 1] == L'/')) full[--n] = 0;
    if (n <= 3) return 0;
    for (DWORD i = 3; i <= n; ++i) {
        if (full[i] != L'\\' && full[i] != 0) continue;
        wchar_t saved = full[i];
        full[i] = 0;
        DWORD attrs = GetFileAttributesW(full);
        DWORD error = GetLastError();
        full[i] = saved;
        if (attrs == INVALID_FILE_ATTRIBUTES) {
            if (i != n || (error != ERROR_FILE_NOT_FOUND && error != ERROR_PATH_NOT_FOUND)) return 0;
        } else if (!(attrs & FILE_ATTRIBUTE_DIRECTORY) || (attrs & FILE_ATTRIBUTE_REPARSE_POINT)) return 0;
    }
    return 1;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR cmd, int show) {
    (void)instance; (void)previous; (void)cmd; (void)show;
    wchar_t root[CAP], payload[CAP], version_file[CAP], version[128], path[CAP];
    wchar_t runtime[CAP], helper[CAP], calibre[CAP], kepubify[CAP], magick[CAP];
    wchar_t python[CAP], script[CAP], command[CAP] = L"";
    wchar_t systemroot[CAP], system32[CAP], envpath[CAP], default_state[CAP], validated_state[CAP];
    DWORD len, got; HANDLE file; size_t used = 0; int argc = 0, i;
    LPWSTR *argv = NULL; STARTUPINFOEXW si = {0}; PROCESS_INFORMATION pi = {0};
    const wchar_t *state = NULL;
    HANDLE gate = INVALID_HANDLE_VALUE;
    HANDLE install_gate = INVALID_HANDLE_VALUE;
    SECURITY_ATTRIBUTES gate_attributes = {sizeof(SECURITY_ATTRIBUTES), NULL, TRUE};
    BY_HANDLE_FILE_INFORMATION gate_info;
    SIZE_T attributes_size = 0;

    len = GetModuleFileNameW(NULL, root, CAP);
    if (!len || len >= CAP || !wcsrchr(root, L'\\')) { fail(L"Cannot determine the program directory."); return 1; }
    *wcsrchr(root, L'\\') = 0;
    /* Every supported launch holds this shared gate until its child exits.
       Uninstall holds it exclusively across removal, regardless of profile. */
    len = GetEnvironmentVariableW(L"LOCALAPPDATA", path, CAP);
    if (!len || len >= CAP || !combine(default_state, path, INSTALL_GATE)) goto error;
    install_gate = CreateFileW(default_state, GENERIC_READ, FILE_SHARE_READ, &gate_attributes,
                               OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (install_gate == INVALID_HANDLE_VALUE || GetFileType(install_gate) != FILE_TYPE_DISK ||
        !GetFileInformationByHandle(install_gate, &gate_info) ||
        (gate_info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) {
        fail(L"The installation is busy or its launch lock is unavailable."); goto error;
    }
    argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) goto error;
    for (i = 1; i < argc; ++i) {
        if (!wcscmp(argv[i], L"--state")) {
            if (i + 1 >= argc) goto error;
            state = argv[++i];
        } else if (!wcsncmp(argv[i], L"--state=", 8)) state = argv[i] + 8;
    }
#ifdef CWNG_INSTALLER_PROOF
    {
        len = GetEnvironmentVariableW(L"LOCALAPPDATA", path, CAP);
        if (!len || len >= CAP || !combine(default_state, path, DEFAULT_PROFILE)) goto error;
        if (state && _wcsicmp(state, default_state)) {
            fail(L"This disposable proof can only use its separate test profile."); goto error;
        }
        if (!state) state = default_state;
    }
#else
    if (!state) {
        len = GetEnvironmentVariableW(L"LOCALAPPDATA", path, CAP);
        if (!len || len >= CAP || !combine(default_state, path, DEFAULT_PROFILE)) goto error;
        state = default_state;
    }
#endif
    if (!state[0] || !state[1] || !state[2] || state[1] != L':' ||
        (state[2] != L'\\' && state[2] != L'/') ||
        !validate_state_path(state, validated_state) || !make_profile(validated_state)) {
        fail(L"The private profile directory could not be prepared."); goto error;
    }
    state = validated_state;
    /* Shared readers permit ordinary duplicate launches. Exclusive maintenance
       opens this same file without sharing and must wait for ALL launch trees.
       The child inherits this handle: killing this bootstrap cannot drop the
       gate while its Python controller continues to execute. */
    if (!combine(path, state, L"bootstrap-maintenance.lock")) goto error;
    gate = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, &gate_attributes, OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (gate == INVALID_HANDLE_VALUE || GetFileType(gate) != FILE_TYPE_DISK ||
        !GetFileInformationByHandle(gate, &gate_info) ||
        (gate_info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) {
        fail(L"The maintenance lock could not be acquired."); goto error;
    }
    /* A maintenance transaction has exclusive authority over both payload and
       data. Never interpret the activation record or start Python while it is
       unresolved, including when the journal is a reparse point. */
    if (!combine(path, state, L"maintenance-journal.json")) goto error;
    if (GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES) {
        fail(L"Maintenance recovery is required before CWNG can start."); goto error;
    }
    DWORD journal_error = GetLastError();
    if (journal_error != ERROR_FILE_NOT_FOUND && journal_error != ERROR_PATH_NOT_FOUND) {
        fail(L"The maintenance status could not be checked."); goto error;
    }
    if (!combine(version_file, root, L"active-version.txt")) return 1;
    file = CreateFileW(version_file, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file == INVALID_HANDLE_VALUE) { fail(L"The active program version is missing. Repair the installation."); return 1; }
    char bytes[128] = {0};
    if (!ReadFile(file, bytes, sizeof(bytes) - 1, &got, NULL) || !got || got >= sizeof(bytes) - 1) {
        CloseHandle(file); fail(L"The active program version is invalid."); return 1;
    }
    CloseHandle(file);
    while (got && (bytes[got-1] == '\n' || bytes[got-1] == '\r')) bytes[--got] = 0;
    if (!got || bytes[0] == '.') { fail(L"The active program version is invalid."); return 1; }
    for (DWORD n = 0; n < got; ++n) {
        char c = bytes[n];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '-' || c == '_')) {
            fail(L"The active program version is invalid."); return 1;
        }
        version[n] = (wchar_t)c;
    }
    version[got] = 0;
    if (wcsstr(version, L"..") || !combine(path, root, L"versions") || !combine(payload, path, version) ||
        !combine(runtime, payload, L"runtime") || !combine(helper, payload, L"cwng-native.dll") ||
        !combine(calibre, payload, L"calibre") || !combine(kepubify, payload, L"kepubify\\kepubify.exe") ||
        !combine(magick, payload, L"imagemagick") || !combine(python, runtime, L"pythonw.exe") ||
        !combine(script, payload, L"scripts\\windows_native_launcher.py")) {
        fail(L"The program path is invalid."); return 1;
    }
    if (!append_arg(command, &used, python) || !append_arg(command, &used, L"-I") || !append_arg(command, &used, L"-B") ||
        !append_arg(command, &used, script)) { fail(L"The launch command is too long."); goto error; }
    const wchar_t *names[] = {L"--helper", L"--runtime", L"--calibre", L"--kepubify", L"--magick"};
    const wchar_t *values[] = {helper, runtime, calibre, kepubify, magick};
    for (i = 0; i < 5; ++i)
        if (!append_arg(command, &used, names[i]) || !append_arg(command, &used, values[i])) goto error;
    if (!append_arg(command, &used, L"--state") || !append_arg(command, &used, state)) goto error;
    for (i = 1; i < argc; ++i) {
        if (!wcscmp(argv[i], L"--state")) { ++i; continue; }
        if (!wcsncmp(argv[i], L"--state=", 8)) continue;
        if (!append_arg(command, &used, argv[i])) goto error;
    }
    /* Sanitize before Python imports; retain the real LOCALAPPDATA for the
       per-user profile and opt-in startup entry. This changes this process only. */
    const wchar_t *poison[] = {L"PYTHONHOME", L"PYTHONPATH", L"PYTHONUSERBASE", L"QT_PLUGIN_PATH",
        L"QT_QPA_PLATFORM_PLUGIN_PATH", L"MAGICK_HOME", L"MAGICK_CONFIGURE_PATH",
        L"MAGICK_CODER_MODULE_PATH", L"MAGICK_FILTER_MODULE_PATH", L"MAGIC", L"TCL_LIBRARY", L"TK_LIBRARY"};
    for (i = 0; i < (int)(sizeof(poison) / sizeof(*poison)); ++i) SetEnvironmentVariableW(poison[i], NULL);
    len = GetEnvironmentVariableW(L"SystemRoot", systemroot, CAP);
    if (!len || len >= CAP || !combine(system32, systemroot, L"System32") ||
        swprintf(envpath, CAP, L"%ls;%ls;%ls", magick, calibre, system32) <= 0 ||
        !SetEnvironmentVariableW(L"PATH", envpath)) goto error;
    /* Explicit allowlist: never leak any other inherited handle into Python. */
    InitializeProcThreadAttributeList(NULL, 1, 0, &attributes_size);
    si.lpAttributeList = HeapAlloc(GetProcessHeap(), 0, attributes_size);
    if (!si.lpAttributeList || !InitializeProcThreadAttributeList(si.lpAttributeList, 1, 0, &attributes_size)) {
        if (si.lpAttributeList) HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
        si.lpAttributeList = NULL; goto error;
    }
    HANDLE inherited[] = {gate, install_gate};
    if (!UpdateProcThreadAttribute(si.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                   inherited, sizeof(inherited), NULL, NULL)) goto error;
    si.StartupInfo.cb = sizeof(si);
    if (!CreateProcessW(python, command, NULL, NULL, TRUE, EXTENDED_STARTUPINFO_PRESENT,
                        NULL, payload, &si.StartupInfo, &pi)) {
        fail(L"The private runtime could not be started. Repair the installation."); goto error;
    }
    CloseHandle(pi.hThread);
    DWORD child_exit = 1;
    DWORD waited = WaitForSingleObject(pi.hProcess, INFINITE);
    if (waited == WAIT_OBJECT_0 && !GetExitCodeProcess(pi.hProcess, &child_exit)) child_exit = 1;
    CloseHandle(pi.hProcess);
    DeleteProcThreadAttributeList(si.lpAttributeList);
    HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
    CloseHandle(gate); CloseHandle(install_gate); LocalFree(argv);
    if (waited != WAIT_OBJECT_0 || child_exit != 0) {
        fail(L"CWNG could not start or stopped unexpectedly. Check the local diagnostics.");
        return 1;
    }
    return 0;
error:
    if (si.lpAttributeList) {
        DeleteProcThreadAttributeList(si.lpAttributeList);
        HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
    }
    if (gate != INVALID_HANDLE_VALUE) CloseHandle(gate);
    if (install_gate != INVALID_HANDLE_VALUE) CloseHandle(install_gate);
    if (argv) LocalFree(argv);
    return 1;
}
