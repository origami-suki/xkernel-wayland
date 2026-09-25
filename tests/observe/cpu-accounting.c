#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Broad correctness bounds, not a benchmark or a clock precision claim. */
static int failed, checks;
static volatile sig_atomic_t fired;
static uint64_t ns(clockid_t id) {
    struct timespec t;
    if (clock_gettime(id, &t)) { perror("clock_gettime"); exit(2); }
    return (uint64_t)t.tv_sec * 1000000000ULL + t.tv_nsec;
}
static void check(const char *name, int ok, uint64_t wall, uint64_t cpu) {
    printf("CPU_ACCOUNTING %s %s wall_ns=%llu cpu_ns=%llu\n", ok ? "PASS" : "FAIL", name,
           (unsigned long long)wall, (unsigned long long)cpu);
    checks++; failed += !ok;
}
static void sleep_ms(long ms) {
    struct timespec t = {ms / 1000, ms % 1000 * 1000000};
    while (nanosleep(&t, &t) && errno == EINTR) {}
}
static void burn_ms(unsigned ms) {
    uint64_t end = ns(CLOCK_MONOTONIC) + (uint64_t)ms * 1000000;
    volatile uint64_t v = 3;
    do { for (int i = 0; i < 10000; ++i) v = v * 1664525 + 1013904223; }
    while (ns(CLOCK_MONOTONIC) < end);
}
static void signal_handler(int signo) { (void)signo; fired++; }
static void timer_check(clockid_t clock, const char *sleep_name, const char *busy_name) {
    timer_t timer;
    struct sigevent ev = {.sigev_notify = SIGEV_SIGNAL, .sigev_signo = SIGUSR1};
    if (timer_create(clock, &ev, &timer)) {
        if (clock == CLOCK_THREAD_CPUTIME_ID && errno == EINVAL) {
            puts("CPU_ACCOUNTING_UNSUPPORTED thread_cpu_timer EINVAL");
            return;
        }
        perror("timer_create"); exit(2);
    }
    struct itimerspec spec = {.it_value = {.tv_nsec = 80000000}};
    fired = 0;
    if (timer_settime(timer, 0, &spec, NULL)) { perror("timer_settime"); exit(2); }
    sleep_ms(250);
    check(sleep_name, fired == 0, 250000000, fired);
    uint64_t end = ns(CLOCK_MONOTONIC) + 2000000000ULL;
    while (!fired && ns(CLOCK_MONOTONIC) < end) burn_ms(20);
    check(busy_name, fired != 0, 0, fired);
    timer_delete(timer);
}
static pthread_barrier_t barrier;
struct worker { uint64_t wall, cpu; };
static void *worker_run(void *arg) {
    struct worker *w = arg;
    if (sched_getcpu() != 0) {
        fputs("CPU_ACCOUNTING_ERROR worker did not inherit CPU 0 affinity\n", stderr);
        _exit(2);
    }
    pthread_barrier_wait(&barrier);
    uint64_t wall = ns(CLOCK_MONOTONIC), cpu = ns(CLOCK_THREAD_CPUTIME_ID);
    burn_ms(500);
    w->cpu = ns(CLOCK_THREAD_CPUTIME_ID) - cpu;
    w->wall = ns(CLOCK_MONOTONIC) - wall;
    if (sched_getcpu() != 0) {
        fputs("CPU_ACCOUNTING_ERROR worker left CPU 0\n", stderr);
        _exit(2);
    }
    return NULL;
}
static uint64_t timeval_ns(struct timeval t) { return (uint64_t)t.tv_sec * 1000000000 + t.tv_usec * 1000; }
int main(void) {
    setbuf(stdout, NULL);
    uint64_t w = ns(CLOCK_MONOTONIC), c = ns(CLOCK_THREAD_CPUTIME_ID);
    sleep_ms(600);
    uint64_t wd = ns(CLOCK_MONOTONIC)-w, cd = ns(CLOCK_THREAD_CPUTIME_ID)-c;
    check("sleep_excluded", cd < wd/5, wd, cd);
    w=ns(CLOCK_MONOTONIC); c=ns(CLOCK_THREAD_CPUTIME_ID);
    burn_ms(250);
    wd=ns(CLOCK_MONOTONIC)-w; cd=ns(CLOCK_THREAD_CPUTIME_ID)-c;
    check("busy_accumulates", cd > 25000000 && cd < wd+20000000, wd, cd);

    int fd[2]; if (pipe(fd)) return 2;
    pid_t pid=fork(); if (pid<0) return 2;
    if (!pid) { close(fd[0]); sleep_ms(350); if(write(fd[1], "x", 1)!=1) _exit(2); _exit(0); }
    close(fd[1]); char ch; w=ns(CLOCK_MONOTONIC); c=ns(CLOCK_THREAD_CPUTIME_ID);
    ssize_t n=read(fd[0], &ch, 1); wd=ns(CLOCK_MONOTONIC)-w; cd=ns(CLOCK_THREAD_CPUTIME_ID)-c;
    int status; waitpid(pid,&status,0); close(fd[0]);
    check("blocking_read_excluded", n==1 && WIFEXITED(status) && WEXITSTATUS(status)==0 && cd<wd/5,wd,cd);

    struct sigaction sa={.sa_handler=signal_handler}; sigemptyset(&sa.sa_mask); sigaction(SIGUSR1,&sa,NULL);
    timer_check(CLOCK_THREAD_CPUTIME_ID,"thread_timer_ignores_sleep","thread_timer_fires_on_cpu");
    timer_check(CLOCK_PROCESS_CPUTIME_ID,"process_timer_ignores_sleep","process_timer_fires_on_cpu");

    struct rusage before,after;
    getrusage(RUSAGE_SELF,&before);
    for (unsigned i=0;i<40000;i++) (void)syscall(SYS_gettid);
    getrusage(RUSAGE_SELF,&after);
    uint64_t system=timeval_ns(after.ru_stime)-timeval_ns(before.ru_stime);
    check("syscalls_charge_kernel",system>0,0,system);
    getrusage(RUSAGE_SELF,&before); burn_ms(200); getrusage(RUSAGE_SELF,&after);
    uint64_t user=timeval_ns(after.ru_utime)-timeval_ns(before.ru_utime);
    check("computation_charges_user",user>10000000,0,user);

    cpu_set_t mask; CPU_ZERO(&mask); CPU_SET(0,&mask);
    if (sched_setaffinity(0,sizeof(mask),&mask)) { perror("sched_setaffinity"); return 2; }
    pthread_t threads[3]; struct worker workers[3]={{0}};
    pthread_barrier_init(&barrier,NULL,4);
    uint64_t p0=ns(CLOCK_PROCESS_CPUTIME_ID);
    for(int i=0;i<3;i++) if(pthread_create(&threads[i],NULL,worker_run,&workers[i])) return 2;
    w=ns(CLOCK_MONOTONIC); pthread_barrier_wait(&barrier);
    uint64_t sum=0;
    for(int i=0;i<3;i++) { pthread_join(threads[i],NULL); sum+=workers[i].cpu;
        check("same_cpu_ready_wait_excluded",workers[i].cpu>10000000 && workers[i].cpu<workers[i].wall*3/4,workers[i].wall,workers[i].cpu); }
    wd=ns(CLOCK_MONOTONIC)-w; uint64_t pd=ns(CLOCK_PROCESS_CPUTIME_ID)-p0;
    check("single_cpu_sum_bounded",sum<wd*5/4,wd,sum);
    check("exited_threads_retained",pd+5000000>=sum && pd<sum+150000000, sum,pd);
    pthread_barrier_destroy(&barrier);
    c=ns(CLOCK_PROCESS_CPUTIME_ID); sleep_ms(300); cd=ns(CLOCK_PROCESS_CPUTIME_ID)-c;
    check("process_after_join_ignores_sleep",cd<60000000,300000000,cd);
    printf("CPU_ACCOUNTING_SUMMARY checks=%d failed=%d\n",checks,failed);
    return failed?1:0;
}
