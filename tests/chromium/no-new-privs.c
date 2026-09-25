#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/fsuid.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

/* Each public mode starts in a fresh process with no_new_privs unset. */
#define REQUIRE(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "NNP_FAIL line=%d condition=%s errno=%d detail=%s\n", \
                __LINE__, #condition, errno, strerror(errno)); \
        exit(1); \
    } \
} while (0)

static int get_nnp(void)
{
    int value = prctl(PR_GET_NO_NEW_PRIVS, 0UL, 0UL, 0UL, 0UL);
    REQUIRE(value == 0 || value == 1);
    return value;
}

static void set_nnp(void)
{
    errno = 0;
    REQUIRE(prctl(PR_SET_NO_NEW_PRIVS, 1UL, 0UL, 0UL, 0UL) == 0);
    REQUIRE(get_nnp() == 1);
}

static void expect_invalid(int option, const unsigned long args[4], int state)
{
    errno = 0;
    int rc = prctl(option, args[0], args[1], args[2], args[3]);
    int saved_errno = errno;
    printf("NNP_INVALID option=%d args=%lu/%lu/%lu/%lu rc=%d errno=%d\n",
           option, args[0], args[1], args[2], args[3], rc, saved_errno);
    REQUIRE(rc == -1 && saved_errno == EINVAL);
    REQUIRE(get_nnp() == state);
}

static void invalid_arguments(int state)
{
    for (int index = 0; index < 4; ++index) {
        unsigned long args[4] = {0, 0, 0, 0};
        args[index] = 1;
        expect_invalid(PR_GET_NO_NEW_PRIVS, args, state);
    }
    for (int index = 1; index < 4; ++index) {
        unsigned long args[4] = {1, 0, 0, 0};
        args[index] = 1;
        expect_invalid(PR_SET_NO_NEW_PRIVS, args, state);
    }
    const unsigned long zero[4] = {0, 0, 0, 0};
    const unsigned long two[4] = {2, 0, 0, 0};
    expect_invalid(PR_SET_NO_NEW_PRIVS, zero, state);
    expect_invalid(PR_SET_NO_NEW_PRIVS, two, state);
}

static void write_byte(int fd)
{
    ssize_t rc;
    do {
        rc = write(fd, "x", 1);
    } while (rc == -1 && errno == EINTR);
    REQUIRE(rc == 1);
}

static void read_byte(int fd)
{
    char byte = 0;
    ssize_t rc;
    do {
        rc = read(fd, &byte, 1);
    } while (rc == -1 && errno == EINTR);
    REQUIRE(rc == 1 && byte == 'x');
}

static void wait_child(pid_t child)
{
    int status = 0;
    pid_t rc;
    do {
        rc = waitpid(child, &status, 0);
    } while (rc == -1 && errno == EINTR);
    printf("NNP_WAIT child=%ld rc=%ld status=%#x\n", (long)child, (long)rc, status);
    REQUIRE(rc == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
}

static void fork_mode(void)
{
    int ready[2], ack[2];
    REQUIRE(pipe(ready) == 0);
    REQUIRE(pipe(ack) == 0);
    pid_t child = fork();
    REQUIRE(child >= 0);
    if (child == 0) {
        alarm(20);
        close(ready[0]);
        close(ack[1]);
        REQUIRE(get_nnp() == 0);
        set_nnp();
        write_byte(ready[1]);
        read_byte(ack[0]);
        REQUIRE(get_nnp() == 1);
        _exit(0);
    }
    close(ready[1]);
    close(ack[0]);
    read_byte(ready[0]);
    REQUIRE(get_nnp() == 0);
    write_byte(ack[1]);
    wait_child(child);
    close(ready[0]);
    close(ack[1]);
    REQUIRE(get_nnp() == 0);

    set_nnp();
    child = fork();
    REQUIRE(child >= 0);
    if (child == 0) {
        alarm(20);
        REQUIRE(get_nnp() == 1);
        _exit(0);
    }
    wait_child(child);
    REQUIRE(get_nnp() == 1);
}

struct thread_sync {
    int ready[2];
    int ack[2];
};

static void *isolated_thread(void *argument)
{
    struct thread_sync *sync = argument;
    REQUIRE(get_nnp() == 0);
    set_nnp();
    write_byte(sync->ready[1]);
    read_byte(sync->ack[0]);
    REQUIRE(get_nnp() == 1);
    return NULL;
}

static void *inheriting_thread(void *argument)
{
    int expected = *(const int *)argument;
    REQUIRE(get_nnp() == expected);
    return NULL;
}

static void check_thread_inheritance(int expected)
{
    pthread_t thread;
    REQUIRE(pthread_create(&thread, NULL, inheriting_thread, &expected) == 0);
    REQUIRE(pthread_join(thread, NULL) == 0);
}

static void thread_mode(void)
{
    struct thread_sync sync;
    REQUIRE(pipe(sync.ready) == 0);
    REQUIRE(pipe(sync.ack) == 0);
    pthread_t thread;
    REQUIRE(pthread_create(&thread, NULL, isolated_thread, &sync) == 0);
    read_byte(sync.ready[0]);
    REQUIRE(get_nnp() == 0);
    write_byte(sync.ack[1]);
    REQUIRE(pthread_join(thread, NULL) == 0);
    for (int index = 0; index < 2; ++index) {
        close(sync.ready[index]);
        close(sync.ack[index]);
    }
    REQUIRE(get_nnp() == 0);
    check_thread_inheritance(0);
    set_nnp();
    check_thread_inheritance(1);
    REQUIRE(get_nnp() == 1);
}

enum { RUID, EUID, SUID, FSUID, RGID, EGID, SGID, FSGID, ID_COUNT };

static void read_ids(unsigned long ids[ID_COUNT])
{
    uid_t real_uid, effective_uid, saved_uid;
    gid_t real_gid, effective_gid, saved_gid;
    REQUIRE(getresuid(&real_uid, &effective_uid, &saved_uid) == 0);
    REQUIRE(getresgid(&real_gid, &effective_gid, &saved_gid) == 0);
    ids[RUID] = real_uid;
    ids[EUID] = effective_uid;
    ids[SUID] = saved_uid;
    ids[FSUID] = (uid_t)setfsuid((uid_t)-1);
    ids[RGID] = real_gid;
    ids[EGID] = effective_gid;
    ids[SGID] = saved_gid;
    ids[FSGID] = (gid_t)setfsgid((gid_t)-1);
}

static void print_ids(const char *phase, const unsigned long ids[ID_COUNT])
{
    printf("NNP_IDS phase=%s uid=%lu/%lu/%lu/%lu gid=%lu/%lu/%lu/%lu\n", phase,
           ids[RUID], ids[EUID], ids[SUID], ids[FSUID],
           ids[RGID], ids[EGID], ids[SGID], ids[FSGID]);
}

static unsigned long parse_number(const char *value)
{
    char *end = NULL;
    errno = 0;
    unsigned long number = strtoul(value, &end, 10);
    REQUIRE(errno == 0 && end != value && *end == '\0' && number <= UINT_MAX);
    return number;
}

static void exec_child(int argc, char **argv)
{
    REQUIRE(argc == 4 + ID_COUNT);
    int expected_nnp = (int)parse_number(argv[3]);
    REQUIRE(expected_nnp == 0 || expected_nnp == 1);
    REQUIRE(get_nnp() == expected_nnp);
    unsigned long actual[ID_COUNT];
    read_ids(actual);
    print_ids("after-exec", actual);
    if (!strcmp(argv[2], "setid-control")) {
        REQUIRE(expected_nnp == 0);
        int unchanged = 1, applied = 1;
        for (int index = 0; index < ID_COUNT; ++index) {
            unsigned long before = parse_number(argv[4 + index]);
            unsigned long privileged = (index == RUID || index == RGID) ? before : 0;
            unchanged &= actual[index] == before;
            applied &= actual[index] == privileged;
        }
        REQUIRE(unchanged || applied);
        printf("NNP_CONTROL_COMPLETE mode=setid-control applied=%d unchanged=%d "
               "inherited_nnp=0 ids_checked=%d\n", applied, unchanged, ID_COUNT);
        return;
    }
    for (int index = 0; index < ID_COUNT; ++index)
        REQUIRE(actual[index] == parse_number(argv[4 + index]));
    printf("NNP_PASS mode=%s inherited_nnp=%d ids_checked=%d\n",
           argv[2], expected_nnp, ID_COUNT);
}

static void exec_mode(const char *path, const char *mode, int setid, int control)
{
    unsigned long expected[ID_COUNT];
    read_ids(expected);
    print_ids("before-exec", expected);
    if (setid) {
        struct stat info;
        REQUIRE(stat(path, &info) == 0);
        REQUIRE(S_ISREG(info.st_mode));
        REQUIRE(info.st_uid == 0 && info.st_gid == 0);
        REQUIRE((info.st_mode & 07777) == 06755);
        /* Equal non-root IDs make an unexpected saved/fs transition visible. */
        REQUIRE(expected[RUID] != 0 && expected[RGID] != 0);
        REQUIRE(expected[RUID] == expected[EUID] && expected[EUID] == expected[SUID]);
        REQUIRE(expected[SUID] == expected[FSUID]);
        REQUIRE(expected[RGID] == expected[EGID] && expected[EGID] == expected[SGID]);
        REQUIRE(expected[SGID] == expected[FSGID]);
    }
    if (!control) {
        set_nnp();
    }

    char values[ID_COUNT][32];
    char *arguments[ID_COUNT + 5];
    arguments[0] = (char *)path;
    arguments[1] = "exec-child";
    arguments[2] = (char *)mode;
    arguments[3] = control ? "0" : "1";
    for (int index = 0; index < ID_COUNT; ++index) {
        int length = snprintf(values[index], sizeof(values[index]), "%lu", expected[index]);
        REQUIRE(length > 0 && (size_t)length < sizeof(values[index]));
        arguments[4 + index] = values[index];
    }
    arguments[4 + ID_COUNT] = NULL;
    execv(path, arguments);
    REQUIRE(0 && "execv returned");
}

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    alarm(20);
    REQUIRE(argc >= 2);
    if (!strcmp(argv[1], "exec-child")) {
        exec_child(argc, argv);
        return 0;
    }
    printf("NNP_BEGIN mode=%s pid=%ld\n", argv[1], (long)getpid());
    REQUIRE(get_nnp() == 0);
    if (!strcmp(argv[1], "basic")) {
        REQUIRE(argc == 2);
        invalid_arguments(0);
        set_nnp();
        set_nnp();
        invalid_arguments(1);
    } else if (!strcmp(argv[1], "fork")) {
        REQUIRE(argc == 2);
        fork_mode();
    } else if (!strcmp(argv[1], "thread")) {
        REQUIRE(argc == 2);
        thread_mode();
    } else if (!strcmp(argv[1], "exec")) {
        REQUIRE(argc == 2 || argc == 3);
        exec_mode(argc == 3 ? argv[2] : argv[0], "exec", 0, 0);
    } else if (!strcmp(argv[1], "setid") || !strcmp(argv[1], "setid-control")) {
        REQUIRE(argc == 3);
        exec_mode(argv[2], argv[1], 1, !strcmp(argv[1], "setid-control"));
    } else {
        fprintf(stderr, "usage: %s basic|fork|thread|exec [self-path]|setid fixture|setid-control fixture\n", argv[0]);
        return 2;
    }
    printf("NNP_PASS mode=%s\n", argv[1]);
    return 0;
}
