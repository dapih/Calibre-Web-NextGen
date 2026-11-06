/* Experimental inventory verifier. It does not launch or recover anything.
   The expected inventory digest must come from an independently trusted source.
   Caller must fence all cooperating payload writers for the entire operation. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <bcrypt.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef struct { wchar_t *name; int directory; unsigned char hash[32]; } entry;
static entry *items;
static size_t count, capacity;
static BCRYPT_ALG_HANDLE algorithm;
#ifdef CWNG_INVENTORY_EMBEDDED
static int secure_program_object(const wchar_t *path);
#endif

/* This prototype accepts local absolute paths only. Inspection failures or a
   reparse point at any component prevent use as a recovery verifier. */
static int safe_root(const wchar_t *path) {
    wchar_t full[32768]; DWORD n = GetFullPathNameW(path, 32768, full, NULL);
    if (!n || n >= 32768 || n < 4 || wcslen(path) < 4 || path[1] != L':' || path[2] != L'\\' ||
        full[1] != L':' || full[2] != L'\\' || _wcsicmp(path, full)) return 0;
    for (DWORD i = 3; i <= n; ++i) {
        if (full[i] != L'\\' && full[i] != 0) continue;
        wchar_t saved = full[i]; full[i] = 0;
        DWORD attr = GetFileAttributesW(full);
        full[i] = saved;
        if (attr == INVALID_FILE_ATTRIBUTES || !(attr & FILE_ATTRIBUTE_DIRECTORY) ||
            (attr & FILE_ATTRIBUTE_REPARSE_POINT)) return 0;
    }
    return 1;
}

static int safe_component(const wchar_t *name) {
    size_t n = wcslen(name);
    if (!n || name[n - 1] == L'.' || name[n - 1] == L' ') return 0;
    for (size_t i = 0; i < n; ++i)
        if (name[i] < 32 || wcschr(L"<>:\"/\\|?*", name[i])) return 0;
    return 1;
}

static int no_named_streams(const wchar_t *path) {
    WIN32_FIND_STREAM_DATA data;
    HANDLE search = FindFirstStreamW(path, FindStreamInfoStandard, &data, 0);
    if (search == INVALID_HANDLE_VALUE) return GetLastError() == ERROR_HANDLE_EOF;
    do {
        if (wcscmp(data.cStreamName, L"::$DATA")) { FindClose(search); return 0; }
    } while (FindNextStreamW(search, &data));
    DWORD error = GetLastError(); FindClose(search);
    return error == ERROR_HANDLE_EOF;
}

static int hash_file(const wchar_t *path, unsigned char out[32]) {
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    BY_HANDLE_FILE_INFORMATION before, after;
    BCRYPT_HASH_HANDLE hash = NULL;
    unsigned char buffer[65536]; DWORD n; int ok = 0;
    if (f == INVALID_HANDLE_VALUE) return 0;
    if (GetFileType(f) != FILE_TYPE_DISK || !GetFileInformationByHandle(f, &before) ||
        before.nNumberOfLinks != 1 ||
        (before.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))) goto done;
    if (BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0) < 0) goto done;
    do {
        if (!ReadFile(f, buffer, sizeof(buffer), &n, NULL)) goto done;
        if (n && BCryptHashData(hash, buffer, n, 0) < 0) goto done;
    } while (n);
    if (!GetFileInformationByHandle(f, &after) ||
        before.dwVolumeSerialNumber != after.dwVolumeSerialNumber ||
        before.nFileIndexHigh != after.nFileIndexHigh || before.nFileIndexLow != after.nFileIndexLow ||
        before.nFileSizeHigh != after.nFileSizeHigh || before.nFileSizeLow != after.nFileSizeLow ||
        CompareFileTime(&before.ftLastWriteTime, &after.ftLastWriteTime) ||
        BCryptFinishHash(hash, out, 32, 0) < 0) goto done;
    ok = 1;
done:
    if (hash) BCryptDestroyHash(hash);
    CloseHandle(f); return ok;
}

static int add_item(const wchar_t *name, int directory, const wchar_t *full) {
    if (count >= 1000000) return 0;
    if (count == capacity) {
        size_t next = capacity ? capacity * 2 : 256;
        entry *p = realloc(items, next * sizeof(*items));
        if (!p) return 0;
        items = p; capacity = next;
    }
    entry *item = &items[count];
    item->name = _wcsdup(name); item->directory = directory;
    memset(item->hash, 0, sizeof(item->hash));
    if (!item->name || (!directory && !hash_file(full, item->hash))) {
        free(item->name); return 0;
    }
    ++count; return 1;
}

static HANDLE open_directory(const wchar_t *path) {
    HANDLE f = CreateFileW(path, FILE_READ_ATTRIBUTES, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                           FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    BY_HANDLE_FILE_INFORMATION info;
    if (f == INVALID_HANDLE_VALUE) return f;
    if (GetFileType(f) != FILE_TYPE_DISK || !GetFileInformationByHandle(f, &info) ||
        !(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
        CloseHandle(f); return INVALID_HANDLE_VALUE;
    }
    return f;
}

static int walk(const wchar_t *root, const wchar_t *rel, int depth) {
    wchar_t *pattern = NULL, *full = NULL, *child = NULL; WIN32_FIND_DATAW data;
    HANDLE search = INVALID_HANDLE_VALUE; DWORD last; int ok = 0;
    if (depth > 256) return 0;
    pattern = malloc(32768 * sizeof(wchar_t)); full = malloc(32768 * sizeof(wchar_t));
    child = malloc(32768 * sizeof(wchar_t));
    if (!pattern || !full || !child || _snwprintf(pattern, 32768, L"%ls%ls%ls\\*", root, *rel ? L"\\" : L"", rel) < 0) goto done;
    search = FindFirstFileW(pattern, &data);
    if (search == INVALID_HANDLE_VALUE) { ok = GetLastError() == ERROR_FILE_NOT_FOUND; goto done; }
    do {
        if (!wcscmp(data.cFileName, L".") || !wcscmp(data.cFileName, L"..")) continue;
        if (!safe_component(data.cFileName)) goto done;
        if (data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) goto done;
        if (_snwprintf(child, 32768, L"%ls%ls%ls", rel, *rel ? L"\\" : L"", data.cFileName) < 0 ||
            _snwprintf(full, 32768, L"%ls\\%ls", root, child) < 0) goto done;
        int directory = !!(data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY);
        HANDLE held = directory ? open_directory(full) : INVALID_HANDLE_VALUE;
        if ((directory && held == INVALID_HANDLE_VALUE) ||
#ifdef CWNG_INVENTORY_EMBEDDED
            !secure_program_object(full) ||
#endif
            !no_named_streams(full) || !add_item(child, directory, full) ||
            (directory && !walk(root, child, depth + 1))) {
            if (held != INVALID_HANDLE_VALUE) CloseHandle(held);
            goto done;
        }
        if (held != INVALID_HANDLE_VALUE) CloseHandle(held);
    } while (FindNextFileW(search, &data));
    last = GetLastError(); ok = last == ERROR_NO_MORE_FILES;
done:
    if (search != INVALID_HANDLE_VALUE) FindClose(search);
    free(pattern); free(full); free(child); return ok;
}

static int compare_entry(const void *a, const void *b) {
    return wcscmp(((const entry *)a)->name, ((const entry *)b)->name);
}
static int hash_bytes(const unsigned char *data, ULONG size, unsigned char out[32]) {
    BCRYPT_HASH_HANDLE hash = NULL;
    int ok = BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0) >= 0 &&
             BCryptHashData(hash, (PUCHAR)data, size, 0) >= 0 &&
             BCryptFinishHash(hash, out, 32, 0) >= 0;
    if (hash) BCryptDestroyHash(hash);
    return ok;
}
static int hexvalue(wchar_t c) {
    if (c >= L'0' && c <= L'9') return c - L'0';
    if (c >= L'a' && c <= L'f') return c - L'a' + 10;
    return -1;
}
static uint32_t read32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static int verify_inventory(int argc, wchar_t **argv) {
    unsigned char expected[32], digest[32], *data = NULL; FILE *f = NULL;
    long length; size_t at = 12; int ok = 0; HANDLE root_handle = INVALID_HANDLE_VALUE;
    if (argc != 4 || wcslen(argv[3]) != 64) return 2;
    for (int i = 0; i < 32; ++i) {
        int a = hexvalue(argv[3][i * 2]), b = hexvalue(argv[3][i * 2 + 1]);
        if (a < 0 || b < 0) return 2;
        expected[i] = (unsigned char)((a << 4) | b);
    }
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) < 0) return 2;
    if (!safe_root(argv[1])) goto done;
    root_handle = open_directory(argv[1]);
    if (root_handle == INVALID_HANDLE_VALUE || !no_named_streams(argv[1]) ||
        !hash_file(argv[2], digest) || memcmp(digest, expected, 32)) goto done;
    f = _wfopen(argv[2], L"rb");
    if (!f || fseek(f, 0, SEEK_END) || (length = ftell(f)) < 12 || length > 128 * 1024 * 1024 || fseek(f, 0, SEEK_SET)) goto done;
    data = malloc((size_t)length);
    if (!data || fread(data, 1, (size_t)length, f) != (size_t)length || memcmp(data, "CWNGINV1", 8)) goto done;
    /* Authenticate the actual bytes being parsed, not a second file read. */
    if (!hash_bytes(data, (ULONG)length, digest) || memcmp(digest, expected, 32)) goto done;
    if (!walk(argv[1], L"", 0)) goto done;
    if (count) qsort(items, count, sizeof(*items), compare_entry);
    if (read32(data + 8) != count) goto done;
    for (size_t i = 0; i < count; ++i) {
        if (at + 5 > (size_t)length) goto done;
        uint32_t chars = read32(data + at); unsigned int directory = data[at + 4]; at += 5;
        if (chars > 32767 || directory > 1 || chars != wcslen(items[i].name) ||
            (size_t)chars * 2 + 32 > (size_t)length - at) goto done;
        for (uint32_t j = 0; j < chars; ++j) {
            unsigned int c = data[at + j * 2] | (unsigned int)data[at + j * 2 + 1] << 8;
            if (c != (unsigned int)items[i].name[j]) goto done;
        }
        at += (size_t)chars * 2;
        if ((int)directory != items[i].directory || memcmp(data + at, items[i].hash, 32)) goto done;
        at += 32;
    }
    if (at != (size_t)length) goto done;
    /* Catch mutations during enumeration; the caller still needs the gate and
       must not treat this rescan as protection against uncooperative writers. */
    size_t first_count = count;
    if (!walk(argv[1], L"", 0) || count != first_count * 2) goto done;
    if (first_count) qsort(items + first_count, first_count, sizeof(*items), compare_entry);
    for (size_t i = 0; i < first_count; ++i) {
        entry *a = &items[i], *b = &items[first_count + i];
        if (wcscmp(a->name, b->name) || a->directory != b->directory || memcmp(a->hash, b->hash, 32)) goto done;
    }
    if (no_named_streams(argv[1])) ok = 1;
done:
    if (root_handle != INVALID_HANDLE_VALUE) CloseHandle(root_handle);
    if (f) fclose(f);
    free(data);
    for (size_t i = 0; i < count; ++i) free(items[i].name);
    free(items); items = NULL; count = capacity = 0;
    BCryptCloseAlgorithmProvider(algorithm, 0); algorithm = NULL;
    return ok ? 0 : 1;
}

#ifndef CWNG_INVENTORY_EMBEDDED
int wmain(int argc, wchar_t **argv) { return verify_inventory(argc, argv); }
#endif
