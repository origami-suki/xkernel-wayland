#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static int failures, checks;
struct worker { int ready, done; pid_t tid; };

static void must(int ok, const char *what) {
    if (!ok) { perror(what); exit(2); }
}
static void result(const char *phase, const char *what, int ok, long got, long want) {
    ++checks;
    failures += !ok;
    printf("PROC_TASK phase=%s check=%s got=%ld expected=%ld failed=%d\n",
           phase, what, got, want, !ok);
}
static void check_stat(const char *phase, const char *what, int rc,
                       const struct stat *st, nlink_t expected) {
    result(phase, what, rc == 0 && S_ISDIR(st->st_mode) && st->st_nlink == expected,
           rc ? -errno : (long)st->st_nlink, (long)expected);
}
static void phase(int procfd, int taskfd, const char *name, int threads) {
    struct stat st = {0};
    int rc = fstatat(procfd, "self/task/", &st, 0);
    check_stat(name, "fstatat-self", rc, &st, 2 + threads);
    rc = fstat(taskfd, &st);
    check_stat(name, "held-fd", rc, &st, 2 + threads);
    char path[80];
    snprintf(path, sizeof(path), "/proc/%d/task", getpid());
    rc = stat(path, &st);
    check_stat(name, "stat-pid", rc, &st, 2 + threads);
    DIR *dir = opendir(path);
    must(dir != NULL, "opendir");
    int count = 0;
    struct dirent *entry;
    for (;;) {
        errno = 0;
        entry = readdir(dir);
        if (!entry) {
            must(errno == 0, "readdir");
            break;
        }
        char *end;
        (void)strtol(entry->d_name, &end, 10);
        if (entry->d_name[0] && !*end) ++count;
    }
    closedir(dir);
    result(name, "directory-members", count == threads, count, threads);
}
static void *worker_main(void *arg) {
    struct worker *w = arg;
    must(pthread_mutex_lock(&lock) == 0, "worker lock");
    w->tid = (pid_t)syscall(SYS_gettid);
    w->ready = 1;
    must(pthread_cond_broadcast(&cv) == 0, "ready signal");
    while (!w->done) must(pthread_cond_wait(&cv, &lock) == 0, "worker wait");
    must(pthread_mutex_unlock(&lock) == 0, "worker unlock");
    return NULL;
}
static void start_worker(pthread_t *thread, struct worker *w) {
    must(pthread_create(thread, NULL, worker_main, w) == 0, "create");
    must(pthread_mutex_lock(&lock) == 0, "start lock");
    while (!w->ready) must(pthread_cond_wait(&cv, &lock) == 0, "ready wait");
    must(pthread_mutex_unlock(&lock) == 0, "start unlock");
}
static void stop_worker(pthread_t thread, struct worker *w) {
    must(pthread_mutex_lock(&lock) == 0, "stop lock");
    w->done = 1;
    must(pthread_cond_broadcast(&cv) == 0, "stop signal");
    must(pthread_mutex_unlock(&lock) == 0, "stop unlock");
    must(pthread_join(thread, NULL) == 0, "join");
    /* Linux can wake pthread_join before removing the proc task entry. Wait
       for observed removal, rather than assuming join completes kernel teardown. */
    char path[80];
    snprintf(path, sizeof(path), "/proc/self/task/%d", w->tid);
    struct timespec start, now;
    must(clock_gettime(CLOCK_MONOTONIC, &start) == 0, "clock");
    for (;;) {
        struct stat st;
        if (stat(path, &st) == -1) {
            must(errno == ENOENT, "retired tid");
            break;
        }
        must(clock_gettime(CLOCK_MONOTONIC, &now) == 0, "clock");
        must(now.tv_sec - start.tv_sec < 3, "tid retirement deadline");
        sched_yield();
    }
}
static void tid_exists(int taskfd, pid_t tid, int exists) {
    char path[32];
    snprintf(path, sizeof(path), "%d", tid);
    struct stat st;
    errno = 0;
    int rc = fstatat(taskfd, path, &st, 0), err = errno;
    result(exists ? "worker-alive" : "worker-joined", "tid-lookup",
           exists ? rc == 0 && S_ISDIR(st.st_mode) : rc == -1 && err == ENOENT,
           rc ? -err : 0, exists ? 0 : -ENOENT);
}
int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    alarm(20); /* Failure bound only; transitions below use cond/join/pipe/wait. */
    int procfd = open("/proc", O_RDONLY | O_DIRECTORY);
    int taskfd = open("/proc/self/task", O_RDONLY | O_DIRECTORY);
    must(procfd >= 0 && taskfd >= 0, "open proc");
    phase(procfd, taskfd, "single", 1);
    for (int cycle = 0; cycle < 3; ++cycle) {
        printf("PROC_TASK_CYCLE %d\n", cycle);
        struct worker a = {0}, b = {0};
        pthread_t ta, tb;
        start_worker(&ta, &a);
        phase(procfd, taskfd, "one-worker", 2);
        tid_exists(taskfd, a.tid, 1);
        start_worker(&tb, &b);
        phase(procfd, taskfd, "two-workers", 3);
        tid_exists(taskfd, b.tid, 1);
        stop_worker(ta, &a);
        phase(procfd, taskfd, "one-joined", 2);
        tid_exists(taskfd, a.tid, 0);
        tid_exists(taskfd, b.tid, 1);
        stop_worker(tb, &b);
        phase(procfd, taskfd, "all-joined", 1);
        tid_exists(taskfd, b.tid, 0);
    }
    int gate[2];
    must(pipe(gate) == 0, "pipe");
    pid_t child = fork();
    must(child >= 0, "fork");
    if (!child) {
        close(gate[1]);
        char byte;
        _exit(read(gate[0], &byte, 1) == 1 ? 0 : 2);
    }
    close(gate[0]);
    char path[80];
    snprintf(path, sizeof(path), "/proc/%d/task", child);
    int childfd = open(path, O_RDONLY | O_DIRECTORY);
    must(childfd >= 0, "open child");
    struct stat st = {0};
    int rc = fstat(childfd, &st);
    check_stat("child-live", "held-fd", rc, &st, 3);
    phase(procfd, taskfd, "parent-with-child", 1);
    must(write(gate[1], "x", 1) == 1, "release child");
    close(gate[1]);
    int status;
    must(waitpid(child, &status, 0) == child && WIFEXITED(status)
         && WEXITSTATUS(status) == 0, "wait child");
    rc = fstat(childfd, &st);
    check_stat("child-reaped", "held-fd", rc, &st, 2);
    errno = 0;
    rc = stat(path, &st);
    result("child-reaped", "fresh-path", rc == -1 && errno == ENOENT,
           rc ? -errno : 0, -ENOENT);
    errno = 0;
    rc = fstatat(procfd, "self/task/not-a-tid", &st, 0);
    result("invalid", "non-numeric-tid", rc == -1 && errno == ENOENT,
           rc ? -errno : 0, -ENOENT);
    errno = 0;
    rc = fstatat(-1, "self/task", &st, 0);
    result("invalid", "bad-fd", rc == -1 && errno == EBADF,
           rc ? -errno : 0, -EBADF);
    close(childfd);
    close(taskfd);
    close(procfd);
    alarm(0);
    printf("PROC_TASK_SUMMARY checks=%d failures=%d\n", checks, failures);
    return failures ? 1 : 0;
}
