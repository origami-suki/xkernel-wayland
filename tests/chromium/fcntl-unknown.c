#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <unistd.h>

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    int source = open("/dev/null", O_RDONLY | O_CLOEXEC);
    int closed = dup(source), failures = 0, checks = 0;
    if (source < 0 || closed < 0 || close(closed)) return 2;
    int commands[] = {-1, 0x7fff, INT_MAX};
    int sources[] = {source, closed, -1};
    for (unsigned c = 0; c < sizeof(commands) / sizeof(commands[0]); ++c) {
        for (unsigned s = 0; s < sizeof(sources) / sizeof(sources[0]); ++s) {
            for (int arg = 0; arg < 2; ++arg) {
                errno = 0;
                int rc = fcntl(sources[s], commands[c], arg ? 0x1234 : 0);
                int error = errno, expected = s ? EBADF : EINVAL;
                int ok = rc == -1 && error == expected;
                ++checks; failures += !ok;
                printf("FCNTL_UNKNOWN fd=%d cmd=%d arg=%d rc=%d errno=%d expected=%d %s\n",
                       sources[s], commands[c], arg, rc, error, expected,
                       ok ? "PASS" : "FAIL");
            }
        }
    }
    int flags = fcntl(source, F_GETFD);
    ++checks; failures += flags != FD_CLOEXEC;
    int next = dup(source);
    ++checks; failures += next != closed;
    if (next >= 0) close(next);
    close(source);
    printf("FCNTL_UNKNOWN_SUMMARY checks=%d failures=%d\n", checks, failures);
    return failures ? 1 : 0;
}
