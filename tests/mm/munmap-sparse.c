#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static size_t page;
static unsigned checks, failures;

static void check(const char *name, int ok) {
    printf("MUNMAP_CHECK %s %s\n", ok ? "PASS" : "FAIL", name);
    checks++;
    failures += !ok;
}

static void *mapping(size_t bytes) {
    void *p = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) { perror("mmap"); exit(2); }
    return p;
}

static uint64_t ns(clockid_t clock) {
    struct timespec t;
    if (clock_gettime(clock, &t)) { perror("clock_gettime"); exit(2); }
    return (uint64_t)t.tv_sec * 1000000000ULL + (uint64_t)t.tv_nsec;
}

static void benchmark(const char *name, size_t bytes, size_t touches) {
    for (unsigned sample = 1; sample <= 5; sample++) {
        volatile unsigned char *p = mapping(bytes);
        size_t pages = bytes / page;
        for (size_t i = 0; i < touches; i++)
            p[(i * pages / touches) * page] = (unsigned char)(i + 1);
        uint64_t wall = ns(CLOCK_MONOTONIC);
        uint64_t cpu = ns(CLOCK_THREAD_CPUTIME_ID);
        int result = munmap((void *)p, bytes);
        cpu = ns(CLOCK_THREAD_CPUTIME_ID) - cpu;
        wall = ns(CLOCK_MONOTONIC) - wall;
        if (result) { perror("munmap"); exit(2); }
        printf("MUNMAP_BENCH case=%s sample=%u bytes=%zu touched_pages=%zu cpu_ns=%llu wall_ns=%llu\n",
               name, sample, bytes, touches,
               (unsigned long long)cpu, (unsigned long long)wall);
    }
}

static void semantics(void) {
    unsigned char *p = mapping(5 * page);
    p[0] = 17; p[4 * page] = 29;
    check("partial-middle", munmap(p + page, 3 * page) == 0);
    check("partial-neighbours", p[0] == 17 && p[4 * page] == 29);
    check("repeat-hole", munmap(p + page, 3 * page) == 0);
    void *q = mmap(p + page, 3 * page, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    check("remap-address", q == p + page);
    if (q == p + page) {
        check("remap-zero", p[page] == 0 && p[3 * page] == 0);
        p[2 * page] = 43;
        check("remap-independent", p[0] == 17 && p[4 * page] == 29);
    }
    errno = 0;
    check("unaligned-start", munmap(p + 1, page) == -1 && errno == EINVAL);
    errno = 0;
    check("zero-length", munmap(p, 0) == -1 && errno == EINVAL);
    check("whole-after-partial", munmap(p, 5 * page) == 0);

    p = mapping(5 * page);
    p[0] = 51; p[4 * page] = 63;
    check("create-hole", munmap(p + page, 3 * page) == 0);
    check("across-vmas-and-hole", munmap(p, 5 * page) == 0);

    p = mapping(3 * page);
    p[0] = 71; p[2 * page] = 83;
    pid_t child = fork();
    if (child < 0) { perror("fork"); exit(2); }
    if (child == 0) {
        if (munmap(p, page) || p[2 * page] != 83) _exit(3);
        p[2 * page] = 97;
        _exit(munmap(p + page, 2 * page) ? 4 : 0);
    }
    int status = 0;
    int waited = waitpid(child, &status, 0) == child;
    check("child-private-unmap", waited && WIFEXITED(status) && WEXITSTATUS(status) == 0);
    check("parent-cow-preserved", p[0] == 71 && p[2 * page] == 83);
    child = fork();
    if (child < 0) { perror("fork"); exit(2); }
    if (child == 0) {
        volatile unsigned char *old = p;
        if (*old != 71 || munmap(p, page)) _exit(5);
        unsigned char stale = *old;
        (void)stale;
        _exit(6);
    }
    status = 0;
    waited = waitpid(child, &status, 0) == child;
    check("unmapped-access-faults", waited && WIFSIGNALED(status) && WTERMSIG(status) == SIGSEGV);
    check("parent-unmap", munmap(p, 3 * page) == 0);

    char path[] = "/tmp/munmap-sparse-XXXXXX";
    int fd = mkstemp(path);
    if (fd < 0) { perror("mkstemp"); exit(2); }
    unlink(path);
    if (ftruncate(fd, (off_t)(3 * page))) { perror("ftruncate"); exit(2); }
    unsigned char value = 101;
    if (pwrite(fd, &value, 1, 0) != 1) { perror("pwrite"); exit(2); }
    p = mmap(NULL, 3 * page, PROT_READ | PROT_WRITE, MAP_PRIVATE, fd, 0);
    if (p == MAP_FAILED) { perror("file mmap"); exit(2); }
    check("file-private-read", p[0] == 101);
    p[0] = 103; p[2 * page] = 107;
    check("file-private-partial", munmap(p + page, page) == 0);
    check("file-private-neighbours", p[0] == 103 && p[2 * page] == 107);
    check("file-private-whole", munmap(p, 3 * page) == 0);
    value = 0;
    check("file-not-overwritten", pread(fd, &value, 1, 0) == 1 && value == 101);
    close(fd);
    printf("MUNMAP_SUMMARY checks=%u failures=%u\n", checks, failures);
}

int main(int argc, char **argv) {
    setbuf(stdout, NULL);
    long size = sysconf(_SC_PAGESIZE);
    if (size <= 0) return 2;
    page = (size_t)size;
    if (argc != 2 || (strcmp(argv[1], "check") && strcmp(argv[1], "bench"))) {
        fputs("usage: munmap-sparse check|bench\n", stderr);
        return 2;
    }
    if (!strcmp(argv[1], "check")) semantics();
    else {
        const size_t mib = 1024 * 1024;
        benchmark("empty-16m", 16 * mib, 0);
        benchmark("empty-256m", 256 * mib, 0);
        benchmark("empty-1g", 1024 * mib, 0);
        benchmark("empty-4g", 4096 * mib, 0);
        benchmark("sparse-16m", 16 * mib, 64);
        benchmark("sparse-256m", 256 * mib, 64);
        benchmark("sparse-1g", 1024 * mib, 64);
        benchmark("sparse-4g", 4096 * mib, 64);
        benchmark("dense-16m", 16 * mib, 16 * mib / page);
    }
    return failures ? 1 : 0;
}
