#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

static int checks, failures;
static void check(const char *name, int ok) {
    ++checks;
    failures += !ok;
    printf("DUPFD_CHECK %s %s\n", name, ok ? "PASS" : "FAIL");
}
static void expect_error(int source, int cmd, int minimum, int error,
                         const char *name) {
    errno = 0;
    int fd = fcntl(source, cmd, minimum), saved = errno;
    printf("DUPFD_RESULT cmd=%d source=%d min=%d rc=%d errno=%d\n",
           cmd, source, minimum, fd, saved);
    check(name, fd == -1 && saved == error);
    if (fd >= 0) close(fd);
}
static int set_soft(rlim_t limit) {
    struct rlimit r;
    if (getrlimit(RLIMIT_NOFILE, &r)) return -1;
    r.rlim_cur = limit;
    return setrlimit(RLIMIT_NOFILE, &r);
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc == 4 && !strcmp(argv[1], "--exec")) {
        int keep = atoi(argv[2]), drop = atoi(argv[3]);
        check("exec keeps F_DUPFD", fcntl(keep, F_GETFD) == 0);
        errno = 0;
        check("exec closes F_DUPFD_CLOEXEC",
              fcntl(drop, F_GETFD) == -1 && errno == EBADF);
        return failures ? 1 : 0;
    }
    struct rlimit original;
    if (getrlimit(RLIMIT_NOFILE, &original) || original.rlim_max < 128
        || set_soft(128)) { perror("rlimit setup"); return 2; }
    /* Keep fixtures independent of inherited launcher descriptors. */
    for (int fd = 3; fd < 128; ++fd) close(fd);
    char path[] = "/tmp/ict-dupfd-XXXXXX";
    int source = mkstemp(path);
    if (source < 0) { perror("mkstemp"); return 2; }
    unlink(path);
    if (write(source, "abcdef", 6) != 6) return 2;
    for (int cloexec = 0; cloexec < 2; ++cloexec) {
        int cmd = cloexec ? F_DUPFD_CLOEXEC : F_DUPFD;
        printf("DUPFD_GROUP cmd=%d\n", cmd);
        check("source CLOEXEC setup", fcntl(source, F_SETFD, FD_CLOEXEC) == 0);
        int minima[] = {0, 64, 100, 127};
        for (unsigned i = 0; i < sizeof(minima) / sizeof(minima[0]); ++i) {
            int minimum = minima[i], expected = minimum ? minimum : source + 1;
            int fd = fcntl(source, cmd, minimum);
            printf("DUPFD_RESULT cmd=%d min=%d rc=%d expected=%d\n",
                   cmd, minimum, fd, expected);
            check("lowest available at or above minimum", fd == expected);
            check("duplicate CLOEXEC independent of source", fd >= 0 &&
                  fcntl(fd, F_GETFD) == (cloexec ? FD_CLOEXEC : 0));
            check("source CLOEXEC preserved", fcntl(source, F_GETFD) == FD_CLOEXEC);
            if (fd >= 0) close(fd);
        }
        check("occupied minimum setup", dup3(source, 64, 0) == 64);
        int fd = fcntl(source, cmd, 64);
        check("skip occupied minimum without using low hole", fd == 65);
        if (fd >= 0) close(fd);
        close(64);
        fd = fcntl(source, cmd, 64);
        check("reuse hole at minimum", fd == 64);
        if (fd >= 0) close(fd);
        expect_error(source, cmd, -1, EINVAL, "negative minimum");
        expect_error(source, cmd, INT_MIN, EINVAL, "INT_MIN minimum");
        expect_error(source, cmd, 128, EINVAL, "minimum equals soft limit");
        expect_error(source, cmd, INT_MAX, EINVAL, "minimum above limit");
        expect_error(-1, cmd, 64, EBADF, "negative source");
        expect_error(63, cmd, 64, EBADF, "closed source");
        expect_error(-1, cmd, -1, EBADF, "bad source precedes bad minimum");
        fd = fcntl(source, cmd, 64);
        char c = 0;
        check("seek through duplicate", fd >= 0 && lseek(fd, 2, SEEK_SET) == 2);
        check("shared offset visible to source", read(source, &c, 1) == 1 && c == 'c');
        check("shared offset visible to duplicate", fd >= 0 && lseek(fd, 0, SEEK_CUR) == 3);
        check("shared file status setup", fd >= 0 && fcntl(fd, F_SETFL, O_APPEND) == 0);
        check("shared O_APPEND", (fcntl(source, F_GETFL) & O_APPEND) != 0);
        fcntl(source, F_SETFL, 0);
        if (fd >= 0) close(fd);
        check("duplicate close preserves source", fcntl(source, F_GETFD) == FD_CLOEXEC);

        /* Every allowed high slot is full, although many low holes remain. */
        for (int n = 100; n < 128; ++n)
            if (dup3(source, n, 0) != n) return 2;
        expect_error(source, cmd, 100, EMFILE, "full requested range");
        close(110);
        fd = fcntl(source, cmd, 100);
        check("failed range allocation leaves reusable hole", fd == 110);
        if (fd >= 0) close(fd);
        for (int n = 100; n < 128; ++n) close(n);

        /* Existing high FDs stay open after lowering the limit. New numbers
           are bounded by the limit, not by the count of existing FDs. */
        for (int n = 100; n < 128; ++n)
            if (dup3(source, n, 0) != n) return 2;
        if (set_soft(8)) return 2;
        fd = fcntl(source, cmd, 4);
        check("high descriptors do not consume low-number allowance", fd == 4);
        if (fd >= 0) close(fd);
        expect_error(source, cmd, 8, EINVAL, "lowered limit minimum rejected");
        if (set_soft(128)) return 2;
        for (int n = 100; n < 128; ++n) close(n);
    }

    int keep = fcntl(source, F_DUPFD, 64);
    int drop = fcntl(source, F_DUPFD_CLOEXEC, 100);
    pid_t pid = fork();
    if (pid == 0) {
        char a[24], b[24];
        snprintf(a, sizeof(a), "%d", keep); snprintf(b, sizeof(b), "%d", drop);
        execl(argv[0], argv[0], "--exec", a, b, (char *)NULL);
        _exit(2);
    }
    int status = 0;
    check("CLOEXEC across fork and exec", pid > 0 && waitpid(pid, &status, 0) == pid &&
          WIFEXITED(status) && WEXITSTATUS(status) == 0);
    if (keep >= 0) close(keep);
    if (drop >= 0) close(drop);

    for (int n = 4; n < 128; ++n)
        if (dup3(source, n, 0) != n) return 2;
    expect_error(source, F_DUPFD, 0, EMFILE, "table full F_DUPFD");
    expect_error(source, F_DUPFD_CLOEXEC, 0, EMFILE, "table full CLOEXEC");
    close(70);
    int fd = fcntl(source, F_DUPFD, 64);
    check("full table failure does not leak", fd == 70);
    for (int n = 3; n < 128; ++n) close(n);
    check("restore soft limit", set_soft(original.rlim_cur) == 0);
    printf("DUPFD_SUMMARY checks=%d failures=%d\n", checks, failures);
    return failures ? 1 : 0;
}
