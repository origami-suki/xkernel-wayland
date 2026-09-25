#define _GNU_SOURCE
#include <errno.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

/* Exercise the real libgcc cache synchronization entry point, plus each
 * architectural operation separately so a CTR trap cannot hide a UCI trap. */
extern void __clear_cache(void *, void *);

static int failures;
static int checks;

static void check(int ok, const char *name, int cpu, int detail)
{
    ++checks;
    if (!ok) ++failures;
    printf("%s cpu=%d %s detail=%d\n", ok ? "PASS" : "FAIL", cpu, name, detail);
}

static int operation(int kind, int cpu)
{
    if (sched_getcpu() != cpu) return 19;
    uint32_t *code = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    uint64_t value;
    if (code == MAP_FAILED) return 20;
    code[0] = 0xd28000e0; /* mov x0, #7 */
    code[1] = 0xd65f03c0; /* ret */
    switch (kind) {
    case 0:
        __asm__ volatile("mrs %0, ctr_el0" : "=r"(value));
        if (!(value & (UINT64_C(1) << 31))) return 21;
        break;
    case 1: __asm__ volatile("dc cvau, %0\n dsb ish" :: "r"(code) : "memory"); break;
    case 2: __asm__ volatile("ic ivau, %0\n dsb ish\n isb" :: "r"(code) : "memory"); break;
    case 3: __asm__ volatile("dc cvac, %0\n dsb ish" :: "r"(code) : "memory"); break;
    case 4: __asm__ volatile("dc civac, %0\n dsb ish" :: "r"(code) : "memory"); break;
    case 5:
        for (unsigned i = 0; i < 16; ++i) {
            unsigned result = i & 1 ? 42 : 7;
            code[0] = 0xd2800000 | (result << 5);
            __clear_cache(code, code + 2);
            if (mprotect(code, 4096, PROT_READ | PROT_EXEC)) return 22;
            if (((unsigned (*)(void))code)() != result) return 23;
            if (mprotect(code, 4096, PROT_READ | PROT_WRITE)) return 24;
        }
        break;
    case 6: __asm__ volatile("mrs %0, sctlr_el1" : "=r"(value)); break;
    case 7: __asm__ volatile("dc cisw, xzr" ::: "memory"); break;
    case 8: __asm__ volatile(".inst 0x00000000"); break;
    default: return 25;
    }
    return munmap(code, 4096) ? 26 : 0;
}

int main(void)
{
    const char *names[] = {"ctr-el0", "dc-cvau", "ic-ivau", "dc-cvac", "dc-civac",
                           "clear-cache-rewrite-execute", "sctlr-el1-denied",
                           "dc-cisw-denied", "undefined-instruction-denied"};
    cpu_set_t initial;
    struct rlimit limit = {0, 0};
    setvbuf(stdout, NULL, _IONBF, 0);
    if (setrlimit(RLIMIT_CORE, &limit) || sched_getaffinity(0, sizeof(initial), &initial)) {
        perror("probe setup");
        return 2;
    }
    for (int cpu = 0; cpu < 4; ++cpu) {
        cpu_set_t selected, actual;
        CPU_ZERO(&selected);
        CPU_SET(cpu, &selected);
        int rc = sched_setaffinity(0, sizeof(selected), &selected);
        if (!rc) rc = sched_getaffinity(0, sizeof(actual), &actual);
        check(rc == 0 && CPU_EQUAL(&selected, &actual), "affinity", cpu, rc ? errno : 0);
        if (rc) continue;
        for (int kind = 0; kind < 9; ++kind) {
            pid_t child = fork();
            if (!child) _exit(operation(kind, cpu));
            int status = 0;
            pid_t got;
            do { got = child < 0 ? -1 : waitpid(child, &status, 0); }
            while (got < 0 && errno == EINTR);
            int ok = got == child && child > 0;
            ok = ok && (kind < 6 ? WIFEXITED(status) && WEXITSTATUS(status) == 0
                                 : WIFSIGNALED(status) && WTERMSIG(status) == SIGILL);
            check(ok, names[kind], cpu, status);
        }
    }
    int rc = sched_setaffinity(0, sizeof(initial), &initial);
    check(rc == 0, "restore-affinity", -1, rc ? errno : 0);
    printf("CACHE_SYNC_RESULT checks=%d failures=%d\n", checks, failures);
    return failures != 0;
}
