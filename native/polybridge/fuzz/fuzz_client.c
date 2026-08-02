/*
 * Fuzz harness for the client-side frame parser (polyclient_parse).
 *
 * Runs the input twice: once as a single buffer, once through the incremental
 * read/memmove loop polyclient.c uses, which is where a mis-sized frame header
 * turns into a desync.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../polyproto.h"

/* Read every payload byte so ASan sees any out-of-bounds handoff. */
static void fz_on_relay(void *ctx, uint16_t from_slot, const unsigned char *payload, uint32_t plen) {
  (void)from_slot;
  unsigned long *sum = (unsigned long *)ctx;
  for (uint32_t i = 0; i < plen; i++) *sum += payload[i];
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  static int muted = 0;
  if (!muted) {
    if (!freopen("/dev/null", "w", stderr)) return 0;
    muted = 1;
  }
  if (size < 1) return 0;

  unsigned long sum = 0;
  size_t used = polyclient_parse(data, size, fz_on_relay, &sum);
  if (used > size) abort(); /* consumed past the end of the buffer */

  /* Now the incremental path: append a slice, parse, shift the remainder. */
  unsigned char *buf = (unsigned char *)malloc(size);
  if (!buf) return 0;
  size_t have = 0, off = 0;
  while (off < size) {
    size_t slice = 1 + (size_t)(data[off] % 53);
    if (slice > size - off) slice = size - off;
    memcpy(buf + have, data + off, slice);
    have += slice;
    off += slice;
    size_t u = polyclient_parse(buf, have, fz_on_relay, &sum);
    if (u > have) abort();
    memmove(buf, buf + u, have - u);
    have -= u;
  }

  free(buf);
  return 0;
}
