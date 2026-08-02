/*
 * Fuzz harness for the server-side frame parser (process_peer).
 *
 * The input is replayed in pseudo-random slices through the same buffer
 * growth policy read_more uses, so partial-frame and re-entry paths are
 * exercised rather than just whole-buffer parses.
 *
 * Standard libFuzzer entry point; fuzz/standalone_main.c provides a driver for
 * platforms without the libFuzzer runtime (see the Makefile).
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>

#include "../polyproto.h"

static void fz_on_hello(void *ctx, int idx) {
  (void)ctx;
  (void)idx;
}

/* Read every payload byte so ASan sees any out-of-bounds handoff. */
static void fz_on_chunk(void *ctx, int idx, const unsigned char *payload, uint32_t plen) {
  (void)idx;
  unsigned long *sum = (unsigned long *)ctx;
  for (uint32_t i = 0; i < plen; i++) *sum += payload[i];
}

static const HubSink fz_sink = {fz_on_hello, fz_on_chunk};

/* Mirror read_more's growth and its INBUF_CAP ceiling. */
static int fz_append(Peer *p, const unsigned char *d, size_t n) {
  if (p->in_len + n > INBUF_CAP) return -1;
  if (p->in_len + n > p->in_cap) {
    size_t ncap = p->in_cap ? p->in_cap : 4096;
    while (ncap < p->in_len + n) ncap *= 2;
    if (ncap > INBUF_CAP) ncap = INBUF_CAP;
    unsigned char *nb = (unsigned char *)realloc(p->in, ncap);
    if (!nb) return -1;
    p->in = nb;
    p->in_cap = ncap;
  }
  memcpy(p->in + p->in_len, d, n);
  p->in_len += n;
  return 0;
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

  /* Bit 0 of the first byte pre-arms the peer, so the CHUNK path is reachable
   * without the input having to spell a valid HELLO first. */
  int prearmed = data[0] & 1;
  data++;
  size--;

  Peer p;
  memset(&p, 0, sizeof(p));
  p.fd = -1;
  p.alive = 1;
  if (prearmed) {
    p.ready = 1;
    memcpy(p.id, "fuzz", 5);
  }

  unsigned long sum = 0;
  size_t off = 0;
  while (off < size) {
    size_t slice = 1 + (size_t)(data[off] % 61);
    if (slice > size - off) slice = size - off;
    if (fz_append(&p, data + off, slice) < 0) break;
    off += slice;
    if (process_peer(&p, 0, &fz_sink, &sum) < 0) break;
  }

  free(p.in);
  fz_unmute();
  return 0;
}
