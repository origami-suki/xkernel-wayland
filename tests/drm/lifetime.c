/* DRM file-description isolation and physical mapping lifetime contracts. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include "drm.h"
#include "drm_mode.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "DRM_LIFETIME_FAIL line=%d check=%s errno=%d\n", __LINE__, #x, errno); exit(1); } } while (0)

static int open_card(const char *path) {
    int fd = open(path, O_RDWR | O_CLOEXEC);
    CHECK(fd >= 0);
    return fd;
}

static struct drm_mode_create_dumb create(int fd) {
    /* At least three pages for split and partial unmap coverage. */
    struct drm_mode_create_dumb b = {.width = 64, .height = 64, .bpp = 32};
    CHECK(ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &b) == 0);
    CHECK(b.size >= 3 * (uint64_t)sysconf(_SC_PAGESIZE));
    return b;
}

static void destroy(int fd, uint32_t handle) {
    struct drm_mode_destroy_dumb d = {.handle = handle};
    CHECK(ioctl(fd, DRM_IOCTL_MODE_DESTROY_DUMB, &d) == 0);
}

static uint64_t offset(int fd, uint32_t handle) {
    struct drm_mode_map_dumb m = {.handle = handle};
    CHECK(ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &m) == 0);
    return m.offset;
}

static void isolation(const char *path) {
    int first = open_card(path), shared = dup(first), other = open_card(path);
    CHECK(shared >= 0);
    struct drm_mode_create_dumb b = create(first);
    uint64_t original = offset(first, b.handle);
    CHECK(offset(shared, b.handle) == original);
    struct drm_mode_map_dumb foreign = {.handle = b.handle};
    errno = 0;
    int rc = ioctl(other, DRM_IOCTL_MODE_MAP_DUMB, &foreign);
    int error = errno;
    printf("DRM_LIFETIME foreign-handle rc=%d errno=%d\n", rc, error);
    CHECK(rc == -1);
    CHECK(close(first) == 0);
    CHECK(offset(shared, b.handle) == original);
    destroy(shared, b.handle);
    foreign = (struct drm_mode_map_dumb){.handle = b.handle};
    CHECK(ioctl(shared, DRM_IOCTL_MODE_MAP_DUMB, &foreign) == -1);
    CHECK(close(shared) == 0 && close(other) == 0);
    puts("DRM_LIFETIME_ISOLATION_OK");
}

static void mapping(const char *path) {
    int fd = open_card(path);
    struct drm_mode_create_dumb b = create(fd);
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    volatile uint32_t *map = mmap(NULL, b.size, PROT_READ | PROT_WRITE,
                                 MAP_SHARED, fd, offset(fd, b.handle));
    CHECK(map != MAP_FAILED);
    volatile uint32_t *last = (volatile uint32_t *)((volatile char *)map + b.size - page);
    map[0] = 0x12345678;
    last[0] = 0x87654321;
    destroy(fd, b.handle);
    CHECK(close(fd) == 0);
    CHECK(munmap((void *)((volatile char *)map + page), b.size - 2 * page) == 0);
    int ready[2], ack[2];
    CHECK(pipe(ready) == 0 && pipe(ack) == 0);
    pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        CHECK(close(ready[0]) == 0 && close(ack[1]) == 0);
        CHECK(map[0] == 0x12345678 && last[0] == 0x87654321);
        map[0] = 0xaabbccdd;
        CHECK(write(ready[1], "x", 1) == 1);
        char token;
        CHECK(read(ack[0], &token, 1) == 1);
        CHECK(last[0] == 0x55aa55aa);
        CHECK(munmap((void *)map, page) == 0 && munmap((void *)last, page) == 0);
        _exit(0);
    }
    CHECK(close(ready[1]) == 0 && close(ack[0]) == 0);
    char token;
    CHECK(read(ready[0], &token, 1) == 1);
    CHECK(map[0] == 0xaabbccdd && last[0] == 0x87654321);
    last[0] = 0x55aa55aa;
    CHECK(munmap((void *)map, page) == 0 && munmap((void *)last, page) == 0);
    /* Only the child mapping now owns the backing. */
    CHECK(write(ack[1], "x", 1) == 1);
    int status;
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    CHECK(close(ready[0]) == 0 && close(ack[1]) == 0);
    puts("DRM_LIFETIME_MAPPING_OK");
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "usage: %s isolation|mapping [/dev/dri/card0]\n", argv[0]);
        return 2;
    }
    const char *path = argc == 3 ? argv[2] : "/dev/dri/card0";
    if (!strcmp(argv[1], "isolation")) isolation(path);
    else if (!strcmp(argv[1], "mapping")) mapping(path);
    else return 2;
    return 0;
}
