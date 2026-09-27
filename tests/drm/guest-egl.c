/* Use the installed guest Mesa libraries unchanged. Require virgl and verify
 * an GLES framebuffer clear by pixel readback. No window or timing claims. */
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <gbm.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "GUEST_EGL_FAIL line=%d check=%s errno=%d\n", __LINE__, #x, errno); exit(1); } } while (0)
#define LOAD(lib, name) __typeof__(&name) p_##name = (__typeof__(&name))dlsym(lib, #name); CHECK(p_##name)

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc > 2) return 2;
    const char *node = argc == 2 ? argv[1] : "/dev/dri/renderD128";
    void *gbm_lib = dlopen("libgbm.so.1", RTLD_NOW | RTLD_GLOBAL);
    void *egl_lib = dlopen("libEGL.so.1", RTLD_NOW | RTLD_GLOBAL);
    void *gles_lib = dlopen("libGLESv2.so.2", RTLD_NOW | RTLD_GLOBAL);
    if (!gbm_lib || !egl_lib || !gles_lib) {
        fprintf(stderr, "GUEST_EGL_DLOPEN_FAIL %s\n", dlerror());
        return 1;
    }
    LOAD(gbm_lib, gbm_create_device); LOAD(gbm_lib, gbm_device_destroy);
    LOAD(egl_lib, eglGetProcAddress); LOAD(egl_lib, eglInitialize);
    LOAD(egl_lib, eglBindAPI); LOAD(egl_lib, eglChooseConfig);
    LOAD(egl_lib, eglCreateContext); LOAD(egl_lib, eglMakeCurrent);
    LOAD(egl_lib, eglDestroyContext); LOAD(egl_lib, eglTerminate);
    LOAD(egl_lib, eglQueryString); LOAD(egl_lib, eglGetError);
    LOAD(gles_lib, glGetString); LOAD(gles_lib, glGetError);
    LOAD(gles_lib, glGenTextures); LOAD(gles_lib, glBindTexture);
    LOAD(gles_lib, glTexImage2D); LOAD(gles_lib, glTexParameteri);
    LOAD(gles_lib, glGenFramebuffers); LOAD(gles_lib, glBindFramebuffer);
    LOAD(gles_lib, glFramebufferTexture2D); LOAD(gles_lib, glCheckFramebufferStatus);
    LOAD(gles_lib, glClearColor); LOAD(gles_lib, glClear); LOAD(gles_lib, glReadPixels);
    LOAD(gles_lib, glFinish); LOAD(gles_lib, glDeleteTextures); LOAD(gles_lib, glDeleteFramebuffers);
    int fd = open(node, O_RDWR | O_CLOEXEC);
    CHECK(fd >= 0);
    struct gbm_device *gbm = p_gbm_create_device(fd);
    CHECK(gbm);
    PFNEGLGETPLATFORMDISPLAYEXTPROC get_display =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)p_eglGetProcAddress("eglGetPlatformDisplayEXT");
    CHECK(get_display);
    EGLDisplay display = get_display(EGL_PLATFORM_GBM_KHR, gbm, NULL);
    EGLint major, minor;
    CHECK(display != EGL_NO_DISPLAY && p_eglInitialize(display, &major, &minor));
    CHECK(p_eglBindAPI(EGL_OPENGL_ES_API));
    const EGLint attributes[] = {EGL_SURFACE_TYPE, 0, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_NONE};
    EGLConfig config;
    EGLint count = 0;
    CHECK(p_eglChooseConfig(display, attributes, &config, 1, &count) && count == 1);
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    EGLContext context = p_eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    CHECK(context != EGL_NO_CONTEXT && p_eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context));
    const char *renderer = (const char *)p_glGetString(GL_RENDERER);
    const char *version = (const char *)p_glGetString(GL_VERSION);
    CHECK(renderer && version);
    printf("GUEST_EGL node=%s egl=%d.%d vendor=%s renderer=%s version=%s\n",
           node, major, minor, p_eglQueryString(display, EGL_VENDOR), renderer, version);
    CHECK(strstr(renderer, "virgl") && !strstr(renderer, "llvmpipe") && !strstr(renderer, "SwiftShader"));
    GLuint texture, framebuffer;
    p_glGenTextures(1, &texture); p_glBindTexture(GL_TEXTURE_2D, texture);
    p_glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    p_glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    p_glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 8, 8, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    p_glGenFramebuffers(1, &framebuffer); p_glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    p_glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
    CHECK(p_glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE);
    p_glClearColor(1, 0, 0, 1); p_glClear(GL_COLOR_BUFFER_BIT); p_glFinish();
    unsigned char pixels[8 * 8 * 4] = {0};
    p_glReadPixels(0, 0, 8, 8, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    CHECK(p_glGetError() == GL_NO_ERROR);
    for (unsigned i = 0; i < 8 * 8; ++i)
        CHECK(pixels[4*i] == 255 && pixels[4*i+1] == 0 && pixels[4*i+2] == 0 && pixels[4*i+3] == 255);
    p_glDeleteFramebuffers(1, &framebuffer); p_glDeleteTextures(1, &texture);
    CHECK(p_eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT));
    CHECK(p_eglDestroyContext(display, context) && p_eglTerminate(display));
    p_gbm_device_destroy(gbm); CHECK(close(fd) == 0);
    CHECK(dlclose(gles_lib) == 0 && dlclose(egl_lib) == 0 && dlclose(gbm_lib) == 0);
    puts("GUEST_EGL_VIRGL_READBACK_OK pixels=64");
    return 0;
}
