/* M0-only proof of creation-time Windows Job Object membership.
   This is not the production controller or crash-recovery implementation. */
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#ifndef PROC_THREAD_ATTRIBUTE_JOB_LIST
#define PROC_THREAD_ATTRIBUTE_JOB_LIST ProcThreadAttributeValue(13, FALSE, TRUE, FALSE)
#endif

static int fail(const wchar_t *step) {
    fwprintf(stderr, L"%ls failed: %lu\n", step, GetLastError());
    return 1;
}

static int marker(const wchar_t *dir, const wchar_t *name) {
    wchar_t path[32768], text[128];
    BOOL member = FALSE;
    if (!IsProcessInJob(GetCurrentProcess(), NULL, &member)) return fail(L"IsProcessInJob");
    if (!member) { fwprintf(stderr, L"Child executed outside a job\n"); return 1; }
    if (swprintf(path, 32768, L"%ls\\%ls.txt", dir, name) < 0) return 1;
    HANDLE f = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return fail(L"CreateFileW");
    int n = swprintf(text, 128, L"%lu\n", GetCurrentProcessId());
    DWORD written = 0;
    BOOL ok = n > 0 && WriteFile(f, text, (DWORD)(n * sizeof(wchar_t)), &written, NULL);
    CloseHandle(f);
    return !ok || written != (DWORD)(n * sizeof(wchar_t));
}

static int command(wchar_t *out, size_t size, const wchar_t *exe, const wchar_t *mode, const wchar_t *dir) {
    /* Windows paths cannot contain a double quote; no shell is involved. */
    return swprintf(out, size, L"\"%ls\" %ls \"%ls\"", exe, mode, dir) < 0;
}

static int verified_member(HANDLE job, const wchar_t *dir, const wchar_t *name) {
    wchar_t path[32768], text[128] = {0};
    if (swprintf(path, 32768, L"%ls\\%ls.txt", dir, name) < 0) return 0;
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return 0;
    DWORD n = 0;
    BOOL read = ReadFile(f, text, sizeof(text) - sizeof(wchar_t), &n, NULL);
    CloseHandle(f);
    if (!read || !n || n % sizeof(wchar_t)) return 0;
    DWORD pid = wcstoul(text, NULL, 10);
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) return 0;
    BOOL member = FALSE, ok = IsProcessInJob(process, job, &member);
    CloseHandle(process);
    return ok && member;
}

int wmain(int argc, wchar_t **argv) {
    if (argc != 3) { fwprintf(stderr, L"usage: job-proof parent|worker|grandchild DIRECTORY\n"); return 2; }
    wchar_t exe[32768], cmd[32768];
    DWORD len = GetModuleFileNameW(NULL, exe, 32768);
    if (!len || len == 32768) return fail(L"GetModuleFileNameW");
    if (!wcscmp(argv[1], L"grandchild")) {
        if (marker(argv[2], L"grandchild")) return 1;
        Sleep(30000);
        return 0;
    }
    if (!wcscmp(argv[1], L"worker")) {
        if (marker(argv[2], L"worker")) return 1;
        if (command(cmd, 32768, exe, L"grandchild", argv[2])) return 1;
        STARTUPINFOW si = {0}; PROCESS_INFORMATION pi = {0}; si.cb = sizeof(si);
        if (!CreateProcessW(exe, cmd, NULL, NULL, FALSE, 0, NULL, argv[2], &si, &pi)) return fail(L"CreateProcessW grandchild");
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
        Sleep(30000);
        return 0;
    }
    if (wcscmp(argv[1], L"parent")) return 2;

    HANDLE job = CreateJobObjectW(NULL, NULL);
    if (!job) return fail(L"CreateJobObjectW");
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = {0};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) { CloseHandle(job); return fail(L"SetInformationJobObject"); }
    SIZE_T bytes = 0;
    InitializeProcThreadAttributeList(NULL, 1, 0, &bytes);
    LPPROC_THREAD_ATTRIBUTE_LIST attrs = HeapAlloc(GetProcessHeap(), 0, bytes);
    if (!attrs) { CloseHandle(job); return fail(L"HeapAlloc"); }
    if (!InitializeProcThreadAttributeList(attrs, 1, 0, &bytes)) { HeapFree(GetProcessHeap(), 0, attrs); CloseHandle(job); return fail(L"InitializeProcThreadAttributeList"); }
    if (!UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST, &job, sizeof(job), NULL, NULL)) {
        DeleteProcThreadAttributeList(attrs); HeapFree(GetProcessHeap(), 0, attrs); CloseHandle(job); return fail(L"UpdateProcThreadAttribute job list");
    }
    STARTUPINFOEXW si = {0}; PROCESS_INFORMATION pi = {0}; si.StartupInfo.cb = sizeof(si); si.lpAttributeList = attrs;
    if (command(cmd, 32768, exe, L"worker", argv[2])) { DeleteProcThreadAttributeList(attrs); HeapFree(GetProcessHeap(), 0, attrs); CloseHandle(job); return 1; }
    BOOL ok = CreateProcessW(exe, cmd, NULL, NULL, FALSE, EXTENDED_STARTUPINFO_PRESENT, NULL, argv[2], &si.StartupInfo, &pi);
    DWORD error = ok ? ERROR_SUCCESS : GetLastError();
    DeleteProcThreadAttributeList(attrs); HeapFree(GetProcessHeap(), 0, attrs);
    if (!ok) { CloseHandle(job); SetLastError(error); return fail(L"CreateProcessW atomic job"); }
    CloseHandle(pi.hThread);
    BOOL verified = FALSE;
    for (int i = 0; i < 100; ++i) {
        if (verified_member(job, argv[2], L"worker") && verified_member(job, argv[2], L"grandchild")) { verified = TRUE; break; }
        if (WaitForSingleObject(pi.hProcess, 100) != WAIT_TIMEOUT) break;
    }
    if (!verified) { CloseHandle(pi.hProcess); CloseHandle(job); fwprintf(stderr, L"Could not verify both descendants in the created job\n"); return 1; }
    wchar_t verified_path[32768];
    if (swprintf(verified_path, 32768, L"%ls\\verified.txt", argv[2]) < 0) { CloseHandle(pi.hProcess); CloseHandle(job); return 1; }
    HANDLE signal = CreateFileW(verified_path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (signal == INVALID_HANDLE_VALUE) { CloseHandle(pi.hProcess); CloseHandle(job); return fail(L"CreateFileW verified marker"); }
    CloseHandle(signal);
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD result = 1; GetExitCodeProcess(pi.hProcess, &result);
    CloseHandle(pi.hProcess); CloseHandle(job);
    return (int)result;
}
