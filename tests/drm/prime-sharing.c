/* Export a real dma-buf, pass its file over SCM_RIGHTS, then import/map it
 * after the creator GEM handle and DRM file have closed. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include "drm.h"
#include "drm_mode.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "DRM_PRIME_FAIL line=%d check=%s errno=%d\n", __LINE__, #x, errno); exit(1); } } while (0)
/* Linux dma-buf UAPI: read/write start=3, read/write end=7. */
struct sync_request { uint64_t flags; };
#define DMA_SYNC _IOW('b', 0, struct sync_request)

static void sync_buffer(int fd, uint64_t flags) {
    struct sync_request request = {.flags = flags};
    CHECK(ioctl(fd, DMA_SYNC, &request) == 0);
}

static void send_file(int socket, int fd) {
    char byte = 'x';
    struct iovec iov = {.iov_base = &byte, .iov_len = 1};
    union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(int))]; } control = {0};
    struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1,
                         .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes)};
    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET; cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(cmsg), &fd, sizeof(fd));
    CHECK(sendmsg(socket, &msg, 0) == 1);
}

static int receive_file(int socket) {
    char byte;
    struct iovec iov = {.iov_base = &byte, .iov_len = 1};
    union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(int))]; } control = {0};
    struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1,
                         .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes)};
    CHECK(recvmsg(socket, &msg, MSG_CMSG_CLOEXEC) == 1 && !(msg.msg_flags & MSG_CTRUNC));
    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    CHECK(cmsg && cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS
          && cmsg->cmsg_len == CMSG_LEN(sizeof(int)));
    int fd;
    memcpy(&fd, CMSG_DATA(cmsg), sizeof(fd));
    CHECK(fd >= 0 && (fcntl(fd, F_GETFD) & FD_CLOEXEC));
    return fd;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc > 2) return 2;
    const char *node = argc == 2 ? argv[1] : "/dev/dri/card0";
    int card = open(node, O_RDWR | O_CLOEXEC);
    CHECK(card >= 0);
    struct drm_mode_create_dumb object = {.width = 64, .height = 64, .bpp = 32};
    CHECK(ioctl(card, DRM_IOCTL_MODE_CREATE_DUMB, &object) == 0);
    struct drm_prime_handle exported = {.handle = object.handle, .flags = DRM_CLOEXEC | DRM_RDWR, .fd = -1};
    CHECK(ioctl(card, DRM_IOCTL_PRIME_HANDLE_TO_FD, &exported) == 0);
    CHECK(exported.fd >= 0 && (fcntl(exported.fd, F_GETFD) & FD_CLOEXEC));
    volatile uint32_t *parent_map = mmap(NULL, object.size, PROT_READ | PROT_WRITE,
                                       MAP_SHARED, exported.fd, 0);
    CHECK(parent_map != MAP_FAILED);
    int sockets[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0);
    pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        CHECK(close(sockets[0]) == 0 && close(card) == 0 && close(exported.fd) == 0);
        CHECK(munmap((void *)parent_map, object.size) == 0);
        int received = receive_file(sockets[1]);
        int importer = open(node, O_RDWR | O_CLOEXEC);
        CHECK(importer >= 0);
        struct drm_prime_handle imported = {.fd = received};
        CHECK(ioctl(importer, DRM_IOCTL_PRIME_FD_TO_HANDLE, &imported) == 0);
        CHECK(imported.handle != 0);
        struct drm_prime_handle again = {.fd = received};
        CHECK(ioctl(importer, DRM_IOCTL_PRIME_FD_TO_HANDLE, &again) == 0);
        CHECK(again.handle == imported.handle);
        struct drm_mode_map_dumb offset = {.handle = imported.handle};
        CHECK(ioctl(importer, DRM_IOCTL_MODE_MAP_DUMB, &offset) == 0);
        volatile uint32_t *gem_map = mmap(NULL, object.size, PROT_READ | PROT_WRITE,
                                        MAP_SHARED, importer, offset.offset);
        volatile uint32_t *dma_map = mmap(NULL, object.size, PROT_READ | PROT_WRITE,
                                        MAP_SHARED, received, 0);
        CHECK(gem_map != MAP_FAILED && dma_map != MAP_FAILED);
        sync_buffer(received, 3);
        CHECK(gem_map[0] == UINT32_C(0x13579bdf) && dma_map[0] == gem_map[0]);
        dma_map[0] = UINT32_C(0x2468ace0);
        CHECK(gem_map[0] == UINT32_C(0x2468ace0));
        sync_buffer(received, 7);
        struct drm_gem_close close_handle = {.handle = imported.handle};
        CHECK(ioctl(importer, DRM_IOCTL_GEM_CLOSE, &close_handle) == 0);
        CHECK(close(importer) == 0 && close(received) == 0);
        CHECK(gem_map[0] == UINT32_C(0x2468ace0) && dma_map[0] == gem_map[0]);
        CHECK(munmap((void *)gem_map, object.size) == 0 && munmap((void *)dma_map, object.size) == 0);
        CHECK(write(sockets[1], "y", 1) == 1);
        CHECK(close(sockets[1]) == 0);
        _exit(0);
    }
    CHECK(close(sockets[1]) == 0);
    sync_buffer(exported.fd, 3);
    parent_map[0] = UINT32_C(0x13579bdf);
    sync_buffer(exported.fd, 7);
    struct drm_gem_close close_handle = {.handle = object.handle};
    CHECK(ioctl(card, DRM_IOCTL_GEM_CLOSE, &close_handle) == 0 && close(card) == 0);
    send_file(sockets[0], exported.fd);
    char byte;
    CHECK(read(sockets[0], &byte, 1) == 1 && byte == 'y');
    sync_buffer(exported.fd, 3);
    CHECK(parent_map[0] == UINT32_C(0x2468ace0));
    sync_buffer(exported.fd, 7);
    CHECK(close(exported.fd) == 0);
    CHECK(parent_map[0] == UINT32_C(0x2468ace0));
    CHECK(munmap((void *)parent_map, object.size) == 0 && close(sockets[0]) == 0);
    int status;
    CHECK(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
    puts("DRM_PRIME_SCM_RIGHTS_LIFETIME_OK");
    return 0;
}
