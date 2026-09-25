#define _GNU_SOURCE
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#define PAGES 8192u
#define PAGE 4096u
#define WORKERS 4
#define ROUNDS 2

static uint32_t next_random(uint32_t *s) {
    *s ^= *s << 13; *s ^= *s >> 17; *s ^= *s << 5;
    return *s;
}

static int worker(unsigned id) {
    unsigned order[PAGES];
    uint32_t seed = 0x12345678u ^ id;
    for (unsigned round = 0; round < ROUNDS; ++round) {
        unsigned char *p = mmap(NULL, PAGES * PAGE, PROT_READ | PROT_WRITE,
                               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (p == MAP_FAILED) return 10;
        for (unsigned i = 0; i < PAGES; ++i) {
            order[i] = i;
            p[i * PAGE] = (unsigned char)(i ^ id ^ round);
            p[(i + 1) * PAGE - 1] = (unsigned char)~(i ^ id ^ round);
        }
        for (unsigned i = PAGES - 1; i; --i) {
            unsigned j = next_random(&seed) % (i + 1);
            unsigned t = order[i]; order[i] = order[j]; order[j] = t;
        }
        for (unsigned j = 0; j < PAGES / 2; ++j)
            if (munmap(p + order[j] * PAGE, PAGE)) return 11;
        for (unsigned j = PAGES / 2; j < PAGES; ++j) {
            unsigned i = order[j];
            if (p[i * PAGE] != (unsigned char)(i ^ id ^ round) ||
                p[(i + 1) * PAGE - 1] != (unsigned char)~(i ^ id ^ round)) return 12;
        }
        for (unsigned j = 0; j < PAGES / 2; ++j) {
            unsigned i = order[j];
            void *q = mmap(p + i * PAGE, PAGE, PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
            if (q != p + i * PAGE) return 13;
            if (p[i * PAGE] || p[(i + 1) * PAGE - 1]) return 14;
            p[i * PAGE] = (unsigned char)(i ^ id ^ round);
            p[(i + 1) * PAGE - 1] = (unsigned char)~(i ^ id ^ round);
        }
        for (unsigned i = 0; i < PAGES; ++i)
            if (p[i * PAGE] != (unsigned char)(i ^ id ^ round) ||
                p[(i + 1) * PAGE - 1] != (unsigned char)~(i ^ id ^ round)) return 15;
        if (munmap(p, PAGES * PAGE)) return 16;
    }
    return 0;
}

int main(void) {
    pid_t children[WORKERS];
    for (unsigned i = 0; i < WORKERS; ++i) {
        children[i] = fork();
        if (children[i] < 0) { perror("fork"); return 1; }
        if (!children[i]) _exit(worker(i));
    }
    int failed = 0;
    for (unsigned i = 0; i < WORKERS; ++i) {
        int status;
        pid_t waited;
        do { waited = waitpid(children[i], &status, 0); } while (waited < 0 && errno == EINTR);
        if (waited != children[i] || !WIFEXITED(status) || WEXITSTATUS(status)) {
            fprintf(stderr, "worker %u failed status=%d\n", i, waited < 0 ? -1 : status);
            failed = 1;
        }
    }
    if (!failed) puts("BUDDY_PRESSURE_OK workers=4 rounds=2 bytes_per_worker=33554432");
    return failed;
}
