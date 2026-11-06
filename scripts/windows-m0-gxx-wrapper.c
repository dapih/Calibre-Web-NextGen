/* Local M0 build-only wrapper: preserve arguments and map private header paths. */
#include <process.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

/* _wspawnv joins arguments without adding Windows command-line quoting. */
static wchar_t *quote(const wchar_t *arg) {
    size_t n = wcslen(arg), used = 0, slashes = 0;
    wchar_t *out = calloc(2 * n + 3, sizeof(*out));
    if (!out) return NULL;
    out[used++] = L'"';
    for (size_t i = 0; i < n; ++i) {
        if (arg[i] == L'\\') { ++slashes; continue; }
        if (arg[i] == L'"') {
            for (size_t j = 0; j < 2 * slashes + 1; ++j) out[used++] = L'\\';
        } else {
            for (size_t j = 0; j < slashes; ++j) out[used++] = L'\\';
        }
        slashes = 0;
        out[used++] = arg[i];
    }
    for (size_t j = 0; j < 2 * slashes; ++j) out[used++] = L'\\';
    out[used++] = L'"';
    out[used] = L'\0';
    return out;
}

int wmain(int argc, wchar_t **argv) {
    const wchar_t *compiler = _wgetenv(L"M0_REAL_GXX");
    const wchar_t *mapping = _wgetenv(L"M0_PREFIX_MAP");
    if (!compiler || !*compiler || !mapping || !*mapping) {
        fwprintf(stderr, L"M0 compiler wrapper configuration missing\n");
        return 2;
    }
    wchar_t **args = calloc((size_t)argc + 2, sizeof(*args));
    if (!args) return 2;
    args[0] = quote(compiler);
    args[1] = quote(mapping);
    for (int i = 1; i < argc; ++i) args[i + 1] = quote(argv[i]);
    for (int i = 0; i <= argc; ++i) {
        if (!args[i]) {
            for (int j = 0; j <= argc; ++j) free(args[j]);
            free(args);
            return 2;
        }
    }
    intptr_t result = _wspawnv(_P_WAIT, compiler, (const wchar_t *const *)args);
    if (result == -1) perror("M0 compiler wrapper failed");
    for (int i = 0; i <= argc; ++i) free(args[i]);
    free(args);
    return result == -1 ? 2 : (int)result;
}
