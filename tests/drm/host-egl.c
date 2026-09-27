/* Check the hardware GL renderer on an explicitly selected host render node.
 * This proves host EGL availability only, never guest acceleration. */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/gl.h>
#include <fcntl.h>
#include <gbm.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s /dev/dri/renderD...\n", argv[0]);
        return 2;
    }
    int status = 1;
    int fd = open(argv[1], O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        perror("open render node");
        return 1;
    }
    struct gbm_device *gbm = gbm_create_device(fd);
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLContext context = EGL_NO_CONTEXT;
    if (!gbm) {
        fprintf(stderr, "gbm_create_device failed\n");
        goto done;
    }
    PFNEGLGETPLATFORMDISPLAYEXTPROC get_display =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!get_display) {
        fprintf(stderr, "eglGetPlatformDisplayEXT unavailable\n");
        goto done;
    }
    display = get_display(EGL_PLATFORM_GBM_KHR, gbm, NULL);
    EGLint major, minor, count;
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, &major, &minor)) {
        fprintf(stderr, "eglInitialize failed: %#x\n", eglGetError());
        goto done;
    }
    if (!eglBindAPI(EGL_OPENGL_API)) {
        fprintf(stderr, "eglBindAPI failed: %#x\n", eglGetError());
        goto done;
    }
    const EGLint attributes[] = {
        EGL_SURFACE_TYPE, 0, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT, EGL_NONE
    };
    EGLConfig config;
    if (!eglChooseConfig(display, attributes, &config, 1, &count) || count != 1) {
        fprintf(stderr, "eglChooseConfig failed: %#x\n", eglGetError());
        goto done;
    }
    context = eglCreateContext(display, config, EGL_NO_CONTEXT, NULL);
    if (context == EGL_NO_CONTEXT ||
        !eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context)) {
        fprintf(stderr, "create/current GL context failed: %#x\n", eglGetError());
        goto done;
    }
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    const char *vendor = (const char *)glGetString(GL_VENDOR);
    const char *version = (const char *)glGetString(GL_VERSION);
    if (!renderer || !vendor || !version) {
        fprintf(stderr, "glGetString failed\n");
        goto done;
    }
    printf("HOST_RENDER_NODE=%s\nEGL_VERSION=%d.%d\nGL_VENDOR=%s\n"
           "GL_RENDERER=%s\nGL_VERSION=%s\n", argv[1], major, minor,
           vendor, renderer, version);
    if (strstr(renderer, "llvmpipe") || strstr(renderer, "softpipe") ||
        strstr(renderer, "Software") || strstr(renderer, "SwiftShader")) {
        fprintf(stderr, "software renderer rejected\n");
        goto done;
    }
    status = 0;
done:
    if (display != EGL_NO_DISPLAY) {
        eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (context != EGL_NO_CONTEXT) eglDestroyContext(display, context);
        eglTerminate(display);
    }
    if (gbm) gbm_device_destroy(gbm);
    close(fd);
    return status;
}
