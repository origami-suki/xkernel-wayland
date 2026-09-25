/* Calibrate trace thread-time semantics independently of Chromium. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <time.h>

static int sample(clockid_t clock, int64_t *ns)
{
    struct timespec value;
    if (clock_gettime(clock, &value) != 0) {
        perror("clock_gettime");
        return -1;
    }
    *ns = (int64_t)value.tv_sec * 1000000000 + value.tv_nsec;
    return 0;
}

int main(void)
{
    int failed = 0;
    for (int i = 0; i < 3; ++i) {
        int64_t wall0, wall1, cpu0, cpu1;
        struct timespec request = {.tv_sec = 1, .tv_nsec = 0};
        if (sample(CLOCK_MONOTONIC, &wall0) ||
            sample(CLOCK_THREAD_CPUTIME_ID, &cpu0))
            return 2;
        while (nanosleep(&request, &request) != 0) {
            if (errno != EINTR) {
                perror("nanosleep");
                return 2;
            }
        }
        if (sample(CLOCK_THREAD_CPUTIME_ID, &cpu1) ||
            sample(CLOCK_MONOTONIC, &wall1))
            return 2;
        int64_t wall = wall1 - wall0, cpu = cpu1 - cpu0;
        /* A generous diagnostic bound: a sleeping thread should not charge
         * half its elapsed interval. This is not a POSIX precision bound. */
        int suspicious = wall <= 0 || cpu < 0 || cpu > wall / 2;
        printf("CLOCK_CALIBRATION sample=%d wall_ns=%" PRId64
               " thread_cpu_ns=%" PRId64 " suspicious=%d\n",
               i, wall, cpu, suspicious);
        failed |= suspicious;
    }
    return failed;
}
