/*
 * polyproto — the polybridge wire codec, split out of the event loop so it can
 * be driven from a plain buffer (tests, fuzzing) with no sockets involved.
 *
 * Frame layout is documented in docs/STREAMING_BRIDGE.md and must stay in sync
 * with it. Header sizes live here so the builders and the parsers cannot drift.
 */

#ifndef POLYPROTO_H
#define POLYPROTO_H

#include <stddef.h>
#include <stdint.h>

#define MAX_CHUNK (1u << 20)
#define MAX_NAME 256
#define INBUF_CAP (MAX_CHUNK + 64)

#define T_HELLO 0x01u
#define T_CHUNK 0x02u
#define S_PEER_UP 0x81u
#define S_PEER_DOWN 0x82u
#define S_RELAY 0x83u

/* RELAY = 0x83 | u16be from_slot | u32be len | payload. Payload starts at +7. */
#define RELAY_HDR 7u

typedef struct {
  int fd;
  int alive;
  int ready; /* hello complete */
  uint16_t slot;
  char id[MAX_NAME];
  char label[MAX_NAME];
  char prov[MAX_NAME];
  unsigned char *in;
  size_t in_len;
  size_t in_cap;
  /* framed parse: waiting for more bytes */
  int phase; /* 0=need type, 1=hello body, 2=chunk len, 3=chunk body */
  unsigned char hdr[16];
  size_t hdr_got;
  uint32_t want_chunk;
  uint16_t hid, hlab, hpr;
} Peer;

/* process_peer does no I/O: completed frames are handed to the sink, so the hub
 * supplies the fan-out and tests supply a recorder. */
typedef struct {
  void (*on_hello)(void *ctx, int idx);
  void (*on_chunk)(void *ctx, int idx, const unsigned char *payload, uint32_t plen);
} HubSink;

int u16be(const unsigned char *b);
uint32_t u32be(const unsigned char *b);
void put_u16be(unsigned char *d, uint16_t v);
void put_u32be(unsigned char *d, uint32_t v);
void consume(Peer *p, size_t n);

int build_peer_up(unsigned char *out, size_t *olen, uint16_t slot, const Peer *info);
int build_peer_down(unsigned char *out, size_t *olen, uint16_t slot, const char *id);
int build_relay(unsigned char *out, size_t *olen, uint16_t from_slot, const unsigned char *payload,
                uint32_t plen);

/* Consume as many whole client->server frames as p->in holds. 0 = need more
 * bytes, -1 = protocol violation (caller closes the peer). */
int process_peer(Peer *p, int idx, const HubSink *sink, void *ctx);

typedef void (*relay_cb)(void *ctx, uint16_t from_slot, const unsigned char *payload, uint32_t plen);

/* Decode server->client frames out of buf; returns the number of bytes
 * consumed, leaving any trailing partial frame for the next call. */
size_t polyclient_parse(const unsigned char *buf, size_t len, relay_cb on_relay, void *ctx);

#endif /* POLYPROTO_H */
