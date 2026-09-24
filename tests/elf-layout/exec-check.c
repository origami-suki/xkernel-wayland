#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

/* Report exec errors separately from crashes and preserve a surviving parent. */
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    int expected_failure = !strcmp(argv[1], "reject");
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 2; }
    if (!pid) {
        char *args[] = {argv[2], NULL};
        execv(args[0], args);
        int error = errno;
        printf("EXEC_REJECT path=%s errno=%d message=%s\n", args[0], error, strerror(error));
        fflush(stdout);
        _exit(error > 0 && error < 100 ? 100 + error : 255);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) != pid) { perror("waitpid"); return 2; }
    printf("EXEC_PARENT_ALIVE path=%s exited=%d status=%d signal=%d\n", argv[2],
           WIFEXITED(status), WIFEXITED(status) ? WEXITSTATUS(status) : -1,
           WIFSIGNALED(status) ? WTERMSIG(status) : 0);
    if (expected_failure)
        return WIFEXITED(status) && WEXITSTATUS(status) > 100 && WEXITSTATUS(status) < 200 ? 0 : 1;
    return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : 1;
}
