#define _GNU_SOURCE
#include <errno.h>
#include <dirent.h>
#include <pthread.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static pthread_mutex_t helper_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t helper_ready_cond = PTHREAD_COND_INITIALIZER;
static pthread_cond_t helper_stop_cond = PTHREAD_COND_INITIALIZER;
static unsigned helper_ready;
static int helper_stop;
static void pthread_check(int error, const char *what) {
    if (error) { fprintf(stderr, "%s: %s\n", what, strerror(error)); exit(12); }
}
static void *helper_wait(void *unused) {
    (void)unused;
    pthread_check(pthread_mutex_lock(&helper_mutex), "helper lock");
    ++helper_ready;
    pthread_check(pthread_cond_signal(&helper_ready_cond), "helper ready signal");
    while (!helper_stop)
        pthread_check(pthread_cond_wait(&helper_stop_cond, &helper_mutex), "helper wait");
    pthread_check(pthread_mutex_unlock(&helper_mutex), "helper unlock");
    return NULL;
}
static long actual_threads(void) {
    DIR *dir = opendir("/proc/self/task");
    if (!dir) return -1;
    long count = 0;
    struct dirent *entry;
    while ((entry = readdir(dir))) {
        char *end;
        (void)strtoul(entry->d_name, &end, 10);
        if (entry->d_name[0] && !*end) ++count;
    }
    if (closedir(dir)) return -1;
    return count;
}
static uint64_t ns(clockid_t id) {
    struct timespec t;
    if (clock_gettime(id, &t)) { perror("clock_gettime"); exit(2); }
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
static uint64_t verify(int fd, unsigned char *buf, size_t chunk, uint64_t expected) {
    uint64_t h = UINT64_C(14695981039346656037), bytes = 0;
    if (lseek(fd, 0, SEEK_SET) != 0) { perror("verify lseek"); exit(3); }
    for (;;) {
        ssize_t n = read(fd, buf, chunk);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) { perror("verify read"); exit(4); }
        if (!n) break;
        bytes += (uint64_t)n;
        for (ssize_t i = 0; i < n; ++i) { h ^= buf[i]; h *= UINT64_C(1099511628211); }
    }
    if (bytes != expected) { fprintf(stderr, "verify size changed\n"); exit(5); }
    return h;
}
int main(int argc, char **argv) {
    if (argc != 6 && argc != 7) { fprintf(stderr, "usage: probe path mode chunk prefault expected-fnv64hex [helpers:0|8|32]\n"); return 1; }
    const char *mode = argv[2];
    int per_call = !strcmp(mode, "per-call"), clock_only = !strcmp(mode, "clock-only");
    if (strcmp(mode, "batch") && !per_call && !clock_only) return 1;
    char *end; unsigned long parsed = strtoul(argv[3], &end, 10);
    if (*end || parsed < 1 || parsed > 65536) return 1;
    size_t chunk = (size_t)parsed;
    int prefault = !strcmp(argv[4], "1");
    if (!prefault && strcmp(argv[4], "0")) return 1;
    uint64_t expected_hash = strtoull(argv[5], &end, 16);
    if (*end) return 1;
    unsigned helpers = 0;
    if (argc == 7) {
        if (strcmp(argv[6], "0") && strcmp(argv[6], "8") && strcmp(argv[6], "32")) return 1;
        helpers = (unsigned)strtoul(argv[6], NULL, 10);
    }
    int fd = open(argv[1], O_RDONLY | O_CLOEXEC);
    if (fd < 0) { perror("open"); return 6; }
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 0) { fprintf(stderr, "expected regular file\n"); return 7; }
    unsigned char *buf = malloc(chunk);
    if (!buf) { perror("malloc"); return 8; }
    if (prefault) { volatile unsigned char *p = buf; for (size_t i = 0; i < chunk; ++i) p[i] = 0; }
    printf("READ_META page_size=%ld pid=%ld uid=%ld\n", sysconf(_SC_PAGESIZE), (long)getpid(), (long)getuid());
    uint64_t expected_bytes = (uint64_t)st.st_size;
    uint64_t expected_calls = (expected_bytes + chunk - 1) / chunk + 1;
    pthread_t tids[32];
    for (unsigned i = 0; i < helpers; ++i)
        pthread_check(pthread_create(&tids[i], NULL, helper_wait, NULL), "pthread_create");
    pthread_check(pthread_mutex_lock(&helper_mutex), "main ready lock");
    while (helper_ready != helpers)
        pthread_check(pthread_cond_wait(&helper_ready_cond, &helper_mutex), "main ready wait");
    /* Acquiring this mutex after every helper incremented ready requires each
       helper to release it in cond_wait. They wait without timeout or busy loop. */
    pthread_check(pthread_mutex_unlock(&helper_mutex), "main ready unlock");
    long thread_count = actual_threads();
    if (thread_count >= 0 && thread_count != (long)helpers + 1) {
        fprintf(stderr, "unexpected proc task count %ld\n", thread_count); return 13;
    }
    for (unsigned repeat = 1; repeat <= 2; ++repeat) {
        if (lseek(fd, 0, SEEK_SET) != 0) { perror("lseek"); return 9; }
        uint64_t calls = 0, successful = 0, bytes = 0, short_reads = 0, interrupted = 0;
        uint64_t call_wall = 0, call_cpu = 0, raw_read_attempts = 0;
        unsigned eof = 0; int read_error = 0;
        uint64_t wall_start = ns(CLOCK_MONOTONIC), cpu_start = ns(CLOCK_THREAD_CPUTIME_ID);
        for (;;) {
            uint64_t w = 0, c = 0;
            if (per_call || clock_only) { w = ns(CLOCK_MONOTONIC); c = ns(CLOCK_THREAD_CPUTIME_ID); }
            ssize_t n = 0;
            if (!clock_only) {
                size_t filled = 0;
                /* Match Chromium File::ReadAtCurrentPos: fill buffer or stop at EOF. */
                while (filled < chunk) {
                    ssize_t part = read(fd, buf + filled, chunk - filled);
                    ++raw_read_attempts;
                    if (part < 0 && errno == EINTR) { ++interrupted; continue; }
                    if (part < 0) { read_error = errno; n = -1; break; }
                    if (!part) break;
                    filled += (size_t)part;
                }
                if (!read_error) n = (ssize_t)filled;
            }
            if (per_call || clock_only) { call_cpu += ns(CLOCK_THREAD_CPUTIME_ID) - c; call_wall += ns(CLOCK_MONOTONIC) - w; }
            ++calls;
            if (clock_only) { if (calls == expected_calls) break; continue; }
            if (n < 0 && read_error == EINTR) { ++interrupted; read_error = 0; continue; }
            if (n < 0) break;
            if (n == 0) { eof = 1; break; }
            ++successful; bytes += (uint64_t)n;
            if ((size_t)n != chunk) ++short_reads;
        }
        uint64_t cpu = ns(CLOCK_THREAD_CPUTIME_ID) - cpu_start;
        uint64_t wall = ns(CLOCK_MONOTONIC) - wall_start;
        /* Verification is a distinct full-file pass after both outer timers stop. */
        uint64_t verify_cpu_start = ns(CLOCK_THREAD_CPUTIME_ID);
        uint64_t hash = verify(fd, buf, chunk, expected_bytes);
        uint64_t verify_cpu = ns(CLOCK_THREAD_CPUTIME_ID) - verify_cpu_start;
        int valid = hash == expected_hash && !read_error &&
            (clock_only ? calls == expected_calls : bytes == expected_bytes && eof == 1);
        printf("READ_JSON {\"mode\":\"%s\",\"helpers\":%u,\"helpers_ready\":%u,\"proc_task_count\":%ld,\"chunk\":%zu,\"prefault\":%d,\"repeat\":%u,\"label\":\"%s\",\"file_size\":%"PRIu64",\"calls\":%"PRIu64",\"raw_read_attempts\":%"PRIu64",\"successful_reads\":%"PRIu64",\"bytes\":%"PRIu64",\"eof\":%u,\"errno\":%d,\"short_reads\":%"PRIu64",\"eintr\":%"PRIu64",\"wall_ns\":%"PRIu64",\"cpu_ns\":%"PRIu64",\"call_wall_ns\":%"PRIu64",\"call_cpu_ns\":%"PRIu64",\"verify_cpu_ns\":%"PRIu64",\"fnv64\":\"%016"PRIx64"\",\"valid\":%s}\n",
            mode, helpers, helper_ready, thread_count, chunk, prefault, repeat, repeat == 1 ? "mode-first-use" : "mode-warm", expected_bytes, calls, raw_read_attempts, successful, bytes, eof, read_error, short_reads, interrupted, wall, cpu, call_wall, call_cpu, verify_cpu, hash, valid ? "true" : "false");
        if (!valid) return 10;
    }
    pthread_check(pthread_mutex_lock(&helper_mutex), "main stop lock");
    helper_stop = 1;
    pthread_check(pthread_cond_broadcast(&helper_stop_cond), "main stop broadcast");
    pthread_check(pthread_mutex_unlock(&helper_mutex), "main stop unlock");
    for (unsigned i = 0; i < helpers; ++i)
        pthread_check(pthread_join(tids[i], NULL), "pthread_join");
    free(buf);
    if (close(fd)) { perror("close"); return 11; }
    return 0;
}
