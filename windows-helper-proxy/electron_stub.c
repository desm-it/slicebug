/* electron.exe - tiny Windows proxy for SliceBug's device helper.
 *
 * The current Windows CricutDevice.exe only runs its bridge protocol when its
 * parent process is named electron.exe and it sits under a
 * node_modules/@cricut/device-common layout. SliceBug stages that layout in its
 * cache (slicebug/cricut/windows_helper_proxy.py) and launches this stub (named
 * electron.exe) as the helper's parent. The stub spawns the real helper located
 * next to it and relays stdin/stdout/stderr verbatim, so SliceBug's bridge
 * protocol is unchanged. This mirrors the pump logic SliceBug previously ran
 * through a bundled Python interpreter, but needs no Python runtime at launch.
 *
 * Build (x64): cl /nologo /O1 /MT electron_stub.c /Feelectron.exe
 */
#include <windows.h>
#include <wchar.h>

#define PUMP_BUFFER 65536

typedef struct {
    HANDLE source;
    HANDLE target;
    BOOL close_target;
} Pump;

/* Copy bytes from source to target until EOF/error. The stdin pump closes the
 * child's stdin on EOF (close_target) so the helper sees end-of-input, matching
 * the original Python bridge pump. */
static DWORD WINAPI pump(LPVOID param) {
    Pump io = *(Pump *)param;
    unsigned char buffer[PUMP_BUFFER];
    DWORD read_count, written, offset;

    while (ReadFile(io.source, buffer, PUMP_BUFFER, &read_count, NULL) && read_count) {
        for (offset = 0; offset < read_count; offset += written) {
            if (!WriteFile(io.target, buffer + offset, read_count - offset, &written,
                           NULL)
                || written == 0) {
                goto done;
            }
        }
    }
done:
    if (io.close_target) {
        CloseHandle(io.target);
    }
    return 0;
}

int main(void) {
    wchar_t self[MAX_PATH];
    DWORD self_len = GetModuleFileNameW(NULL, self, MAX_PATH);
    if (self_len == 0 || self_len >= MAX_PATH) {
        return 1;
    }
    wchar_t *slash = wcsrchr(self, L'\\');
    if (slash) {
        *slash = L'\0';
    }

    /* Helper lives beside this stub in the staged node_modules layout. */
    wchar_t helper[1024];
    wchar_t helper_dir[1024];
    wchar_t command[1200];
    swprintf(helper, 1024,
             L"%s\\node_modules\\@cricut\\device-common\\CricutDevice.exe", self);
    wcscpy_s(helper_dir, 1024, helper);
    slash = wcsrchr(helper_dir, L'\\');
    if (slash) {
        *slash = L'\0';
    }
    /* argv[0] must be the program even when lpApplicationName is set. */
    swprintf(command, 1200, L"\"%s\" bridge", helper);

    SECURITY_ATTRIBUTES inherit = {sizeof inherit, NULL, TRUE};
    HANDLE in_read, in_write, out_read, out_write, err_read, err_write;
    if (!CreatePipe(&in_read, &in_write, &inherit, 0)
        || !CreatePipe(&out_read, &out_write, &inherit, 0)
        || !CreatePipe(&err_read, &err_write, &inherit, 0)) {
        return 1;
    }
    /* Keep our ends of the pipes out of the child. */
    SetHandleInformation(in_write, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(out_read, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(err_read, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW startup = {0};
    startup.cb = sizeof startup;
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = in_read;
    startup.hStdOutput = out_write;
    startup.hStdError = err_write;

    PROCESS_INFORMATION process = {0};
    if (!CreateProcessW(helper, command, NULL, NULL, TRUE, 0, NULL, helper_dir,
                        &startup, &process)) {
        return (int)GetLastError();
    }
    CloseHandle(in_read);
    CloseHandle(out_write);
    CloseHandle(err_write);
    CloseHandle(process.hThread);

    Pump stdin_pump = {GetStdHandle(STD_INPUT_HANDLE), in_write, TRUE};
    Pump stdout_pump = {out_read, GetStdHandle(STD_OUTPUT_HANDLE), FALSE};
    Pump stderr_pump = {err_read, GetStdHandle(STD_ERROR_HANDLE), FALSE};
    CreateThread(NULL, 0, pump, &stdin_pump, 0, NULL);
    HANDLE out_thread = CreateThread(NULL, 0, pump, &stdout_pump, 0, NULL);
    HANDLE err_thread = CreateThread(NULL, 0, pump, &stderr_pump, 0, NULL);

    WaitForSingleObject(process.hProcess, INFINITE);
    /* Let the output pumps flush the final frames before we exit. */
    if (out_thread) {
        WaitForSingleObject(out_thread, 2000);
    }
    if (err_thread) {
        WaitForSingleObject(err_thread, 2000);
    }
    DWORD exit_code = 0;
    GetExitCodeProcess(process.hProcess, &exit_code);
    return (int)exit_code;
}
