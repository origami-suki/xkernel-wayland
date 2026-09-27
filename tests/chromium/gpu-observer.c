/* CDP pipe observer for the unchanged guest Chromium. JSON/NUL framing follows
 * Puppeteer's PipeTransport. Raw SystemInfo.getInfo is evidence for host review,
 * never a substitute for original-page QEMU monitor pixels. */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define LIMIT (1024u * 1024u)
static volatile sig_atomic_t stopped;
static unsigned char message[LIMIT + 1];
static size_t message_size;
static unsigned char incoming[4096];
static size_t incoming_size, incoming_position;
static int close_reply_failed;

static void stop(int sig) { (void)sig; stopped = 1; }
static double now(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) != 0) return -1;
    return t.tv_sec + t.tv_nsec / 1e9;
}
static int send_command(int fd, const char *json) {
    size_t left = strlen(json) + 1;
    while (left) {
        ssize_t n = write(fd, json, left);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        json += n; left -= (size_t)n;
    }
    return 0;
}
static const char *root_field(const char *json, const char *key) {
    while (isspace((unsigned char)*json)) ++json;
    if (*json != '{') return NULL;
    unsigned depth = 0;
    for (const char *p = json; *p; ++p) {
        if (*p == '{' || *p == '[') { ++depth; continue; }
        if (*p == '}' || *p == ']') {
            if (!depth || --depth == 0) return NULL;
            continue;
        }
        if (*p != '"') continue;
        const char *begin = ++p;
        while (*p && *p != '"') {
            if (*p == '\\') { if (!p[1]) return NULL; ++p; }
            ++p;
        }
        if (!*p) return NULL;
        const char *value = p + 1;
        while (isspace((unsigned char)*value)) ++value;
        if (depth == 1 && *value == ':' && (size_t)(p - begin) == strlen(key)
                && !memcmp(begin, key, (size_t)(p - begin))) {
            ++value;
            while (isspace((unsigned char)*value)) ++value;
            return value;
        }
    }
    return NULL;
}
static int reply_id(const char *json) {
    const char *id = root_field(json, "id");
    if (!id) return -1;
    char *end;
    long value = strtol(id, &end, 10);
    while (isspace((unsigned char)*end)) ++end;
    return end != id && (*end == ',' || *end == '}') && value >= 0 && value <= 3
           ? (int)value : -1;
}
static int receive_reply(int fd, int wanted) {
    double start = now();
    if (start < 0) return -1;
    double deadline = start + 240;
    while (!stopped && now() < deadline) {
        if (incoming_position == incoming_size) {
            struct pollfd p = {.fd = fd, .events = POLLIN};
            int ready = poll(&p, 1, wanted < 0 ? 0 : 1000);
            if (ready < 0 && errno == EINTR) continue;
            if (ready < 0) return -1;
            if (!ready) { if (wanted < 0) return 0; continue; }
            ssize_t n = read(fd, incoming, sizeof(incoming));
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) return -1;
            incoming_size = (size_t)n;
            incoming_position = 0;
        }
        while (incoming_position < incoming_size) {
            unsigned char byte = incoming[incoming_position++];
            if (byte) {
                if (message_size == LIMIT) return -1;
                message[message_size++] = byte;
                continue;
            }
            message[message_size] = 0;
            int id = reply_id((const char *)message);
            printf("CDP_MESSAGE_BEGIN id=%d\n%s\nCDP_MESSAGE_END id=%d\n", id, message, id);
            if (id == 3 && (root_field((const char *)message, "error")
                    || !root_field((const char *)message, "result")))
                close_reply_failed = 1;
            message_size = 0;
            if (wanted >= 0 && id == wanted)
                return root_field((const char *)message, "error")
                       || !root_field((const char *)message, "result") ? -1 : 0;
        }
    }
    return -1;
}

static void dump_log(const char *path) {
    printf("CHROMIUM_GPU_LOG_BEGIN %s\n", path);
    FILE *file = fopen(path, "r");
    if (file) {
        unsigned char bytes[4096];
        size_t total = 0, count;
        while (total < LIMIT && (count = fread(bytes, 1, sizeof(bytes), file)) != 0) {
            fwrite(bytes, 1, count, stdout);
            total += count;
        }
        fclose(file);
    }
    printf("\nCHROMIUM_GPU_LOG_END %s\n", path);
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "usage: %s browser-log [observation-frames:1..30]\n", argv[0]);
        return 2;
    }
    char *end = NULL;
    long frames = argc == 3 ? strtol(argv[2], &end, 10) : 18;
    if (frames < 1 || frames > 30 || (argc == 3 && (end == argv[2] || *end))) return 2;
    signal(SIGPIPE, SIG_IGN); signal(SIGTERM, stop); signal(SIGINT, stop);
    int request[2], response[2];
    if (pipe2(request, O_CLOEXEC) || pipe2(response, O_CLOEXEC)) return 1;
    int log = open(argv[1], O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (log < 0) return 1;
    pid_t browser = fork();
    if (browser < 0) return 1;
    if (browser == 0) {
        int input = fcntl(request[0], F_DUPFD_CLOEXEC, 10);
        int output = fcntl(response[1], F_DUPFD_CLOEXEC, 10);
        int logfile = fcntl(log, F_DUPFD_CLOEXEC, 10);
        if (input < 0 || output < 0 || logfile < 0 || dup2(input, 3) < 0 || dup2(output, 4) < 0
                || dup2(logfile, 1) < 0 || dup2(logfile, 2) < 0) _exit(125);
        int fds[] = {request[0], request[1], response[0], response[1], log, input, output, logfile};
        for (unsigned i = 0; i < sizeof(fds) / sizeof(fds[0]); ++i)
            if (fds[i] > 4) close(fds[i]);
        execl("/usr/bin/chromium", "chromium", "--ozone-platform=wayland",
              "--user-data-dir=/home/kiosk/.config/chromium-virgl-m2",
              "--no-first-run", "--no-default-browser-check", "--no-sandbox",
              "--ipc-connection-timeout=120", "--remote-debugging-pipe",
              "--disable-software-rasterizer", "--use-gl=angle", "--use-angle=gl",
              "--enable-logging=stderr", "file:///opt/ict-testpages/index.html", (char *)NULL);
        _exit(127);
    }
    close(request[0]); close(response[1]); close(log);
    printf("CHROMIUM_GPU_OBSERVER_PID %ld\nCHROMIUM_GPU_BROWSER_PID %d\n", (long)getpid(), browser);
    int result = 1, status = 0, reaped = 0;
    if (send_command(request[1], "{\"id\":1,\"method\":\"SystemInfo.getInfo\"}") == 0
            && receive_reply(response[0], 1) == 0
            && send_command(request[1], "{\"id\":2,\"method\":\"Target.getTargets\"}") == 0
            && receive_reply(response[0], 2) == 0) {
        result = 0;
        for (long frame = 1; frame <= frames && !stopped; ++frame) {
            /* Drain unsolicited CDP messages throughout functional holds. */
            for (unsigned second = 0; second < 10 && !stopped; ++second) {
                sleep(1);
                if (receive_reply(response[0], -1) != 0) { result = 1; break; }
            }
            if (result) break;
            pid_t done = waitpid(browser, &status, WNOHANG);
            if (done != 0) { reaped = done == browser; result = 1; break; }
            /* Early absence of a scanout is a startup observation. Save final
             * GPU state and logs before the one required monitor request. */
            if (frame == frames) {
                puts("CHROMIUM_GPU_FINAL_INFO_BEGIN");
                if (send_command(request[1], "{\"id\":1,\"method\":\"SystemInfo.getInfo\"}") != 0
                        || receive_reply(response[0], 1) != 0
                        || send_command(request[1], "{\"id\":2,\"method\":\"Target.getTargets\"}") != 0
                        || receive_reply(response[0], 2) != 0) result = 1;
                puts("CHROMIUM_GPU_FINAL_INFO_END");
                dump_log(argv[1]);
                dump_log("/tmp/m2-004-weston.log");
                dump_log("/tmp/m2-004-session.log");
                dump_log("/tmp/m2-004-seat.log");
                if (!result) printf("\n__ICT_FRAME_virgl-index-%ld__\n", frame);
                sleep(3);
            }
        }
    }
    if (stopped) result = 1;
    if (!reaped && send_command(request[1], "{\"id\":3,\"method\":\"Browser.close\"}") != 0) result = 1;
    for (unsigned i = 0; !reaped && i < 30; ++i) {
        pid_t done = waitpid(browser, &status, WNOHANG);
        if (done == browser) { reaped = 1; break; }
        if (done < 0 && errno != EINTR) { result = 1; break; }
        (void)receive_reply(response[0], -1);
        sleep(1);
    }
    if (!reaped) {
        puts("CHROMIUM_GPU_FORCED_KILL");
        kill(browser, SIGKILL);
        if (waitpid(browser, &status, 0) != browser) status = -1;
        result = 1;
    }
    (void)receive_reply(response[0], -1);
    if (close_reply_failed) {
        puts("CHROMIUM_GPU_BROWSER_CLOSE_REPLY_FAILED");
        result = 1;
    }
    close(request[1]);
    close(response[0]);
    printf("CHROMIUM_GPU_BROWSER_WAIT status=%d normal_exit=%d code=%d\n", status,
           status >= 0 && WIFEXITED(status), status >= 0 && WIFEXITED(status) ? WEXITSTATUS(status) : -1);
    if (status < 0 || !WIFEXITED(status) || WEXITSTATUS(status) != 0) result = 1;
    if (!result) puts("CHROMIUM_GPU_OBSERVATION_DONE rendering_acceptance_pending_monitor_and_info=1");
    return result;
}
