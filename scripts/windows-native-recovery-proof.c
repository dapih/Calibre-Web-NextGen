/* TEST-ONLY native old-payload recovery parent. Never ship or expose this
   standalone executable: the test harness authenticates its bytes externally.
   Product integration needs a reviewed bootstrap identity and recovery UI;
   first-release signing remains optional under the distribution policy. */
#ifndef CWNG_RECOVERY_PROOF
#error This native recovery entrypoint is for disposable tests only
#endif
#define CWNG_INVENTORY_EMBEDDED
#include "windows-native-recovery-inventory.c"
#include <shellapi.h>
#include <aclapi.h>
#include <sddl.h>

/* Match the profile's conservative owner/DACL rule before trusting anchors. */
static int access_checked_object(const wchar_t *path, int allow_readers) {
    HANDLE token = NULL; DWORD needed = 0; TOKEN_USER *user = NULL;
    PSID owner = NULL, allowed[3] = {NULL, NULL, NULL}; PACL dacl = NULL;
    PSECURITY_DESCRIPTOR descriptor = NULL; ACL_SIZE_INFORMATION size; int ok = 0;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) goto done;
    GetTokenInformation(token, TokenUser, NULL, 0, &needed);
    if (!needed || !(user = malloc(needed)) || !GetTokenInformation(token, TokenUser, user, needed, &needed)) goto done;
    if (GetNamedSecurityInfoW((LPWSTR)path, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                              &owner, NULL, &dacl, NULL, &descriptor) != ERROR_SUCCESS ||
        !owner || !dacl || !EqualSid(owner, user->User.Sid)) goto done;
    if (!ConvertStringSidToSidW(L"S-1-5-18", &allowed[0]) ||
        !ConvertStringSidToSidW(L"S-1-5-32-544", &allowed[1]) ||
        !ConvertStringSidToSidW(L"S-1-3-4", &allowed[2]) ||
        !GetAclInformation(dacl, &size, sizeof(size), AclSizeInformation)) goto done;
    for (DWORD i = 0; i < size.AceCount; ++i) {
        ACE_HEADER *header; ACCESS_ALLOWED_ACE *ace;
        if (!GetAce(dacl, i, (void **)&header)) goto done;
        if (header->AceType == ACCESS_DENIED_ACE_TYPE) continue;
        if (header->AceType != ACCESS_ALLOWED_ACE_TYPE) goto done;
        ace = (ACCESS_ALLOWED_ACE *)header;
        PSID sid = &ace->SidStart;
        if (!EqualSid(sid, user->User.Sid) && !EqualSid(sid, allowed[0]) &&
            !EqualSid(sid, allowed[1]) && !EqualSid(sid, allowed[2])) {
            DWORD writes = FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_EA | FILE_WRITE_ATTRIBUTES |
                           FILE_DELETE_CHILD | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_WRITE | GENERIC_ALL;
            if (!allow_readers || (ace->Mask & writes)) goto done;
        }
    }
    ok = 1;
done:
    for (int i = 0; i < 3; ++i) if (allowed[i]) LocalFree(allowed[i]);
    if (descriptor) LocalFree(descriptor);
    free(user); if (token) CloseHandle(token);
    return ok;
}
static int private_object(const wchar_t *path) { return access_checked_object(path, 0); }
static int secure_program_object(const wchar_t *path) { return access_checked_object(path, 1); }

typedef struct { const char *p, *end; int depth; } json_parser;
static void whitespace(json_parser *j) { while (j->p < j->end && strchr(" \t\r\n", *j->p)) ++j->p; }
static int json_string(json_parser *j, char *out, size_t cap) {
    size_t n = 0;
    if (j->p == j->end || *j->p++ != '"') return 0;
    while (j->p < j->end) {
        unsigned char c = (unsigned char)*j->p++;
        if (c == '"') { if (out) out[n] = 0; return 1; }
        if (c < 32) return 0;
        if (c == '\\') {
            if (j->p == j->end) return 0;
            char escaped = *j->p++;
            if (escaped == 'u') {
                for (int i = 0; i < 4; ++i) {
                    if (j->p == j->end || !((*j->p >= '0' && *j->p <= '9') ||
                        (*j->p >= 'a' && *j->p <= 'f') || (*j->p >= 'A' && *j->p <= 'F'))) return 0;
                    ++j->p;
                }
            } else if (!strchr("\"\\/bfnrt", escaped)) return 0;
            if (out) return 0; /* captured trust fields use plain ASCII */
            continue;
        }
        if (out) { if (c > 126 || n + 1 >= cap) return 0; out[n++] = (char)c; }
    }
    return 0;
}
static int json_value(json_parser *j);
static int json_compound(json_parser *j, char opening) {
    char close = opening == '{' ? '}' : ']';
    if (++j->depth > 32) return 0;
    ++j->p; whitespace(j);
    if (j->p < j->end && *j->p == close) { ++j->p; --j->depth; return 1; }
    for (;;) {
        if (opening == '{') {
            if (!json_string(j, NULL, 0)) return 0;
            whitespace(j); if (j->p == j->end || *j->p++ != ':') return 0;
            whitespace(j);
        }
        if (!json_value(j)) return 0;
        whitespace(j); if (j->p == j->end) return 0;
        if (*j->p == close) { ++j->p; --j->depth; return 1; }
        if (*j->p++ != ',') return 0;
        whitespace(j);
    }
}
static int json_value(json_parser *j) {
    whitespace(j); if (j->p == j->end) return 0;
    if (*j->p == '"') return json_string(j, NULL, 0);
    if (*j->p == '{' || *j->p == '[') return json_compound(j, *j->p);
    const char *begin = j->p;
    while (j->p < j->end && !strchr(",]} \t\r\n", *j->p)) ++j->p;
    size_t n = (size_t)(j->p - begin);
    if ((n == 4 && (!memcmp(begin, "true", 4) || !memcmp(begin, "null", 4))) ||
        (n == 5 && !memcmp(begin, "false", 5))) return 1;
    /* Journal numbers are nonnegative integers. Fail closed on other forms. */
    if (!n || (n > 1 && *begin == '0')) return 0;
    for (size_t i = 0; i < n; ++i) if (begin[i] < '0' || begin[i] > '9') return 0;
    return 1;
}

typedef struct { char *name; char value[160]; int seen; } field;
static int journal_fields(const char *bytes, size_t len, field *fields, size_t fields_count) {
    json_parser j = {bytes, bytes + len, 0}; char key[128]; whitespace(&j);
    if (j.p == j.end || *j.p++ != '{') return 0;
    whitespace(&j);
    for (;;) {
        if (!json_string(&j, key, sizeof(key))) return 0;
        whitespace(&j); if (j.p == j.end || *j.p++ != ':') return 0;
        whitespace(&j); field *target = NULL;
        for (size_t i = 0; i < fields_count; ++i) if (!strcmp(key, fields[i].name)) target = &fields[i];
        if (target) {
            if (target->seen++ || !json_string(&j, target->value, sizeof(target->value))) return 0;
        } else if (!json_value(&j)) return 0;
        whitespace(&j); if (j.p == j.end) return 0;
        if (*j.p == '}') { ++j.p; break; }
        if (*j.p++ != ',') return 0;
        whitespace(&j);
    }
    whitespace(&j); if (j.p != j.end) return 0;
    for (size_t i = 0; i < fields_count; ++i) if (fields[i].seen != 1) return 0;
    return 1;
}
static int ascii_name(const char *s, int version) {
    size_t n = strlen(s);
    if (!n || n > 126 || (version && strstr(s, ".."))) return 0;
    for (size_t i = 0; i < n; ++i)
        if (!((s[i] >= 'A' && s[i] <= 'Z') || (s[i] >= 'a' && s[i] <= 'z') ||
              (s[i] >= '0' && s[i] <= '9') || (version && strchr("._-", s[i])) || (!version && s[i] == '-'))) return 0;
    return !version || ((s[0] >= 'A' && s[0] <= 'Z') || (s[0] >= 'a' && s[0] <= 'z') || (s[0] >= '0' && s[0] <= '9'));
}
static int hash_matches(const wchar_t *path, const char *expected) {
    unsigned char digest[32];
    if (strlen(expected) != 64 || !hash_file(path, digest)) return 0;
    for (int i = 0; i < 32; ++i) {
        int a = hexvalue((wchar_t)expected[i * 2]), b = hexvalue((wchar_t)expected[i * 2 + 1]);
        if (a < 0 || b < 0 || digest[i] != (unsigned char)((a << 4) | b)) return 0;
    }
    return 1;
}
static int format_path(wchar_t *out, const wchar_t *a, const wchar_t *b) {
    return _snwprintf(out, 32768, L"%ls\\%ls", a, b) >= 0;
}
static int env_entry(wchar_t *block, size_t *used, const wchar_t *name, const wchar_t *value) {
    int written = _snwprintf(block + *used, 32768 - *used, L"%ls=%ls", name, value);
    if (written < 0 || (size_t)written + *used + 2 >= 32768) return 0;
    *used += (size_t)written + 1; block[*used] = 0; return 1;
}

int wmain(int argc, wchar_t **argv) {
    /* Explicit arguments: profile, program directory, private recovery dir.
       The caller independently authenticates this test binary before running. */
    if (argc != 4 || !safe_root(argv[1]) || !safe_root(argv[2]) || !safe_root(argv[3]) ||
        !private_object(argv[1]) || !private_object(argv[3])) return 2;
    wchar_t path[32768], payload[32768], inventory[32768], interpreter[32768], script[32768], command[32768];
    HANDLE gate = INVALID_HANDLE_VALUE, journal = INVALID_HANDLE_VALUE, program_handle = INVALID_HANDLE_VALUE;
    HANDLE versions_handle = INVALID_HANDLE_VALUE, payload_handle = INVALID_HANDLE_VALUE;
    int exit_code = 10, attributes_ready = 0;
    SECURITY_ATTRIBUTES sa = {sizeof(sa), NULL, TRUE}; BY_HANDLE_FILE_INFORMATION info;
    STARTUPINFOEXW si = {0}; PROCESS_INFORMATION pi = {0}; SIZE_T attr_size = 0;
    char raw[65537]; DWORD n; field fields[] = {{"phase", "", 0}, {"old_version", "", 0},
        {"inventory_version", "", 0}, {"checkpoint", "", 0}, {"inventory_sha256", "", 0}, {"bootstrap_sha256", "", 0}};
    if (!format_path(path, argv[1], L"bootstrap-maintenance.lock")) goto done;
    gate = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, &sa, OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (gate == INVALID_HANDLE_VALUE || !private_object(path) || GetFileType(gate) != FILE_TYPE_DISK || !GetFileInformationByHandle(gate, &info) ||
        info.nNumberOfLinks != 1 || (info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))) goto done;
    exit_code = 11;
    if (!format_path(path, argv[1], L"maintenance-journal.json")) goto done;
    journal = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                          FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (journal == INVALID_HANDLE_VALUE || !private_object(path) || GetFileType(journal) != FILE_TYPE_DISK ||
        !GetFileInformationByHandle(journal, &info) || info.nFileSizeHigh || info.nFileSizeLow > 65536 ||
        (info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) ||
        !ReadFile(journal, raw, 65537, &n, NULL) || n != info.nFileSizeLow ||
        !journal_fields(raw, n, fields, sizeof(fields) / sizeof(fields[0]))) goto done;
    exit_code = 12;
    if (strcmp(fields[0].value, "switching-no-resume") || strcmp(fields[1].value, fields[2].value) ||
        !ascii_name(fields[1].value, 1) || strlen(fields[3].value) != 43 ||
        strncmp(fields[3].value, "checkpoint-", 11) || !ascii_name(fields[3].value, 0)) goto done;
    for (int i = 11; i < 43; ++i) if (hexvalue((wchar_t)fields[3].value[i]) < 0) goto done;
    wchar_t version[160], checkpoint[160], expected[65];
    if (!MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, fields[1].value, -1, version, 160) ||
        !MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, fields[3].value, -1, checkpoint, 160) ||
        !MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, fields[4].value, -1, expected, 65)) goto done;
    if (!format_path(path, argv[2], L"versions") || !format_path(payload, path, version) ||
        !secure_program_object(argv[2]) || !secure_program_object(path) || !secure_program_object(payload)) goto done;
    program_handle = open_directory(argv[2]); versions_handle = open_directory(path); payload_handle = open_directory(payload);
    if (program_handle == INVALID_HANDLE_VALUE || versions_handle == INVALID_HANDLE_VALUE || payload_handle == INVALID_HANDLE_VALUE ||
        !SetHandleInformation(program_handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) ||
        !SetHandleInformation(versions_handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) ||
        !SetHandleInformation(payload_handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) ||
        !format_path(path, argv[2], L"CWNG.exe") || !secure_program_object(path) ||
        BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) < 0) goto done;
    int bootstrap_ok = hash_matches(path, fields[5].value);
    BCryptCloseAlgorithmProvider(algorithm, 0); algorithm = NULL;
    if (!bootstrap_ok || _snwprintf(inventory, 32768, L"%ls\\%ls.inventory", argv[3], checkpoint) < 0 ||
        !private_object(inventory)) goto done;
    exit_code = 13;
    wchar_t *verifier_args[] = {L"embedded", payload, inventory, expected};
    if (verify_inventory(4, verifier_args) != 0) goto done;
    exit_code = 14;
    if (!format_path(interpreter, payload, L"runtime\\Scripts\\python.exe") ||
        !format_path(script, payload, L"runtime\\windows_native_recovery_proof_adapter.py")) goto done;
    /* Paths are canonical local paths from the test harness; quotes are not
       valid Windows path characters, so this argument quoting is unambiguous. */
    if (_snwprintf(command, 32768, L"\"%ls\" -I -B \"%ls\" --state \"%ls\" --program-root \"%ls\" --recovery \"%ls\" --inherited-gate %llu",
                   interpreter, script, argv[1], argv[2], argv[3], (unsigned long long)(uintptr_t)gate) < 0) goto done;
    wchar_t system32[32768], windows[32768], private_runtime[32768], envpath[32768], envblock[32768];
    size_t env_used = 0; envblock[0] = envblock[1] = 0;
    DWORD system_length = GetSystemDirectoryW(system32, 32768);
    DWORD windows_length = GetWindowsDirectoryW(windows, 32768);
    if (!system_length || system_length >= 32768 || !windows_length || windows_length >= 32768 ||
        !format_path(private_runtime, payload, L"runtime") ||
        _snwprintf(envpath, 32768, L"%ls;%ls", private_runtime, system32) < 0 ||
        !env_entry(envblock, &env_used, L"APPDATA", argv[1]) ||
        !env_entry(envblock, &env_used, L"LOCALAPPDATA", argv[1]) ||
        !env_entry(envblock, &env_used, L"PATH", envpath) ||
        !env_entry(envblock, &env_used, L"SystemRoot", windows) ||
        !env_entry(envblock, &env_used, L"TEMP", argv[1]) ||
        !env_entry(envblock, &env_used, L"TMP", argv[1]) ||
        !env_entry(envblock, &env_used, L"USERPROFILE", argv[1]) || !SetDllDirectoryW(L"")) goto done;
    CloseHandle(journal); journal = INVALID_HANDLE_VALUE;
    InitializeProcThreadAttributeList(NULL, 1, 0, &attr_size);
    si.lpAttributeList = HeapAlloc(GetProcessHeap(), 0, attr_size);
    if (!si.lpAttributeList || !InitializeProcThreadAttributeList(si.lpAttributeList, 1, 0, &attr_size)) goto done;
    attributes_ready = 1;
    HANDLE inherited[] = {gate, program_handle, versions_handle, payload_handle};
    if (!UpdateProcThreadAttribute(si.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                   inherited, sizeof(inherited), NULL, NULL)) goto done;
    si.StartupInfo.cb = sizeof(si);
    if (!CreateProcessW(interpreter, command, NULL, NULL, TRUE,
                        EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                        envblock, payload, &si.StartupInfo, &pi)) goto done;
    exit_code = 15;
    CloseHandle(pi.hThread);
    if (WaitForSingleObject(pi.hProcess, INFINITE) == WAIT_OBJECT_0) {
        DWORD status; if (GetExitCodeProcess(pi.hProcess, &status) && status == 0) exit_code = 0;
    }
    CloseHandle(pi.hProcess);
done:
    if (si.lpAttributeList) {
        if (attributes_ready) DeleteProcThreadAttributeList(si.lpAttributeList);
        HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
    }
    if (journal != INVALID_HANDLE_VALUE) CloseHandle(journal);
    if (gate != INVALID_HANDLE_VALUE) CloseHandle(gate);
    if (payload_handle != INVALID_HANDLE_VALUE) CloseHandle(payload_handle);
    if (versions_handle != INVALID_HANDLE_VALUE) CloseHandle(versions_handle);
    if (program_handle != INVALID_HANDLE_VALUE) CloseHandle(program_handle);
    return exit_code;
}
