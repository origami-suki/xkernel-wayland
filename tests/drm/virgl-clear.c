/* Execute a virgl clear through the Linux virtgpu ABI and read back pixels.
 * Protocol references: virglrenderer 1.3.0 src/virgl_protocol.h/virgl_hw.h,
 * Mesa virgl_encode.c. This is a functional probe, not a performance test. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include "drm.h"
#include "virtgpu_drm.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "VIRGL_CLEAR_FAIL line=%d check=%s errno=%d\n", __LINE__, #x, errno); exit(1); } } while (0)
#define PTR(x) ((uint64_t)(uintptr_t)(x))
#define CMD(cmd, object, length) ((cmd) | ((object) << 8) | ((length) << 16))

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc > 2) return 2;
    const char *node = argc == 2 ? argv[1] : "/dev/dri/renderD128";
    int fd = open(node, O_RDWR | O_CLOEXEC);
    CHECK(fd >= 0);
    uint32_t features = 0;
    struct drm_virtgpu_getparam param = {.param = VIRTGPU_PARAM_3D_FEATURES, .value = PTR(&features)};
    CHECK(ioctl(fd, DRM_IOCTL_VIRTGPU_GETPARAM, &param) == 0 && features != 0);
    const unsigned width = 64, height = 64, stride = width * 4;
    struct drm_virtgpu_resource_create resource = {
        .target = 2, /* PIPE_TEXTURE_2D */
        .format = 1, /* VIRGL_FORMAT_B8G8R8A8_UNORM */
        .bind = (1u << 1), /* VIRGL_BIND_RENDER_TARGET */
        .width = width, .height = height, .depth = 1, .array_size = 1,
        .size = stride * height, .stride = stride,
    };
    CHECK(ioctl(fd, DRM_IOCTL_VIRTGPU_RESOURCE_CREATE, &resource) == 0);
    CHECK(resource.bo_handle != 0 && resource.res_handle != 0);
    printf("VIRGL_RESOURCE handle=%u resource=%u size=%u stride=%u\n",
           resource.bo_handle, resource.res_handle, resource.size, resource.stride);
    struct drm_virtgpu_resource_info info = {.bo_handle = resource.bo_handle};
    CHECK(ioctl(fd, DRM_IOCTL_VIRTGPU_RESOURCE_INFO, &info) == 0);
    CHECK(info.res_handle == resource.res_handle && info.size >= stride * height);
    struct drm_virtgpu_map map = {.handle = resource.bo_handle};
    CHECK(ioctl(fd, DRM_IOCTL_VIRTGPU_MAP, &map) == 0);
    volatile uint32_t *pixels = mmap(NULL, info.size, PROT_READ | PROT_WRITE,
                                    MAP_SHARED, fd, map.offset);
    CHECK(pixels != MAP_FAILED);
    memset((void *)pixels, 0xa5, info.size);
    /* A surface, one color attachment, and red clear. Each command header
     * encodes payload dwords (excluding the header), object type and opcode. */
    const uint32_t commands[] = {
        CMD(1, 8, 5), 1, resource.res_handle, 1, 0, 0,
        CMD(5, 0, 3), 1, 0, 1,
        CMD(7, 0, 8), 4, 0x3f800000, 0, 0, 0x3f800000, 0, 0x3ff00000, 0,
    };
    uint32_t handles[] = {resource.bo_handle};
    struct drm_virtgpu_execbuffer exec = {
        .size = sizeof(commands), .command = PTR(commands),
        .bo_handles = PTR(handles), .num_bo_handles = 1, .fence_fd = -1,
    };
    CHECK(ioctl(fd, DRM_IOCTL_VIRTGPU_EXECBUFFER, &exec) == 0);
    struct drm_virtgpu_3d_wait wait = {.handle = resource.bo_handle};
    CHECK(ioctl(fd, DRM_IOCTL_VIRTGPU_WAIT, &wait) == 0);
    struct drm_virtgpu_3d_transfer_from_host transfer = {
        .bo_handle = resource.bo_handle,
        .box = {.w = width, .h = height, .d = 1},
        /* Traditional non-blob virtgpu transfers use protocol-default strides. */
    };
    CHECK(ioctl(fd, DRM_IOCTL_VIRTGPU_TRANSFER_FROM_HOST, &transfer) == 0);
    CHECK(ioctl(fd, DRM_IOCTL_VIRTGPU_WAIT, &wait) == 0);
    unsigned mismatches = 0;
    for (unsigned i = 0; i < width * height; ++i) {
        if (pixels[i] != UINT32_C(0xffff0000)) {
            if (mismatches < 4) printf("VIRGL_PIXEL_MISMATCH index=%u value=%08" PRIx32 "\n", i, pixels[i]);
            ++mismatches;
        }
    }
    printf("VIRGL_CLEAR_RESULT pixels=%u mismatches=%u expected=ffff0000\n", width * height, mismatches);
    CHECK(mismatches == 0);
    /* Mappings survive the last userspace handle and fd. */
    struct drm_gem_close close_handle = {.handle = resource.bo_handle};
    CHECK(ioctl(fd, DRM_IOCTL_GEM_CLOSE, &close_handle) == 0);
    CHECK(close(fd) == 0);
    CHECK(pixels[0] == UINT32_C(0xffff0000));
    CHECK(munmap((void *)pixels, info.size) == 0);
    puts("VIRGL_CLEAR_OK");
    return 0;
}
