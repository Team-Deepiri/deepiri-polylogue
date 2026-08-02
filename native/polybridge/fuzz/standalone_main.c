/*
 * Driver so the libFuzzer harnesses still run where the libFuzzer runtime is
 * absent (Apple Command Line Tools ship asan/ubsan but no fuzzer runtime).
 *
 * This is blind random generation, not coverage-guided: it is a smoke test
 * under ASan/UBSan, materially weaker than `make fuzz` on Linux. Bytes are
 * drawn from a protocol-biased alphabet so inputs actually form frames
 * sometimes; uniform random bytes almost never reach the parser's deep states.
 *
 *   ./fuzz_x [iterations] [seed]   generate and run inputs
 *   ./fuzz_x file [file ...]       replay inputs (e.g. libFuzzer crash artifacts)
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

/* Type bytes, small lengths, and boundary values the parser branches on. */
static const unsigned char alphabet[] = {0x01, 0x02, 0x81, 0x82, 0x83, 0x00, 0x01, 0x02,
                                         0x03, 0x04, 0x05, 0x07, 0x08, 0x0b, 0x10, 0xff};

#define MAX_INPUT 8192

/*
 * A protocol-biased alphabet still almost never produces a *well-formed* frame:
 * RELAY alone needs 0x83, a big-endian u16 slot and a big-endian u32 length that
 * agrees with the bytes that follow. Blind bytes therefore stall in the header
 * states and never reach the payload handoff -- so an injected overread there
 * survived this driver at exit 0 while `make fuzz` caught it in seconds.
 *
 * Build valid frames deliberately, then corrupt a few bytes. Mutating a correct
 * frame lands on the boundaries that matter (length off by one, truncated
 * payload, type swapped mid-stream) instead of never arriving at them.
 */
static size_t put_frame(unsigned char *out, size_t cap, int kind) {
  size_t n = 0;
  uint32_t plen = (uint32_t)(rand() % 40);
  if (kind == 0) { /* HELLO: 0x01 | u8 id_len | id | u8 label_len | label */
    if (cap < 8) return 0;
    out[n++] = 0x01;
    out[n++] = 4;
    memcpy(out + n, "fuzz", 4);
    n += 4;
    out[n++] = 1;
    out[n++] = 'L';
    return n;
  }
  if (kind == 1) { /* CHUNK: 0x02 | u32be len | payload */
    if (cap < 5 + plen) return 0;
    out[n++] = 0x02;
    out[n++] = (unsigned char)(plen >> 24);
    out[n++] = (unsigned char)(plen >> 16);
    out[n++] = (unsigned char)(plen >> 8);
    out[n++] = (unsigned char)plen;
    for (uint32_t i = 0; i < plen; i++) out[n++] = (unsigned char)(rand() & 0xff);
    return n;
  }
  /* RELAY: 0x83 | u16be from_slot | u32be len | payload */
  if (cap < 7 + plen) return 0;
  out[n++] = 0x83;
  out[n++] = (unsigned char)(rand() & 0xff);
  out[n++] = (unsigned char)(rand() & 0xff);
  out[n++] = (unsigned char)(plen >> 24);
  out[n++] = (unsigned char)(plen >> 16);
  out[n++] = (unsigned char)(plen >> 8);
  out[n++] = (unsigned char)plen;
  for (uint32_t i = 0; i < plen; i++) out[n++] = (unsigned char)(rand() & 0xff);
  return n;
}

static size_t gen_structured(unsigned char *buf, size_t cap) {
  size_t n = 0;
  int frames = 1 + rand() % 6;
  for (int f = 0; f < frames && n < cap; f++) {
    size_t w = put_frame(buf + n, cap - n, rand() % 3);
    if (!w) break;
    n += w;
  }
  /* Corrupt a little, so well-formed streams and near-misses both get exercised. */
  int flips = rand() % 4;
  for (int i = 0; i < flips && n; i++) buf[rand() % (int)n] = (unsigned char)(rand() & 0xff);
  return n;
}

static int replay_file(const char *path) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    perror(path);
    return 1;
  }
  static unsigned char buf[1 << 20];
  size_t n = fread(buf, 1, sizeof(buf), f);
  fclose(f);
  LLVMFuzzerTestOneInput(buf, n);
  printf("replayed %s (%zu bytes), no crash\n", path, n);
  return 0;
}

int main(int argc, char **argv) {
  if (argc > 1 && argv[1][0] != '-') {
    /* If the first argument is not a number, treat all arguments as files. */
    char *end = NULL;
    strtol(argv[1], &end, 10);
    if (end && *end != '\0') {
      int rc = 0;
      for (int i = 1; i < argc; i++) rc |= replay_file(argv[i]);
      return rc;
    }
  }

  long iters = argc > 1 ? atol(argv[1]) : 10000;
  unsigned seed = argc > 2 ? (unsigned)strtoul(argv[2], NULL, 10) : 1u;
  srand(seed);

  unsigned char *buf = (unsigned char *)malloc(MAX_INPUT);
  if (!buf) return 1;

  for (long it = 0; it < iters; it++) {
    size_t n;
    int mode = rand() % 3;
    if (mode == 2) {
      n = gen_structured(buf, MAX_INPUT);
      if (!n) n = 1;
    } else {
      n = (size_t)(rand() % MAX_INPUT) + 1;
      int biased = (mode == 1);
      for (size_t i = 0; i < n; i++)
        buf[i] = biased ? alphabet[rand() % (int)sizeof(alphabet)] : (unsigned char)(rand() & 0xff);
    }
    LLVMFuzzerTestOneInput(buf, n);
  }

  free(buf);
  printf("standalone driver: %ld iterations, seed %u, no crash\n", iters, seed);
  return 0;
}
