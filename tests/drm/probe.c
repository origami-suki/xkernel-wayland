#define _GNU_SOURCE
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <poll.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <drm.h>
#include <drm_mode.h>
#include <drm_fourcc.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "DRM_FAIL line=%d check=%s errno=%d (%s)\n", __LINE__, #x, errno, strerror(errno)); exit(1); } } while (0)
#define PTR(x) ((uint64_t)(uintptr_t)(x))
static int failures;
static void result(const char *name, int ok, int rc) {
    printf("DRM_CASE %s %s rc=%d errno=%d\n", name, ok ? "PASS" : "FAIL", rc, errno);
    failures += !ok;
}

static int query(int fd) {
    struct drm_version v = {0};
    errno = 0;
    int rc = ioctl(fd, DRM_IOCTL_VERSION, &v);
    result("version-zero", rc == 0 && v.name_len > 0, rc);
    char name[256], date[256], desc[256];
    memset(name, 0, sizeof(name)); memset(date, 0, sizeof(date)); memset(desc, 0, sizeof(desc));
    v = (struct drm_version){.name_len = 255, .name = name, .date_len = 255, .date = date, .desc_len = 255, .desc = desc};
    errno = 0; rc = ioctl(fd, DRM_IOCTL_VERSION, &v);
    result("version-full", rc == 0 && v.name_len > 0 && v.name_len < 255 && v.date_len < 255 && v.desc_len < 255, rc);
    printf("DRM_DRIVER name=%s date=%s desc=%s\n", name, date, desc);
    size_t name_len = v.name_len;
    unsigned char small[256]; memset(small, 0xa5, sizeof(small));
    v = (struct drm_version){.name_len = 1, .name = (char *)small};
    errno = 0; rc = ioctl(fd, DRM_IOCTL_VERSION, &v);
    int intact = small[0] == (unsigned char)name[0];
    for (size_t i = 1; i < sizeof(small); ++i) intact &= small[i] == 0xa5;
    result("version-short-canary", rc == 0 && v.name_len == name_len && intact, rc);
    v = (struct drm_version){.name_len = 10, .name = NULL};
    errno = 0; rc = ioctl(fd, DRM_IOCTL_VERSION, &v);
    result("version-null-capacity", rc == 0 && v.name_len == name_len, rc);
    v = (struct drm_version){.name_len = 1, .name = (char *)(uintptr_t)1};
    errno = 0; rc = ioctl(fd, DRM_IOCTL_VERSION, &v);
    result("version-invalid-pointer", rc == -1 && errno == EFAULT, rc);

    /* Linux initializes GET_UNIQUE's bus id through SET_VERSION. */
    struct drm_set_version set = {.drm_di_major = 1, .drm_di_minor = 4, .drm_dd_major = -1, .drm_dd_minor = -1};
    CHECK(ioctl(fd, DRM_IOCTL_SET_VERSION, &set) == 0);
    struct drm_unique unique = {0};
    errno = 0; rc = ioctl(fd, DRM_IOCTL_GET_UNIQUE, &unique);
    result("unique-zero", rc == 0, rc);
    char id[256] = {0};
    unique = (struct drm_unique){.unique_len = 255, .unique = id};
    errno = 0; rc = ioctl(fd, DRM_IOCTL_GET_UNIQUE, &unique);
    result("unique-full", rc == 0 && unique.unique_len < 255, rc);
    size_t id_len = unique.unique_len;
    printf("DRM_UNIQUE len=%zu value=%s\n", id_len, id);
    if (id_len > 1) {
        memset(small, 0xa5, sizeof(small));
        unique = (struct drm_unique){.unique_len = 1, .unique = (char *)small};
        errno = 0; rc = ioctl(fd, DRM_IOCTL_GET_UNIQUE, &unique);
        intact = 1;
        for (size_t i = 0; i < sizeof(small); ++i) intact &= small[i] == 0xa5;
        result("unique-short-no-copy", rc == 0 && unique.unique_len == id_len && intact, rc);
        unique = (struct drm_unique){.unique_len = id_len, .unique = (char *)(uintptr_t)1};
        errno = 0; rc = ioctl(fd, DRM_IOCTL_GET_UNIQUE, &unique);
        result("unique-invalid-pointer", rc == -1 && errno == EFAULT, rc);
    }
    printf("DRM_QUERY_RESULT failures=%d\n", failures);
    return failures ? 1 : 0;
}

struct buffer { struct drm_mode_create_dumb dumb; uint32_t fb; uint32_t *map; };
static struct buffer buffer_create(int fd, unsigned w, unsigned h, uint32_t color) {
    struct buffer b = {.dumb = {.width = w, .height = h, .bpp = 32}};
    CHECK(ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &b.dumb) == 0);
    CHECK(b.dumb.pitch >= w * 4 && b.dumb.size >= (uint64_t)b.dumb.pitch * h);
    struct drm_mode_map_dumb m = {.handle = b.dumb.handle};
    CHECK(ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &m) == 0);
    b.map = mmap(NULL, b.dumb.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, m.offset);
    CHECK(b.map != MAP_FAILED);
    for (unsigned y = 0; y < h; ++y)
        for (unsigned x = 0; x < w; ++x)
            b.map[y * (b.dumb.pitch / 4) + x] = color;
    struct drm_mode_fb_cmd2 fb = {.width = w, .height = h, .pixel_format = DRM_FORMAT_XRGB8888};
    fb.handles[0] = b.dumb.handle; fb.pitches[0] = b.dumb.pitch;
    CHECK(ioctl(fd, DRM_IOCTL_MODE_ADDFB2, &fb) == 0);
    b.fb = fb.fb_id;
    printf("DRM_BUFFER handle=%u fb=%u pitch=%u size=%llu\n", b.dumb.handle, b.fb, b.dumb.pitch, (unsigned long long)b.dumb.size);
    return b;
}

static void frame(const char *name) {
    /* Functional-observation holds only, never performance samples. */
    usleep(250000);
    printf("__ICT_FRAME_%s__\n", name);
    sleep(3);
}

static void draw(int fd) {
    CHECK(ioctl(fd, DRM_IOCTL_SET_MASTER, 0) == 0);
    struct drm_mode_card_res res = {0};
    CHECK(ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res) == 0);
    CHECK(res.count_connectors > 0 && res.count_connectors <= 16 && res.count_crtcs > 0 && res.count_crtcs <= 16);
    uint32_t connectors[16], crtcs[16], encoders[16], fbs[16];
    CHECK(res.count_encoders <= 16 && res.count_fbs <= 16);
    res.connector_id_ptr = PTR(connectors); res.crtc_id_ptr = PTR(crtcs);
    res.encoder_id_ptr = PTR(encoders); res.fb_id_ptr = PTR(fbs);
    CHECK(ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res) == 0);
    struct drm_mode_get_connector con = {0};
    for (uint32_t i = 0; i < res.count_connectors; ++i) {
        con = (struct drm_mode_get_connector){.connector_id = connectors[i]};
        CHECK(ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &con) == 0);
        if (con.connection == 1 && con.count_modes) break;
    }
    CHECK(con.connection == 1 && con.count_modes > 0 && con.count_modes <= 64);
    CHECK(con.count_props <= 64 && con.count_encoders <= 16);
    struct drm_mode_modeinfo modes[64]; uint32_t props[64], con_encoders[16]; uint64_t values[64];
    con.modes_ptr = PTR(modes); con.props_ptr = PTR(props); con.prop_values_ptr = PTR(values); con.encoders_ptr = PTR(con_encoders);
    CHECK(ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &con) == 0);
    struct drm_mode_get_encoder encoder = {.encoder_id = con.encoder_id ? con.encoder_id : con_encoders[0]};
    CHECK(ioctl(fd, DRM_IOCTL_MODE_GETENCODER, &encoder) == 0);
    uint32_t crtc = 0;
    for (uint32_t i = 0; i < res.count_crtcs; ++i) if (encoder.possible_crtcs & (1u << i)) { crtc = crtcs[i]; break; }
    CHECK(crtc);
    struct drm_mode_modeinfo mode = modes[0];
    printf("DRM_MODE connector=%u crtc=%u width=%u height=%u name=%s\n", con.connector_id, crtc, mode.hdisplay, mode.vdisplay, mode.name);
    struct buffer red = buffer_create(fd, mode.hdisplay, mode.vdisplay, 0x00e03c32);
    struct buffer green = buffer_create(fd, mode.hdisplay, mode.vdisplay, 0x0032a852);
    struct drm_mode_crtc set = {.set_connectors_ptr = PTR(&con.connector_id), .count_connectors = 1,
        .crtc_id = crtc, .fb_id = red.fb, .mode_valid = 1, .mode = mode};
    CHECK(ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &set) == 0);
    frame("drm-red");
    struct drm_mode_crtc_page_flip flip = {.crtc_id = crtc, .fb_id = green.fb,
        .flags = DRM_MODE_PAGE_FLIP_EVENT, .user_data = 0x1c72026};
    CHECK(ioctl(fd, DRM_IOCTL_MODE_PAGE_FLIP, &flip) == 0);
    struct pollfd event = {.fd = fd, .events = POLLIN};
    CHECK(poll(&event, 1, 3000) == 1 && (event.revents & POLLIN));
    unsigned char bytes[256]; ssize_t n = read(fd, bytes, sizeof(bytes));
    CHECK(n >= (ssize_t)sizeof(struct drm_event_vblank));
    struct drm_event_vblank vblank; memcpy(&vblank, bytes, sizeof(vblank));
    CHECK(vblank.base.type == DRM_EVENT_FLIP_COMPLETE && vblank.user_data == flip.user_data);
    printf("DRM_FLIP sequence=%u event_bytes=%zd\n", vblank.sequence, n);
    frame("drm-green");
    set.fb_id = 0; set.count_connectors = 0; set.mode_valid = 0;
    CHECK(ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &set) == 0);
    struct buffer *buffers[] = {&red, &green};
    for (size_t i = 0; i < 2; ++i) {
        struct buffer *b = buffers[i];
        CHECK(ioctl(fd, DRM_IOCTL_MODE_RMFB, &b->fb) == 0);
        CHECK(munmap(b->map, b->dumb.size) == 0);
        struct drm_mode_destroy_dumb destroy = {.handle = b->dumb.handle};
        CHECK(ioctl(fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy) == 0);
    }
    puts("DRM_DRAW_OK");
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    int fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
    CHECK(fd >= 0);
    printf("DRM_ABI version=%zu resources=%zu connector=%zu\n", sizeof(struct drm_version), sizeof(struct drm_mode_card_res), sizeof(struct drm_mode_get_connector));
    int rc = 0;
    if (argc > 1 && !strcmp(argv[1], "draw")) draw(fd); else rc = query(fd);
    CHECK(close(fd) == 0);
    return rc;
}
