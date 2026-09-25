#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <unistd.h>

static pthread_mutex_t gate = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t ready = PTHREAD_COND_INITIALIZER;
static pthread_cond_t release_worker = PTHREAD_COND_INITIALIZER;
static int worker_ready;
static int worker_released;
static pid_t worker_tid;
static int failures;

static void check_pthread(int rc, const char *operation)
{
    if (rc) {
        printf("SETUP_FAIL operation=%s rc=%d\n", operation, rc);
        exit(2);
    }
}

static void query_tid(const char *caller, const char *target, pid_t tid,
                      int expected_policy)
{
    struct sched_param param = { .sched_priority = -999 };
    errno = 0;
    long rc = syscall(SYS_sched_getparam, tid, &param);
    int error = errno;
    printf("SCHED caller=%s target=%s tid=%d operation=getparam rc=%ld errno=%d priority=%d\n",
           caller, target, tid, rc, error, param.sched_priority);
    if (expected_policy >= 0 ? (rc != 0 || param.sched_priority != 0)
                             : (rc != -1 || error != -expected_policy))
        ++failures;

    errno = 0;
    rc = syscall(SYS_sched_getscheduler, tid);
    error = errno;
    printf("SCHED caller=%s target=%s tid=%d operation=getscheduler rc=%ld errno=%d\n",
           caller, target, tid, rc, error);
    if (expected_policy >= 0 ? rc != expected_policy
                             : (rc != -1 || error != -expected_policy))
        ++failures;
}

static void query_pthread(const char *caller, const char *target, pthread_t thread,
                          int expected_policy)
{
    int policy = -999;
    struct sched_param param = { .sched_priority = -999 };
    int rc = pthread_getschedparam(thread, &policy, &param);
    printf("PTHREAD caller=%s target=%s rc=%d policy=%d priority=%d\n",
           caller, target, rc, policy, param.sched_priority);
    if (rc || policy != expected_policy || param.sched_priority != 0)
        ++failures;
}

static void query_pointer(const char *target, pid_t tid, void *pointer, int expected)
{
    errno = 0;
    long rc = syscall(SYS_sched_getparam, tid, pointer);
    int error = errno;
    printf("SCHED_POINTER target=%s tid=%d pointer=%p rc=%ld errno=%d expected=%d\n",
           target, tid, pointer, rc, error, expected);
    if (rc != -1 || error != expected)
        ++failures;
}

static void *worker(void *unused)
{
    (void)unused;
    check_pthread(pthread_mutex_lock(&gate), "worker-lock");
    worker_tid = (pid_t)syscall(SYS_gettid);
    /* Give the worker a distinct policy so a successful leader lookup is
       insufficient: getpid() must still select the main thread. */
    struct sched_param param = { .sched_priority = 0 };
    if (syscall(SYS_sched_setscheduler, 0, SCHED_BATCH, &param) != 0) {
        perror("worker sched_setscheduler");
        exit(2);
    }
    printf("IDENTITY caller=worker pid=%d tid=%d\n", getpid(), worker_tid);
    query_tid("worker", "zero", 0, SCHED_BATCH);
    query_tid("worker", "self-tid", worker_tid, SCHED_BATCH);
    query_tid("worker", "process-pid", getpid(), SCHED_OTHER);
    query_pthread("worker", "self", pthread_self(), SCHED_BATCH);
    query_pointer("live-worker-null", worker_tid, NULL, EINVAL);
    query_pointer("live-worker-bad", worker_tid, (void *)1, EFAULT);
    worker_ready = 1;
    check_pthread(pthread_cond_signal(&ready), "worker-ready");
    while (!worker_released)
        check_pthread(pthread_cond_wait(&release_worker, &gate), "worker-wait");
    check_pthread(pthread_mutex_unlock(&gate), "worker-unlock");
    return NULL;
}

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    pid_t main_tid = (pid_t)syscall(SYS_gettid);
    printf("IDENTITY caller=main pid=%d tid=%d\n", getpid(), main_tid);
    query_tid("main", "zero", 0, SCHED_OTHER);
    query_tid("main", "self-tid", main_tid, SCHED_OTHER);
    query_tid("main", "process-pid", getpid(), SCHED_OTHER);
    query_pthread("main", "self", pthread_self(), SCHED_OTHER);
    query_tid("main", "negative-tid", -1, -EINVAL);
    query_tid("main", "min-tid", INT_MIN, -EINVAL);
    query_pointer("zero-null", 0, NULL, EINVAL);
    query_pointer("negative-null", -1, NULL, EINVAL);
    query_pointer("missing-null", INT_MAX, NULL, EINVAL);
    query_pointer("zero-bad", 0, (void *)1, EFAULT);
    query_pointer("negative-bad", -1, (void *)1, EINVAL);
    query_pointer("missing-bad", INT_MAX, (void *)1, ESRCH);

    pthread_t child;
    check_pthread(pthread_mutex_lock(&gate), "main-lock");
    check_pthread(pthread_create(&child, NULL, worker, NULL), "create");
    while (!worker_ready)
        check_pthread(pthread_cond_wait(&ready, &gate), "main-wait");
    query_tid("main", "live-worker-tid", worker_tid, SCHED_BATCH);
    query_pthread("main", "live-worker", child, SCHED_BATCH);
    worker_released = 1;
    check_pthread(pthread_cond_signal(&release_worker), "release");
    check_pthread(pthread_mutex_unlock(&gate), "main-unlock");
    check_pthread(pthread_join(child, NULL), "join");
    query_tid("main", "joined-worker-tid", worker_tid, -ESRCH);
    query_tid("main", "missing-tid", INT_MAX, -ESRCH);
    printf("SCHED_PROBE failures=%d\n", failures);
    return failures ? 1 : 0;
}
