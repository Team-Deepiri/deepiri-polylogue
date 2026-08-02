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
#include <fcntl.h>
#include <unistd.h>

#include "../polyproto.h"

/* Read every payload byte so ASan sees any out-of-bounds handoff. */
static void fz_on_relay(void *ctx, uint16_t from_slot, const unsigned char *payload, uint32_t plen) {
  (void)from_slot;
  unsigned long *sum = (unsigned long *)ctx;
  for (uint32_t i = 0; i < plen; i++) *sum += payload[i];
}


/* The parsers log protocol violations to stderr, and fuzzing is nothing but
 * protocol violations. Muting stderr outright -- as this harness used to do with
 * freopen("/dev/null") -- also silences libFuzzer, which reports its progress,
 * its statistics and its crash diagnostics on the same stream. A campaign then
 * looks like it produced nothing whether it found a bug or not. Mute only for
 * the duration of one parse and restore afterwards. */
static int fz_saved_stderr = -1;
static int fz_devnull = -1;

static void fz_mute(void) {
  if (fz_saved_stderr < 0) {
    fz_saved_stderr = dup(STDERR_FILENO);
    fz_devnull = open("/dev/null", O_WRONLY);
  }
  if (fz_devnull >= 0) {
    fflush(stderr);
    dup2(fz_devnull, STDERR_FILENO);
  }
}

static void fz_unmute(void) {
  if (fz_saved_stderr >= 0) {
    fflush(stderr);
    dup2(fz_saved_stderr, STDERR_FILENO);
  }
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  fz_mute();
  if (size < 1) {
    fz_unmute();
    return 0;
  }

  unsigned long sum = 0;
  size_t used = polyclient_parse(data, size, fz_on_relay, &sum);
  if (used > size) abort(); /* consumed past the end of the buffer */

  /* Now the incremental path: append a slice, parse, shift the remainder. */
  unsigned char *buf = (unsigned char *)malloc(size);
  if (!buf) {
    fz_unmute();
    return 0;
  }
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
  fz_unmute();
  return 0;
}
