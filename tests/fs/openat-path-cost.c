#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define BASE "/home/kiosk/.ict-openat-probe-m5-003"
#define EXISTING BASE "/a/b/existing"
#define FRESH BASE "/a/b/fresh"
#define SAMPLES 20

static uint64_t cpu_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts)) {
        perror("CLOCK_THREAD_CPUTIME_ID");
        exit(2);
    }
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static void make_dir(const char *path) {
    if (mkdir(path, 0700) && errno != EEXIST) {
        perror(path);
        exit(3);
    }
}

static void prepare(void) {
    make_dir(BASE);
    make_dir(BASE "/a");
    make_dir(BASE "/a/b");
    int fd = open(EXISTING, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) { perror("create private fixture"); exit(4); }
    const char seed[] = "openat probe fixture\n";
    if (write(fd, seed, sizeof(seed) - 1) != (ssize_t)(sizeof(seed) - 1)) {
        perror("seed private fixture"); exit(5);
    }
    if (close(fd)) { perror("close fixture"); exit(6); }
    printf("OPENAT_PREPARED uid=%ld base=%s\n", (long)getuid(), BASE);
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    if (!strcmp(argv[1], "prepare")) { prepare(); return 0; }
    const char *name = argv[1], *path = NULL;
    int flags = 0;
    if (!strcmp(name, "P1")) { path = "/proc/self/status"; flags = 0xa0000; }
    else if (!strcmp(name, "P2")) { path = "/proc/self/task"; flags = 0x24000; }
    else if (!strcmp(name, "H1")) { path = EXISTING; flags = 0x20000; }
    else if (!strcmp(name, "H2")) { path = EXISTING; flags = 0xa0042; }
    else if (!strcmp(name, "H3")) { path = FRESH; flags = 0xa0042; }
    else if (!strcmp(name, "H4")) { path = EXISTING; flags = 0x20241; }
    else return 1;
    if (getuid() != 1000) { fprintf(stderr, "expected kiosk uid=1000\n"); return 7; }
    printf("OPENAT_CASE_BEGIN case=%s pid=%ld uid=%ld flags=%d path=%s samples=%d\n",
           name, (long)getpid(), (long)getuid(), flags, path, SAMPLES);
    for (unsigned i = 0; i < SAMPLES; ++i) {
        if (!strcmp(name, "H3") && unlink(FRESH) && errno != ENOENT) {
            perror("unlink own fresh fixture"); return 8;
        }
        uint64_t before = cpu_ns();
        errno = 0;
        int fd = openat(AT_FDCWD, path, flags, 0600);
        int saved_errno = errno;
        uint64_t elapsed = cpu_ns() - before;
        printf("OPENAT_SAMPLE case=%s sample=%u fd=%d errno=%d cpu_ns=%llu\n",
               name, i + 1, fd, fd < 0 ? saved_errno : 0, (unsigned long long)elapsed);
        if (fd < 0) return 9;
        if (close(fd)) { perror("close probe file"); return 10; }
    }
    printf("OPENAT_CASE_END case=%s passed=%d failed=0\n", name, SAMPLES);
    return 0;
}
