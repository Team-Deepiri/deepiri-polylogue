/*
 * Unit tests for the polybridge wire codec (polyproto.c).
 *
 * Plain assert-style runner, no framework: the repo has no C test dependency
 * and should not gain one. Build and run with `make test`.
 */

#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "polyproto.h"

static int checks = 0;
static int failures = 0;

#define CHECK(cond, msg)                                                    \
  do {                                                                      \
    checks++;                                                               \
    if (!(cond)) {                                                          \
      failures++;                                                           \
      fprintf(stdout, "    FAIL %s:%d: %s\n", __FILE__, __LINE__, (msg));   \
    }                                                                       \
  } while (0)

/* The parser reports protocol violations on stderr; negative tests mute it. */
static int saved_stderr = -1;

static void quiet_begin(void) {
  fflush(stderr);
  saved_stderr = dup(STDERR_FILENO);
  int devnull = open("/dev/null", O_WRONLY);
  if (devnull >= 0) {
    dup2(devnull, STDERR_FILENO);
    close(devnull);
  }
}

static void quiet_end(void) {
  fflush(stderr);
  if (saved_stderr >= 0) {
    dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
    saved_stderr = -1;
  }
}

/* ---- recording sink -------------------------------------------------- */

#define MAX_REC 32

typedef struct {
  int hellos;
  int chunks;
  uint32_t plen[MAX_REC];
  unsigned char *pdata[MAX_REC];
} Rec;

static void rec_on_hello(void *ctx, int idx) {
  (void)idx;
  ((Rec *)ctx)->hellos++;
}

static void rec_on_chunk(void *ctx, int idx, const unsigned char *payload, uint32_t plen) {
  (void)idx;
  Rec *r = (Rec *)ctx;
  if (r->chunks < MAX_REC) {
    r->pdata[r->chunks] = (unsigned char *)malloc(plen ? plen : 1);
    memcpy(r->pdata[r->chunks], payload, plen);
    r->plen[r->chunks] = plen;
  }
  r->chunks++;
}

static const HubSink rec_sink = {rec_on_hello, rec_on_chunk};

static void rec_reset(Rec *r) {
  for (int i = 0; i < MAX_REC && i < r->chunks; i++) free(r->pdata[i]);
  memset(r, 0, sizeof(*r));
}

/* Initialise a fresh peer; callers free p->in when the test ends. */
static void peer_init(Peer *p) {
  memset(p, 0, sizeof(*p));
  p->fd = -1;
  p->alive = 1;
}

/* Append bytes the way read_more would, then run the parser. */
static int feed(Peer *p, const unsigned char *d, size_t n, Rec *r) {
  size_t need = p->in_len + n;
  if (need > p->in_cap) {
    size_t ncap = p->in_cap ? p->in_cap : 4096;
    while (ncap < need) ncap *= 2;
    if (ncap > INBUF_CAP) ncap = INBUF_CAP;
    unsigned char *nb = (unsigned char *)realloc(p->in, ncap);
    if (!nb) return -1;
    p->in = nb;
    p->in_cap = ncap;
  }
  memcpy(p->in + p->in_len, d, n);
  p->in_len += n;
  return process_peer(p, 0, &rec_sink, r);
}

/* ---- frame builders (test-side, written from docs/STREAMING_BRIDGE.md) -- */

static size_t mk_hello(unsigned char *out, const char *id, const char *label, const char *prov) {
  size_t il = strlen(id), ll = strlen(label), pl = strlen(prov);
  unsigned char *w = out;
  *w++ = (unsigned char)T_HELLO;
  put_u16be(w, (uint16_t)il);
  w += 2;
  put_u16be(w, (uint16_t)ll);
  w += 2;
  put_u16be(w, (uint16_t)pl);
  w += 2;
  memcpy(w, id, il);
  w += il;
  memcpy(w, label, ll);
  w += ll;
  memcpy(w, prov, pl);
  w += pl;
  return (size_t)(w - out);
}

static size_t mk_chunk(unsigned char *out, const unsigned char *payload, uint32_t plen) {
  unsigned char *w = out;
  *w++ = (unsigned char)T_CHUNK;
  put_u32be(w, plen);
  w += 4;
  memcpy(w, payload, plen);
  w += plen;
  return (size_t)(w - out);
}

/* ---- server-side parser: phase transitions ---------------------------- */

static void test_hello_phase_transitions(void) {
  Peer p;
  Rec r;
  memset(&p, 0, sizeof(p));
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char f[64];
  size_t n = mk_hello(f, "alpha", "Window A", "openai");

  CHECK(p.phase == 0, "starts in phase 0 (need type)");
  /* type byte only: parser arms the hello body and waits */
  CHECK(feed(&p, f, 1, &r) == 0, "type byte alone is not an error");
  CHECK(p.phase == 1, "phase 0 -> 1 after HELLO type byte");
  CHECK(p.ready == 0, "not ready before the body arrives");

  /* the 3 u16 lengths: parser moves to the string phase */
  CHECK(feed(&p, f + 1, 6, &r) == 0, "hello header alone is not an error");
  CHECK(p.phase == 11, "phase 1 -> 11 once the 6-byte header lands");
  CHECK(p.hid == 5 && p.hlab == 8 && p.hpr == 6, "header lengths decoded");
  CHECK(r.hellos == 0, "hello not announced before the strings arrive");

  /* the strings: hello completes */
  CHECK(feed(&p, f + 7, n - 7, &r) == 0, "hello body accepted");
  CHECK(p.phase == 0, "phase 11 -> 0 after hello completes");
  CHECK(p.ready == 1, "peer is ready after hello");
  CHECK(r.hellos == 1, "sink saw exactly one hello");
  CHECK(strcmp(p.id, "alpha") == 0, "id decoded");
  CHECK(strcmp(p.label, "Window A") == 0, "label decoded");
  CHECK(strcmp(p.prov, "openai") == 0, "prov decoded");
  CHECK(p.in_len == 0, "buffer fully consumed");

  rec_reset(&r);
  free(p.in);
}

static void test_chunk_phase_transitions(void) {
  Peer p;
  Rec r;
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char f[64];
  size_t n = mk_hello(f, "a", "A", "x");
  CHECK(feed(&p, f, n, &r) == 0, "hello accepted");

  n = mk_chunk(f, (const unsigned char *)"bravo", 5);
  CHECK(feed(&p, f, 1, &r) == 0, "chunk type byte alone is not an error");
  CHECK(p.phase == 2, "phase 0 -> 2 after CHUNK type byte");
  CHECK(feed(&p, f + 1, 4, &r) == 0, "chunk length alone is not an error");
  CHECK(p.phase == 3, "phase 2 -> 3 once the u32 length lands");
  CHECK(p.want_chunk == 5, "chunk length decoded");
  CHECK(r.chunks == 0, "payload not delivered before it arrives");
  CHECK(feed(&p, f + 5, n - 5, &r) == 0, "chunk payload accepted");
  CHECK(p.phase == 0, "phase 3 -> 0 after chunk completes");
  CHECK(r.chunks == 1, "sink saw exactly one chunk");
  CHECK(r.plen[0] == 5 && memcmp(r.pdata[0], "bravo", 5) == 0, "payload delivered intact");

  rec_reset(&r);
  free(p.in);
}

/* ---- server-side parser: split and pipelined frames -------------------- */

/* Feed a whole stream one byte at a time; every frame must still appear. */
static void test_byte_at_a_time(void) {
  Peer p;
  Rec r;
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char s[256];
  size_t n = mk_hello(s, "a", "A", "x");
  n += mk_chunk(s + n, (const unsigned char *)"alpha", 5);
  n += mk_chunk(s + n, (const unsigned char *)"bravo", 5);
  n += mk_chunk(s + n, (const unsigned char *)"charlie", 7);

  int ok = 1;
  for (size_t i = 0; i < n; i++)
    if (feed(&p, s + i, 1, &r) < 0) ok = 0;
  CHECK(ok, "byte-at-a-time feed never errors");
  CHECK(r.hellos == 1, "one hello from a byte-at-a-time stream");
  CHECK(r.chunks == 3, "three chunks from a byte-at-a-time stream");
  CHECK(r.plen[1] == 5 && memcmp(r.pdata[1], "bravo", 5) == 0, "second chunk intact");
  CHECK(r.plen[2] == 7 && memcmp(r.pdata[2], "charlie", 7) == 0, "third chunk intact");

  rec_reset(&r);
  free(p.in);
}

/* Split the same stream at every possible offset. */
static void test_split_at_every_offset(void) {
  unsigned char s[256];
  size_t n = mk_hello(s, "a", "A", "x");
  n += mk_chunk(s + n, (const unsigned char *)"alpha", 5);
  n += mk_chunk(s + n, (const unsigned char *)"bravo", 5);

  int bad_split = -1;
  for (size_t cut = 0; cut <= n; cut++) {
    Peer p;
    Rec r;
    memset(&r, 0, sizeof(r));
    peer_init(&p);
    int rc = feed(&p, s, cut, &r);
    if (rc == 0) rc = feed(&p, s + cut, n - cut, &r);
    if (rc < 0 || r.hellos != 1 || r.chunks != 2 || r.plen[0] != 5 ||
        memcmp(r.pdata[0], "alpha", 5) != 0 || r.plen[1] != 5 ||
        memcmp(r.pdata[1], "bravo", 5) != 0 || p.in_len != 0) {
      if (bad_split < 0) bad_split = (int)cut;
    }
    rec_reset(&r);
    free(p.in);
  }
  CHECK(bad_split < 0, "every 2-way split of the stream yields the same frames");
  if (bad_split >= 0) fprintf(stdout, "    first bad split offset: %d\n", bad_split);
}

static void test_pipelined_frames(void) {
  Peer p;
  Rec r;
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char s[256];
  size_t n = mk_hello(s, "a", "A", "x");
  n += mk_chunk(s + n, (const unsigned char *)"one", 3);
  n += mk_chunk(s + n, (const unsigned char *)"two", 3);
  n += mk_chunk(s + n, (const unsigned char *)"three", 5);

  CHECK(feed(&p, s, n, &r) == 0, "pipelined stream accepted in one write");
  CHECK(r.hellos == 1 && r.chunks == 3, "all pipelined frames parsed");
  CHECK(p.in_len == 0, "no bytes left over");

  rec_reset(&r);
  free(p.in);
}

static void test_partial_frame_is_retained(void) {
  Peer p;
  Rec r;
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char s[64];
  size_t n = mk_hello(s, "a", "A", "x");
  CHECK(feed(&p, s, n, &r) == 0, "hello accepted");

  n = mk_chunk(s, (const unsigned char *)"payload", 7);
  CHECK(feed(&p, s, n - 3, &r) == 0, "truncated chunk is not an error");
  CHECK(r.chunks == 0, "truncated chunk is not delivered");
  CHECK(p.in_len == 4, "the 4 payload bytes seen so far are retained");
  CHECK(feed(&p, s + n - 3, 3, &r) == 0, "remainder accepted");
  CHECK(r.chunks == 1, "chunk delivered once complete");
  CHECK(r.plen[0] == 7 && memcmp(r.pdata[0], "payload", 7) == 0, "reassembled payload intact");

  rec_reset(&r);
  free(p.in);
}

/* ---- server-side parser: boundaries and rejections --------------------- */

static void test_zero_length_chunk(void) {
  Peer p;
  Rec r;
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char s[64];
  size_t n = mk_hello(s, "a", "A", "x");
  n += mk_chunk(s + n, (const unsigned char *)"", 0);
  CHECK(feed(&p, s, n, &r) == 0, "zero-length chunk accepted");
  CHECK(r.chunks == 1 && r.plen[0] == 0, "zero-length chunk delivered");

  rec_reset(&r);
  free(p.in);
}

static void test_max_chunk_accepted(void) {
  Peer p;
  Rec r;
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char hello[64];
  size_t hn = mk_hello(hello, "a", "A", "x");
  CHECK(feed(&p, hello, hn, &r) == 0, "hello accepted");

  unsigned char *payload = (unsigned char *)malloc(MAX_CHUNK);
  memset(payload, 'Z', MAX_CHUNK);
  unsigned char *frame = (unsigned char *)malloc(5 + MAX_CHUNK);
  size_t fn = mk_chunk(frame, payload, MAX_CHUNK);
  CHECK(feed(&p, frame, fn, &r) == 0, "chunk of exactly MAX_CHUNK accepted");
  CHECK(r.chunks == 1 && r.plen[0] == MAX_CHUNK, "MAX_CHUNK payload delivered");

  free(payload);
  free(frame);
  rec_reset(&r);
  free(p.in);
}

static void test_oversize_chunk_rejected(void) {
  Peer p;
  Rec r;
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char s[64];
  size_t n = mk_hello(s, "a", "A", "x");
  CHECK(feed(&p, s, n, &r) == 0, "hello accepted");

  unsigned char hdr[5];
  hdr[0] = (unsigned char)T_CHUNK;
  put_u32be(hdr + 1, MAX_CHUNK + 1);
  quiet_begin();
  int rc = feed(&p, hdr, 5, &r);
  quiet_end();
  CHECK(rc < 0, "chunk length above MAX_CHUNK is rejected");
  CHECK(r.chunks == 0, "oversize chunk delivers nothing");

  rec_reset(&r);
  free(p.in);
}

static void test_bad_type_rejected(void) {
  Peer p;
  Rec r;
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char junk[1] = {0x7f};
  quiet_begin();
  int rc = feed(&p, junk, 1, &r);
  quiet_end();
  CHECK(rc < 0, "unknown frame type is rejected");

  rec_reset(&r);
  free(p.in);
}

static void test_chunk_before_hello_rejected(void) {
  Peer p;
  Rec r;
  memset(&r, 0, sizeof(r));
  peer_init(&p);

  unsigned char s[32];
  size_t n = mk_chunk(s, (const unsigned char *)"early", 5);
  quiet_begin();
  int rc = feed(&p, s, n, &r);
  quiet_end();
  CHECK(rc < 0, "CHUNK before HELLO is rejected");
  CHECK(r.chunks == 0, "no payload delivered before hello");

  rec_reset(&r);
  free(p.in);
}

static void test_hello_bad_lengths_rejected(void) {
  /* id_len == 0, and each length field at MAX_NAME, must all be refused. */
  const uint16_t bad[][3] = {{0, 1, 1}, {MAX_NAME, 1, 1}, {1, MAX_NAME, 1}, {1, 1, MAX_NAME}};
  for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
    Peer p;
    Rec r;
    memset(&r, 0, sizeof(r));
    peer_init(&p);
    unsigned char h[7];
    h[0] = (unsigned char)T_HELLO;
    put_u16be(h + 1, bad[i][0]);
    put_u16be(h + 3, bad[i][1]);
    put_u16be(h + 5, bad[i][2]);
    quiet_begin();
    int rc = feed(&p, h, 7, &r);
    quiet_end();
    CHECK(rc < 0, "out-of-range HELLO length is rejected");
    rec_reset(&r);
    free(p.in);
  }
}

/* ---- client-side parser ------------------------------------------------ */

typedef struct {
  int n;
  uint32_t plen[MAX_REC];
  unsigned char *pdata[MAX_REC];
} CRec;

static void crec_on_relay(void *ctx, uint16_t from_slot, const unsigned char *payload, uint32_t plen) {
  (void)from_slot;
  CRec *c = (CRec *)ctx;
  if (c->n < MAX_REC) {
    c->pdata[c->n] = (unsigned char *)malloc(plen ? plen : 1);
    memcpy(c->pdata[c->n], payload, plen);
    c->plen[c->n] = plen;
  }
  c->n++;
}

static void crec_reset(CRec *c) {
  for (int i = 0; i < MAX_REC && i < c->n; i++) free(c->pdata[i]);
  memset(c, 0, sizeof(*c));
}

/*
 * Regression, bug 1: polyclient sized the RELAY header at 11 bytes while
 * build_relay writes 7 (0x83 + u16be slot + u32be len). Assert the builder's
 * own output length so the two can never drift apart again.
 */
static void test_relay_header_is_seven_bytes(void) {
  unsigned char out[64];
  size_t flen = 0;
  CHECK(build_relay(out, &flen, 3, (const unsigned char *)"hello", 5) == 0, "build_relay succeeds");
  CHECK(flen == RELAY_HDR + 5, "RELAY frame is exactly RELAY_HDR + payload");
  CHECK(flen == 12, "RELAY of a 5-byte payload is 12 bytes, not 16");
  CHECK(out[0] == (unsigned char)S_RELAY, "type byte");
  CHECK(u16be(out + 1) == 3, "from_slot at +1");
  CHECK(u32be(out + 3) == 5, "payload length at +3");
  CHECK(memcmp(out + RELAY_HDR, "hello", 5) == 0, "payload at +7");
}

/*
 * Regression, bug 1a: with the 11-byte assumption the client advanced 4 bytes
 * too far after each RELAY and shredded the following frame. Three RELAYs
 * built by the server must decode as three payloads, consuming every byte.
 */
static void test_client_parses_consecutive_relays(void) {
  static const char *msgs[3] = {"alpha", "bravo", "charlie"};
  unsigned char buf[256];
  size_t total = 0;
  for (int i = 0; i < 3; i++) {
    size_t flen = 0;
    build_relay(buf + total, &flen, 1, (const unsigned char *)msgs[i], (uint32_t)strlen(msgs[i]));
    total += flen;
  }

  CRec c;
  memset(&c, 0, sizeof(c));
  size_t used = polyclient_parse(buf, total, crec_on_relay, &c);
  CHECK(c.n == 3, "all three RELAY frames decoded");
  CHECK(used == total, "every byte consumed, no desync");
  for (int i = 0; i < 3 && i < c.n; i++) {
    CHECK(c.plen[i] == strlen(msgs[i]), "payload length matches");
    CHECK(memcmp(c.pdata[i], msgs[i], c.plen[i]) == 0, "payload bytes match");
  }
  crec_reset(&c);
}

/*
 * Regression, bug 1b: a RELAY whose payload is shorter than 4 bytes is a
 * complete frame, but `rem < 11` withheld it until unrelated traffic padded
 * the buffer. A lone 9-byte frame must be delivered immediately.
 */
static void test_client_emits_short_payload_immediately(void) {
  unsigned char buf[32];
  size_t flen = 0;
  build_relay(buf, &flen, 0, (const unsigned char *)"hi", 2);
  CHECK(flen == 9, "RELAY carrying 2 bytes is 9 bytes on the wire");

  CRec c;
  memset(&c, 0, sizeof(c));
  size_t used = polyclient_parse(buf, flen, crec_on_relay, &c);
  CHECK(c.n == 1, "short-payload RELAY delivered with nothing following it");
  CHECK(used == flen, "short-payload RELAY fully consumed");
  CHECK(c.n == 1 && c.plen[0] == 2 && memcmp(c.pdata[0], "hi", 2) == 0, "payload intact");
  crec_reset(&c);
}

/*
 * Boundary: the protocol permits MAX_CHUNK payloads, so the largest legal
 * RELAY must decode. Note this exercises the parser only -- the client's fixed
 * 64 KiB receive buffer was a separate defect, guarded by test_e2e.c, because
 * that buffer lives in main() rather than here.
 */
static void test_client_parses_max_chunk_relay(void) {
  unsigned char *payload = (unsigned char *)malloc(MAX_CHUNK);
  memset(payload, 'Z', MAX_CHUNK);
  unsigned char *frame = (unsigned char *)malloc(RELAY_HDR + MAX_CHUNK);
  size_t flen = 0;
  CHECK(build_relay(frame, &flen, 2, payload, MAX_CHUNK) == 0, "build_relay handles MAX_CHUNK");
  CHECK(flen == RELAY_HDR + MAX_CHUNK, "largest legal frame is RELAY_HDR + MAX_CHUNK");

  CRec c;
  memset(&c, 0, sizeof(c));
  size_t used = polyclient_parse(frame, flen, crec_on_relay, &c);
  CHECK(c.n == 1 && c.plen[0] == MAX_CHUNK, "MAX_CHUNK RELAY decoded");
  CHECK(used == flen, "MAX_CHUNK RELAY fully consumed");
  crec_reset(&c);
  free(payload);
  free(frame);
}

static void test_client_partial_relay_consumes_nothing(void) {
  unsigned char buf[64];
  size_t flen = 0;
  build_relay(buf, &flen, 0, (const unsigned char *)"abcdefgh", 8);

  for (size_t cut = 1; cut < flen; cut++) {
    CRec c;
    memset(&c, 0, sizeof(c));
    size_t used = polyclient_parse(buf, cut, crec_on_relay, &c);
    CHECK(c.n == 0, "incomplete RELAY delivers nothing");
    CHECK(used == 0, "incomplete RELAY consumes nothing");
    crec_reset(&c);
  }
}

/* PEER_UP / PEER_DOWN must be skipped without disturbing following RELAYs. */
static void test_client_skips_peer_frames(void) {
  Peer info;
  memset(&info, 0, sizeof(info));
  strcpy(info.id, "alpha");
  strcpy(info.label, "Window A");
  strcpy(info.prov, "openai");

  unsigned char buf[512];
  size_t total = 0, flen = 0;
  build_peer_up(buf + total, &flen, 1, &info);
  total += flen;
  build_relay(buf + total, &flen, 1, (const unsigned char *)"mid", 3);
  total += flen;
  build_peer_down(buf + total, &flen, 1, "alpha");
  total += flen;
  build_relay(buf + total, &flen, 1, (const unsigned char *)"tail", 4);
  total += flen;

  CRec c;
  memset(&c, 0, sizeof(c));
  size_t used = polyclient_parse(buf, total, crec_on_relay, &c);
  CHECK(c.n == 2, "RELAYs interleaved with PEER_UP/PEER_DOWN are found");
  CHECK(used == total, "mixed frame stream fully consumed");
  CHECK(c.n == 2 && memcmp(c.pdata[1], "tail", 4) == 0, "RELAY after PEER_DOWN intact");
  crec_reset(&c);
}

/* Split a mixed server stream at every offset; the client must re-sync. */
static void test_client_split_at_every_offset(void) {
  Peer info;
  memset(&info, 0, sizeof(info));
  strcpy(info.id, "a");
  strcpy(info.label, "A");
  strcpy(info.prov, "x");

  unsigned char buf[512];
  size_t total = 0, flen = 0;
  build_peer_up(buf + total, &flen, 1, &info);
  total += flen;
  build_relay(buf + total, &flen, 1, (const unsigned char *)"alpha", 5);
  total += flen;
  build_relay(buf + total, &flen, 1, (const unsigned char *)"hi", 2);
  total += flen;
  build_relay(buf + total, &flen, 1, (const unsigned char *)"charlie", 7);
  total += flen;

  int bad_split = -1;
  for (size_t cut = 0; cut <= total; cut++) {
    /* Emulate the client's read loop: parse, then memmove the remainder. */
    unsigned char work[512];
    size_t have = cut;
    memcpy(work, buf, cut);
    CRec c;
    memset(&c, 0, sizeof(c));
    size_t used = polyclient_parse(work, have, crec_on_relay, &c);
    memmove(work, work + used, have - used);
    have -= used;
    memcpy(work + have, buf + cut, total - cut);
    have += total - cut;
    used = polyclient_parse(work, have, crec_on_relay, &c);
    if (c.n != 3 || used != have || c.plen[1] != 2 || memcmp(c.pdata[1], "hi", 2) != 0) {
      if (bad_split < 0) bad_split = (int)cut;
    }
    crec_reset(&c);
  }
  CHECK(bad_split < 0, "every 2-way split of a server stream yields the same RELAYs");
  if (bad_split >= 0) fprintf(stdout, "    first bad split offset: %d\n", bad_split);
}

/* ---- runner ------------------------------------------------------------ */

typedef struct {
  const char *name;
  void (*fn)(void);
} Test;

static const Test tests[] = {
    {"hello_phase_transitions", test_hello_phase_transitions},
    {"chunk_phase_transitions", test_chunk_phase_transitions},
    {"byte_at_a_time", test_byte_at_a_time},
    {"split_at_every_offset", test_split_at_every_offset},
    {"pipelined_frames", test_pipelined_frames},
    {"partial_frame_is_retained", test_partial_frame_is_retained},
    {"zero_length_chunk", test_zero_length_chunk},
    {"max_chunk_accepted", test_max_chunk_accepted},
    {"oversize_chunk_rejected", test_oversize_chunk_rejected},
    {"bad_type_rejected", test_bad_type_rejected},
    {"chunk_before_hello_rejected", test_chunk_before_hello_rejected},
    {"hello_bad_lengths_rejected", test_hello_bad_lengths_rejected},
    {"relay_header_is_seven_bytes", test_relay_header_is_seven_bytes},
    {"client_parses_consecutive_relays", test_client_parses_consecutive_relays},
    {"client_emits_short_payload_immediately", test_client_emits_short_payload_immediately},
    {"client_parses_max_chunk_relay", test_client_parses_max_chunk_relay},
    {"client_partial_relay_consumes_nothing", test_client_partial_relay_consumes_nothing},
    {"client_skips_peer_frames", test_client_skips_peer_frames},
    {"client_split_at_every_offset", test_client_split_at_every_offset},
};

int main(void) {
  size_t ntests = sizeof(tests) / sizeof(tests[0]);
  for (size_t i = 0; i < ntests; i++) {
    int before = failures;
    tests[i].fn();
    printf("%-4s %s\n", failures == before ? "ok" : "FAIL", tests[i].name);
  }
  printf("\n%d checks, %d failure(s) across %zu tests\n", checks, failures, ntests);
  return failures ? 1 : 0;
}
