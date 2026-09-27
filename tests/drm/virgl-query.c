/* Query real virtgpu UAPI support. No rendering or performance measurement. */
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include "drm.h"
#include "virtgpu_drm.h"

int main(int argc, char **argv) {
    if (argc > 2) {
        fprintf(stderr, "usage: %s [/dev/dri/card0 or renderD128]\n", argv[0]);
        return 2;
    }
    const char *path = argc == 2 ? argv[1] : "/dev/dri/card0";
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        printf("VIRGL_OPEN node=%s rc=-1 errno=%d\n", path, errno);
        return 2;
    }
    char name[128] = {0};
    struct drm_version version = {.name = name, .name_len = sizeof(name) - 1};
    errno = 0;
    int rc = ioctl(fd, DRM_IOCTL_VERSION, &version);
    printf("VIRGL_VERSION node=%s rc=%d errno=%d name=%s version=%d.%d.%d\n",
           path, rc, errno, name, version.version_major, version.version_minor,
           version.version_patchlevel);
    int available = 0;
    for (unsigned param = VIRTGPU_PARAM_3D_FEATURES;
         param <= VIRTGPU_PARAM_SUPPORTED_CAPSET_IDs; ++param) {
        uint64_t value = 0;
        struct drm_virtgpu_getparam query = {
            .param = param, .value = (uint64_t)(uintptr_t)&value
        };
        errno = 0;
        rc = ioctl(fd, DRM_IOCTL_VIRTGPU_GETPARAM, &query);
        printf("VIRGL_PARAM param=%u rc=%d errno=%d value=%" PRIu64 "\n",
               param, rc, errno, value);
        if (param == VIRTGPU_PARAM_3D_FEATURES) available = rc == 0 && value != 0;
    }
    /* Bounded queries are diagnostic only: opaque bytes do not prove execution. */
    unsigned readable_capsets = 0;
    for (unsigned capset = 1; capset <= 2; ++capset) {
        unsigned char bytes[4096] = {0};
        struct drm_virtgpu_get_caps query = {
            .cap_set_id = capset, .cap_set_ver = 0,
            .addr = (uint64_t)(uintptr_t)bytes, .size = sizeof(bytes)
        };
        errno = 0;
        rc = ioctl(fd, DRM_IOCTL_VIRTGPU_GET_CAPS, &query);
        printf("VIRGL_CAPSET id=%u version=0 capacity=%zu rc=%d errno=%d\n",
               capset, sizeof(bytes), rc, errno);
        if (rc == 0) {
            ++readable_capsets;
            printf("VIRGL_CAPSET_FIRST64 id=%u bytes=", capset);
            for (unsigned i = 0; i < 64; ++i) printf("%02x", bytes[i]);
            putchar('\n');
        }
    }
    close(fd);
    printf("VIRGL_QUERY_RESULT uapi_3d=%d readable_capsets=%u rendering_verified=0\n",
           available, readable_capsets);
    return available && readable_capsets ? 0 : 2;
}
