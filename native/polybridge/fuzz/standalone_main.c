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
    size_t n = (size_t)(rand() % MAX_INPUT) + 1;
    int biased = (rand() & 1);
    for (size_t i = 0; i < n; i++)
      buf[i] = biased ? alphabet[rand() % (int)sizeof(alphabet)] : (unsigned char)(rand() & 0xff);
    LLVMFuzzerTestOneInput(buf, n);
  }

  free(buf);
  printf("standalone driver: %ld iterations, seed %u, no crash\n", iters, seed);
  return 0;
}
