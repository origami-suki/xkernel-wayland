#include <stdio.h>

#ifndef BSS_BYTES
#define BSS_BYTES (80UL * 1024 * 1024)
#endif

/* A small ELF on disk whose writable PT_LOAD crosses the old interpreter hint. */
static volatile unsigned char payload[BSS_BYTES];

int main(void) {
    if (payload[0] || payload[BSS_BYTES - 1]) return 1;
    payload[0] = 0x31;
    payload[BSS_BYTES - 1] = 0x72;
    if (payload[0] != 0x31 || payload[BSS_BYTES - 1] != 0x72) return 2;
    printf("ELF_LAYOUT_OK bss_bytes=%lu first=%u last=%u\n",
           (unsigned long)BSS_BYTES, payload[0], payload[BSS_BYTES - 1]);
    return 0;
}
