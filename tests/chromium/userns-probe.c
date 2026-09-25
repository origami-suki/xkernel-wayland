#define _GNU_SOURCE
#include <errno.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

/* One clone call per invocation; no Wayland, Chromium, mappings or sandbox setup. */
int main(int argc, char **argv)
{
    if (argc != 2 || (strcmp(argv[1], "plain") && strcmp(argv[1], "userns"))) {
        fprintf(stderr, "usage: %s plain|userns\n", argv[0]);
        return 2;
    }
    setvbuf(stdout, NULL, _IONBF, 0);
    alarm(8);

    uid_t ruid, euid, suid;
    gid_t rgid, egid, sgid;
    if (getresuid(&ruid, &euid, &suid) || getresgid(&rgid, &egid, &sgid)) {
        fprintf(stderr, "USERNS_SETUP_ERROR errno=%d detail=%s\n", errno, strerror(errno));
        return 2;
    }
    unsigned long flags = SIGCHLD;
    if (!strcmp(argv[1], "userns"))
        flags |= CLONE_NEWUSER;
    printf("USERNS_BEGIN mode=%s pid=%ld uid=%lu/%lu/%lu gid=%lu/%lu/%lu flags=%#lx\n",
           argv[1], (long)getpid(), (unsigned long)ruid, (unsigned long)euid,
           (unsigned long)suid, (unsigned long)rgid, (unsigned long)egid,
           (unsigned long)sgid, flags);

    /* No CLONE_VM: the child uses its copied stack and exits immediately. */
    errno = 0;
    long child = syscall(SYS_clone, flags, 0, 0, 0, 0);
    int clone_errno = errno;
    if (child == 0)
        _exit(0);
    if (child < 0) {
        int accepted = clone_errno == EPERM || clone_errno == EUSERS ||
                       clone_errno == EINVAL || clone_errno == ENOSPC;
        printf("USERNS_RESULT mode=%s clone_rc=%ld clone_errno=%d detail=%s "
               "chromium_userns_errno_accepted=%d child_created=0\n",
               argv[1], child, clone_errno, strerror(clone_errno), accepted);
        return 1;
    }

    int status = 0;
    pid_t waited;
    do {
        errno = 0;
        waited = waitpid((pid_t)child, &status, 0);
    } while (waited == -1 && errno == EINTR);
    int wait_errno = errno;
    int good = waited == child && WIFEXITED(status) && WEXITSTATUS(status) == 0;
    printf("USERNS_RESULT mode=%s clone_rc=%ld clone_errno=%d child_created=1 "
           "wait_rc=%ld wait_errno=%d wait_status=%#x child_exit_ok=%d\n",
           argv[1], child, clone_errno, (long)waited, wait_errno, status, good);
    return good ? 0 : 2;
}
