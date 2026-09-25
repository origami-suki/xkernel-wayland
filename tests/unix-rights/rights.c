#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Linux ABI probes: build the same source for the host and the supplied
 * AArch64 musl rootfs. Each case runs in a child with its own fd table. */
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line=%d check=%s errno=%d\n", __LINE__, #x, errno); _exit(1); } } while (0)

static int memory_file(char value) {
    int fd = memfd_create("xk-rights", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    CHECK(fd >= 0);
    CHECK(ftruncate(fd, 4096) == 0);
    CHECK(pwrite(fd, &value, 1, 0) == 1);
    return fd;
}

static ssize_t send_fds(int socket, const char *bytes, size_t length, const int *fds, size_t count) {
    union { struct cmsghdr align; char bytes[CMSG_SPACE(8 * sizeof(int))]; } control = {0};
    CHECK(count <= 8);
    struct iovec iov = { .iov_base = (void *)bytes, .iov_len = length };
    struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1 };
    if (count) {
        msg.msg_control = control.bytes;
        msg.msg_controllen = CMSG_SPACE(count * sizeof(int));
        struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
        c->cmsg_level = SOL_SOCKET;
        c->cmsg_type = SCM_RIGHTS;
        c->cmsg_len = CMSG_LEN(count * sizeof(int));
        memcpy(CMSG_DATA(c), fds, count * sizeof(int));
    }
    return sendmsg(socket, &msg, MSG_NOSIGNAL);
}

struct received { ssize_t bytes; int flags, fds[8]; size_t count, control_length; };
static struct received receive_fds(int socket, char *bytes, size_t length, size_t capacity, int flags) {
    union { struct cmsghdr align; char bytes[CMSG_SPACE(8 * sizeof(int))]; } control = {0};
    CHECK(capacity <= sizeof(control.bytes));
    struct iovec iov = { .iov_base = bytes, .iov_len = length };
    struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = capacity ? control.bytes : NULL, .msg_controllen = capacity };
    struct received result = {0};
    result.bytes = recvmsg(socket, &msg, flags);
    CHECK(result.bytes >= 0);
    result.flags = msg.msg_flags;
    result.control_length = msg.msg_controllen;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
        CHECK(c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS);
        CHECK(c->cmsg_len >= CMSG_LEN(0));
        size_t n = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
        CHECK(result.count + n <= 8);
        memcpy(result.fds + result.count, CMSG_DATA(c), n * sizeof(int));
        result.count += n;
    }
    printf("RECV bytes=%zd fds=%zu flags=%#x control=%zu\n", result.bytes, result.count, result.flags, result.control_length);
    return result;
}

static void close_received(struct received r) {
    for (size_t i = 0; i < r.count; ++i) CHECK(close(r.fds[i]) == 0);
}

static void wait_ok(pid_t pid) {
    int status;
    CHECK(waitpid(pid, &status, 0) == pid);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
}

static void basic(void) {
    int sv[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    pid_t child = fork();
    CHECK(child >= 0);
    if (!child) {
        close(sv[0]);
        char mark;
        struct received r = receive_fds(sv[1], &mark, 1, CMSG_SPACE(sizeof(int)), 0);
        CHECK(r.bytes == 1 && mark == 'F' && r.count == 1);
        int descriptor_flags = fcntl(r.fds[0], F_GETFD);
        CHECK(descriptor_flags >= 0 && !(descriptor_flags & FD_CLOEXEC));
        char *p = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, r.fds[0], 0);
        CHECK(p != MAP_FAILED && p[0] == 'A');
        CHECK(lseek(r.fds[0], 17, SEEK_SET) == 17);
        p[0] = 'B';
        CHECK(send(sv[1], "B", 1, MSG_NOSIGNAL) == 1);
        CHECK(recv(sv[1], &mark, 1, 0) == 1 && mark == 'C' && p[1] == 'C');
        CHECK(munmap(p, 4096) == 0);
        close_received(r);
        close(sv[1]);
        _exit(0);
    }
    close(sv[1]);
    /* Created after fork: inheritance cannot counterfeit SCM_RIGHTS success. */
    int fd = memory_file('A');
    char *p = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    CHECK(p != MAP_FAILED);
    int duplicate = dup(fd);
    CHECK(duplicate >= 0);
    CHECK(send_fds(sv[0], "F", 1, &fd, 1) == 1);
    CHECK(close(fd) == 0);
    char ack;
    CHECK(recv(sv[0], &ack, 1, 0) == 1 && ack == 'B' && p[0] == 'B');
    CHECK(lseek(duplicate, 0, SEEK_CUR) == 17);
    CHECK(close(duplicate) == 0);
    p[1] = 'C';
    CHECK(send(sv[0], "C", 1, MSG_NOSIGNAL) == 1);
    wait_ok(child);
    CHECK(munmap(p, 4096) == 0);
    close(sv[0]);
}

static void multiple(int type, size_t capacity, size_t expected, int receive_flags) {
    int sv[2], fds[3];
    CHECK(socketpair(AF_UNIX, type, 0, sv) == 0);
    for (int i = 0; i < 3; ++i) fds[i] = memory_file('a' + i);
    CHECK(send_fds(sv[0], "M", 1, fds, 3) == 1);
    for (int i = 0; i < 3; ++i) close(fds[i]);
    char b;
    struct received r = receive_fds(sv[1], &b, 1, capacity, receive_flags);
    CHECK(r.bytes == 1 && b == 'M' && r.count == expected);
    CHECK(!!(r.flags & MSG_CTRUNC) == (expected < 3));
    CHECK(!!(r.flags & MSG_CMSG_CLOEXEC) == !!(receive_flags & MSG_CMSG_CLOEXEC));
    size_t control_length = expected ? CMSG_SPACE(expected * sizeof(int)) : 0;
    if (control_length > capacity) control_length = capacity;
    CHECK(r.control_length == control_length);
    for (size_t i = 0; i < r.count; ++i) {
        CHECK(pread(r.fds[i], &b, 1, 0) == 1 && b == 'a' + (int)i);
        int descriptor_flags = fcntl(r.fds[i], F_GETFD);
        CHECK(descriptor_flags >= 0);
        CHECK(!!(descriptor_flags & FD_CLOEXEC) == !!(receive_flags & MSG_CMSG_CLOEXEC));
    }
    close_received(r);
    close(sv[0]); close(sv[1]);
}
static void multi(void) { multiple(SOCK_STREAM, CMSG_SPACE(3 * sizeof(int)), 3, 0); }
static void cloexec(void) { multiple(SOCK_STREAM, CMSG_SPACE(3 * sizeof(int)), 3, MSG_CMSG_CLOEXEC); }
static void truncation(void) { multiple(SOCK_STREAM, CMSG_LEN(sizeof(int)), 1, 0); }
static void no_control(void) { multiple(SOCK_STREAM, 0, 0, 0); }
static void dgram(void) { multiple(SOCK_DGRAM, CMSG_SPACE(3 * sizeof(int)), 3, 0); }

static void barrier(void) {
    int sv[2], fd = memory_file('K');
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    CHECK(send_fds(sv[0], "aaaa", 4, NULL, 0) == 4);
    CHECK(send_fds(sv[0], "F", 1, &fd, 1) == 1);
    CHECK(send_fds(sv[0], "zzzz", 4, NULL, 0) == 4);
    char b[32];
    struct received r = receive_fds(sv[1], b, sizeof(b), CMSG_SPACE(sizeof(int)), 0);
    CHECK(r.bytes == 5 && !memcmp(b, "aaaaF", 5) && r.count == 1);
    close_received(r);
    r = receive_fds(sv[1], b, sizeof(b), CMSG_SPACE(sizeof(int)), 0);
    CHECK(r.bytes == 4 && !memcmp(b, "zzzz", 4) && r.count == 0);
    close(fd); close(sv[0]); close(sv[1]);
}

static void partial(void) {
    int sv[2], fd = memory_file('K');
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    CHECK(send_fds(sv[0], "ABCDE", 5, &fd, 1) == 5);
    CHECK(send(sv[0], "zz", 2, MSG_NOSIGNAL) == 2);
    char b[8];
    struct received r = receive_fds(sv[1], b, 2, CMSG_SPACE(sizeof(int)), 0);
    CHECK(r.bytes == 2 && !memcmp(b, "AB", 2) && r.count == 1);
    close_received(r);
    r = receive_fds(sv[1], b, sizeof(b), CMSG_SPACE(sizeof(int)), 0);
    CHECK(r.bytes == 5 && !memcmp(b, "CDEzz", 5) && r.count == 0);
    close(fd); close(sv[0]); close(sv[1]);
}

static void seals(void) {
    int fd = memory_file('S');
    char *p = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    CHECK(p != MAP_FAILED);
    CHECK(fcntl(fd, F_ADD_SEALS, F_SEAL_SHRINK | F_SEAL_GROW) == 0);
    int seal_flags = fcntl(fd, F_GET_SEALS);
    CHECK(seal_flags >= 0 && (seal_flags & (F_SEAL_SHRINK | F_SEAL_GROW)) == (F_SEAL_SHRINK | F_SEAL_GROW));
    CHECK(ftruncate(fd, 0) == -1 && errno == EPERM);
    CHECK(ftruncate(fd, 8192) == -1 && errno == EPERM);
    CHECK(ftruncate(fd, 4096) == 0);
    struct stat st;
    CHECK(fstat(fd, &st) == 0 && st.st_size == 4096 && p[0] == 'S');
    CHECK(fcntl(fd, F_ADD_SEALS, F_SEAL_WRITE) == -1 && errno == EBUSY);
    CHECK(munmap(p, 4096) == 0);
    CHECK(fcntl(fd, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_SEAL) == 0);
    CHECK(pwrite(fd, "X", 1, 0) == -1 && errno == EPERM);
    close(fd);
}

static void discard(void) {
    int anchor = open("/dev/null", O_RDONLY);
    CHECK(anchor >= 0);
    for (int i = 0; i < 64; ++i) {
        int sv[2], fd = memory_file('D');
        CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
        CHECK(send_fds(sv[0], "D", 1, &fd, 1) == 1);
        close(fd);
        if (i % 2) { char b; CHECK(read(sv[1], &b, 1) == 1 && b == 'D'); }
        /* Alternate plain read (discard rights) and unread receiver close. */
        close(sv[1]); close(sv[0]);
    }
    int next = open("/dev/null", O_RDONLY);
    CHECK(next == anchor + 1);
    close(next); close(anchor);
}

/* A socket peer supplies an observable last-reference release oracle. */
static void lifetime(int mode) {
    int sv[2], witness[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, witness) == 0);
    int refs[2] = {witness[0], witness[0]};
    CHECK(send_fds(sv[0], "L", 1, refs, mode == 2 ? 2 : 1) == 1);
    close(witness[0]);
    char byte;
    CHECK(recv(witness[1], &byte, 1, MSG_DONTWAIT) == -1 && errno == EAGAIN);
    if (mode == 0) CHECK(read(sv[1], &byte, 1) == 1);
    if (mode == 1 || mode == 2) {
        struct received r = receive_fds(sv[1], &byte, 1,
            mode == 2 ? CMSG_LEN(sizeof(int)) : 0, 0);
        CHECK(r.flags & MSG_CTRUNC);
        CHECK(r.count == (mode == 2 ? 1u : 0u));
        close_received(r);
    }
    close(sv[1]);
    CHECK(recv(witness[1], &byte, 1, MSG_DONTWAIT) == 0);
    close(witness[1]); close(sv[0]);
}
static void release_read(void) { lifetime(0); }
static void release_no_control(void) { lifetime(1); }
static void release_truncated(void) { lifetime(2); }
static void release_close(void) { lifetime(3); }

static void peer_exit(void) {
    int sv[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    pid_t child = fork();
    CHECK(child >= 0);
    if (!child) {
        close(sv[0]);
        char byte;
        struct received r = receive_fds(sv[1], &byte, 1, CMSG_SPACE(sizeof(int)), 0);
        CHECK(r.count == 1);
        _exit(0); /* Process exit must release the received file. */
    }
    close(sv[1]);
    int witness[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, witness) == 0);
    CHECK(send_fds(sv[0], "X", 1, &witness[0], 1) == 1);
    close(witness[0]);
    wait_ok(child);
    char byte;
    CHECK(recv(witness[1], &byte, 1, MSG_DONTWAIT) == 0);
    close(witness[1]); close(sv[0]);
}

static void consecutive(void) {
    int sv[2], fd = memory_file('C');
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    CHECK(send_fds(sv[0], "1", 1, &fd, 1) == 1);
    CHECK(send_fds(sv[0], "2", 1, &fd, 1) == 1);
    for (int i = 0; i < 2; ++i) {
        char b[8];
        struct received r = receive_fds(sv[1], b, sizeof(b), CMSG_SPACE(2 * sizeof(int)), 0);
        CHECK(r.bytes == 1 && b[0] == '1' + i && r.count == 1);
        close_received(r);
    }
    close(fd); close(sv[0]); close(sv[1]);
}

static void seal_write(void) {
    int fd = memory_file('W');
    CHECK(fcntl(fd, F_ADD_SEALS, F_SEAL_WRITE) == 0);
    CHECK(pwrite(fd, "X", 1, 0) == -1 && errno == EPERM);
    char byte;
    CHECK(pread(fd, &byte, 1, 0) == 1 && byte == 'W');
    close(fd);
}

static void bad_fd(void) {
    int sv[2], fd = -1;
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    CHECK(send_fds(sv[0], "E", 1, &fd, 1) == -1 && errno == EBADF);
    char b;
    CHECK(recv(sv[1], &b, 1, MSG_DONTWAIT) == -1 && errno == EAGAIN);
    close(sv[0]); close(sv[1]);
}

int main(int argc, char **argv) {
    const struct { const char *name; void (*run)(void); } cases[] = {
        {"basic", basic}, {"multi", multi}, {"cloexec", cloexec},
        {"truncation", truncation}, {"no-control", no_control},
        {"barrier", barrier}, {"partial", partial}, {"seals", seals},
        {"discard", discard}, {"bad-fd", bad_fd}, {"dgram", dgram},
        {"release-read", release_read}, {"release-no-control", release_no_control},
        {"release-truncated", release_truncated}, {"release-close", release_close},
        {"peer-exit", peer_exit}, {"consecutive", consecutive}, {"seal-write", seal_write}
    };
    setvbuf(stdout, NULL, _IONBF, 0);
    signal(SIGPIPE, SIG_IGN);
    int failed = 0, selected = 0;
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        if (argc > 1 && strcmp(argv[1], "all") && strcmp(argv[1], cases[i].name)) continue;
        ++selected;
        printf("RIGHTS_BEGIN %s\n", cases[i].name);
        pid_t child = fork();
        CHECK(child >= 0);
        if (!child) { alarm(15); cases[i].run(); _exit(0); }
        int status;
        CHECK(waitpid(child, &status, 0) == child);
        int ok = WIFEXITED(status) && WEXITSTATUS(status) == 0;
        printf("RIGHTS_RESULT %s %s status=%d\n", cases[i].name, ok ? "PASS" : "FAIL", status);
        failed += !ok;
    }
    CHECK(selected > 0);
    printf("RIGHTS_SUMMARY cases=%d failed=%d\n", selected, failed);
    return failed ? 1 : 0;
}
