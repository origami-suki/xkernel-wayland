/* Calibrate the project's opt-in syscall collector; no third-party changes. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define NODE "/proc/syscall_profile"
struct totals { uint64_t started, completed, errors, cpu, wall, max_cpu, max_wall, anomaly; };
static int failures;
static void check(int ok, const char *name) {
    printf("PROFILE_CHECK %s %s\n", name, ok ? "PASS" : "FAIL");
    failures += !ok;
}
static int command(const char *cmd) {
    int fd = open(NODE, O_WRONLY); if (fd < 0) return -1;
    ssize_t n = write(fd, cmd, strlen(cmd)); int saved = errno;
    close(fd); errno = saved; return n == (ssize_t)strlen(cmd) ? 0 : -1;
}
static void ctl(const char *cmd) { if (command(cmd)) { perror(cmd); exit(2); } }
static char *snapshot(void) {
    int fd = open(NODE, O_RDONLY); if (fd < 0) { perror("snapshot open"); exit(2); }
    size_t cap = 4 * 1024 * 1024, size = 0;
    char *buf = malloc(cap); if (!buf) exit(2);
    for (;;) { ssize_t n = read(fd, buf + size, cap - 1 - size);
        if (n < 0) { perror("snapshot read"); exit(2); }
        if (!n) break;
        size += (size_t)n; if (size == cap - 1) exit(2);
    }
    close(fd); buf[size] = 0;
    if (!strstr(buf, "SYSCALL_PROFILE_END dropped=0\n")) { fprintf(stderr, "bad/truncated/lossy snapshot\n"); exit(2); }
    return buf;
}
static struct totals total(char *text, unsigned pid, unsigned nr) {
    struct totals t = {0};
    for (char *line = text; line && *line; line = strchr(line, '\n')) {
        if (*line == '\n') ++line;
        unsigned shard, rowpid, sysno;
        struct totals r;
        int n = sscanf(line, "%u,%u,%u,%" SCNu64 ",%" SCNu64 ",%" SCNu64 ",%" SCNu64 ",%" SCNu64 ",%" SCNu64 ",%" SCNu64 ",%" SCNu64,
            &shard, &rowpid, &sysno, &r.started, &r.completed, &r.errors, &r.cpu, &r.wall, &r.max_cpu, &r.max_wall, &r.anomaly);
        if (n == 11 && rowpid == pid && sysno == nr) {
            t.started += r.started; t.completed += r.completed; t.errors += r.errors;
            t.cpu += r.cpu; t.wall += r.wall; t.anomaly += r.anomaly;
            if (r.max_cpu > t.max_cpu) t.max_cpu = r.max_cpu;
            if (r.max_wall > t.max_wall) t.max_wall = r.max_wall;
        }
    }
    return t;
}
static void sleep_ms(long ms) {
    struct timespec t = {ms / 1000, (ms % 1000) * 1000000};
    while (syscall(SYS_nanosleep, &t, &t) && errno == EINTR) {}
}
static void *worker(void *unused) {
    (void)unused;
    for (int i = 0; i < 2000; ++i) syscall(SYS_getppid);
    return NULL;
}
static int boundary_pipe[2];
static atomic_int boundary_ready;
static void *boundary_worker(void *unused) {
    (void)unused;
    char byte;
    atomic_store(&boundary_ready, 1);
    if (read(boundary_pipe[0], &byte, 1) != 1) return (void *)1;
    syscall(SYS_getpid);
    return NULL;
}
int main(void) {
    unsigned pid = (unsigned)getpid();
    ctl("stop");
    check(command("nonsense") == -1 && errno == EINVAL, "reject-invalid-command");
    ctl("start");
    check(command("start") == -1 && errno == EBUSY, "reject-active-start");
    int fd = open(NODE, O_RDONLY); char byte;
    int readrc = fd < 0 ? -1 : (int)read(fd, &byte, 1); int readerr = errno;
    if (fd >= 0) close(fd);
    check(readrc == -1 && readerr == EBUSY, "reject-active-snapshot");
    for (int i = 0; i < 5000; ++i) syscall(SYS_getpid);
    for (int i = 0; i < 17; ++i) syscall(SYS_read, -1, &byte, 1);
    sleep_ms(200);
    pthread_t threads[4];
    for (int i = 0; i < 4; ++i) if (pthread_create(&threads[i], NULL, worker, NULL)) return 2;
    for (int i = 0; i < 4; ++i) if (pthread_join(threads[i], NULL)) return 2;
    int pipefd[2]; if (pipe(pipefd)) return 2;
    pid_t child = fork(); if (child < 0) return 2;
    if (!child) { close(pipefd[0]); sleep_ms(200); if (write(pipefd[1], "x", 1) != 1) _exit(2); _exit(0); }
    close(pipefd[1]); if (read(pipefd[0], &byte, 1) != 1) return 2;
    close(pipefd[0]); int status; if (waitpid(child, &status, 0) < 0) return 2;
    check(WIFEXITED(status) && WEXITSTATUS(status) == 0, "child-exit");
    ctl("stop");
    char *a = snapshot(), *b = snapshot();
    check(!strcmp(a, b), "stopped-snapshot-stable"); free(b);
    struct totals t = total(a, pid, SYS_getpid);
    check(t.started == 5000 && t.completed == 5000 && t.errors == 0 && t.cpu > 0 && t.anomaly == 0, "exact-getpid-and-cpu");
    t = total(a, pid, SYS_getppid);
    check(t.started == 8000 && t.completed == 8000 && !t.errors && !t.anomaly, "four-thread-aggregation");
    t = total(a, pid, SYS_nanosleep);
    printf("PROFILE_CALIBRATION sleep_cpu_ns=%" PRIu64 " sleep_wall_ns=%" PRIu64 "\n", t.cpu, t.wall);
    check(t.completed == 1 && t.wall >= 180000000 && t.cpu < t.wall / 5 && !t.anomaly, "sleep-excludes-offcpu");
    t = total(a, pid, SYS_read);
    printf("PROFILE_CALIBRATION read_cpu_ns=%" PRIu64 " read_wall_ns=%" PRIu64 " errors=%" PRIu64 "\n", t.cpu, t.wall, t.errors);
    check(t.errors == 18 && t.wall >= 180000000 && t.cpu < t.wall / 5 && !t.anomaly, "read-errors-and-blocking");
    free(a);
    ctl("start"); syscall(SYS_getpid); ctl("stop"); a = snapshot();
    t = total(a, pid, SYS_getpid);
    check(t.started == 1 && t.completed == 1, "new-window-resets-counts"); free(a);
    if (pipe(boundary_pipe)) return 2;
    ctl("start");
    pthread_t boundary_thread;
    if (pthread_create(&boundary_thread, NULL, boundary_worker, NULL)) return 2;
    while (!atomic_load(&boundary_ready)) sleep_ms(1);
    /* Integration check: establish that the read really was outstanding below;
     * the wait alone is never treated as proof of entry. */
    sleep_ms(30);
    ctl("stop"); a = snapshot(); t = total(a, pid, SYS_read);
    check(t.started == 1 && t.completed == 0, "boundary-read-observed-outstanding"); free(a);
    ctl("start");
    if (write(boundary_pipe[1], "b", 1) != 1) return 2;
    void *boundary_result;
    if (pthread_join(boundary_thread, &boundary_result)) return 2;
    ctl("stop"); a = snapshot(); t = total(a, pid, SYS_read);
    check(boundary_result == NULL && t.started == 0 && t.completed == 0, "old-read-not-attributed-to-new-window");
    t = total(a, pid, SYS_getpid);
    check(t.started == 1 && t.completed == 1, "worker-continues-in-new-window"); free(a);
    close(boundary_pipe[0]); close(boundary_pipe[1]);
    fd = open(NODE, O_RDWR); if (fd < 0) return 2;
    child = fork(); if (child < 0) return 2;
    if (!child) {
        if (setuid(65534)) _exit(2);
        errno = 0; int denied_open = open(NODE, O_RDONLY) == -1 && errno == EACCES;
        errno = 0; int denied_read = read(fd, &byte, 1) == -1 && errno == EACCES;
        errno = 0; int denied_write = write(fd, "start", 5) == -1 && errno == EACCES;
        _exit(denied_open && denied_read && denied_write ? 0 : 1);
    }
    close(fd); if (waitpid(child, &status, 0) < 0) return 2;
    check(WIFEXITED(status) && WEXITSTATUS(status) == 0, "unprivileged-and-inherited-fd-denied");
    printf("PROFILE_CALIBRATION_RESULT failures=%d\n", failures);
    return failures != 0;
}
