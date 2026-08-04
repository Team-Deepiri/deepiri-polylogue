/* Minimal polybridge client: HELLO + optional CHUNK(s), then copy RELAY to stdout. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include "polyproto.h"

static ssize_t write_all(int fd, const void *buf, size_t len) {
  const unsigned char *p = (const unsigned char *)buf;
  size_t off = 0;
  while (off < len) {
    ssize_t w = write(fd, p + off, len - off);
    if (w < 0) {
      if (errno == EINTR) continue;
      return -1;
    }
    if (w == 0) return -1;
    off += (size_t)w;
  }
  return (ssize_t)len;
}

static int send_hello(int fd, const char *id, const char *label, const char *prov) {
  size_t il = strlen(id), ll = strlen(label), pl = strlen(prov);
  if (il > 65535 || ll > 65535 || pl > 65535) return -1;
  size_t tot = 1 + 2 + 2 + 2 + il + ll + pl;
  unsigned char *b = (unsigned char *)malloc(tot);
  if (!b) return -1;
  unsigned char *w = b;
  *w++ = T_HELLO;
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
  int rc = write_all(fd, b, tot) < 0 ? -1 : 0;
  free(b);
  return rc;
}

static int send_chunk(int fd, const void *data, size_t len) {
  if (len > 0xffffffffu) return -1;
  size_t tot = 1 + 4 + len;
  unsigned char *b = (unsigned char *)malloc(tot);
  if (!b) return -1;
  unsigned char *w = b;
  *w++ = T_CHUNK;
  put_u32be(w, (uint32_t)len);
  w += 4;
  memcpy(w, data, len);
  w += len;
  int rc = write_all(fd, b, tot) < 0 ? -1 : 0;
  free(b);
  return rc;
}

static void usage(void) {
  fprintf(stderr,
          "usage: polyclient [--send-only] HOST PORT ID LABEL PROVIDER [MESSAGE]\n"
          "  Sends HELLO, optional UTF-8 MESSAGE as one CHUNK.\n"
          "  Default: print RELAY payloads until EOF.\n"
          "  --send-only: exit after sending (for scripts / token emitters).\n");
}

static void print_relay(void *ctx, uint16_t from_slot, const unsigned char *payload, uint32_t plen) {
  (void)ctx;
  (void)from_slot;
  fwrite(payload, 1, plen, stdout);
  fputc('\n', stdout);
  fflush(stdout);
}

int main(int argc, char **argv) {
  int send_only = 0;
  int base = 1;
  if (argc >= 2 && strcmp(argv[1], "--send-only") == 0) {
    send_only = 1;
    base = 2;
  }
  if (argc < base + 5) {
    usage();
    return 1;
  }
  const char *host = argv[base];
  int port = atoi(argv[base + 1]);
  const char *id = argv[base + 2], *label = argv[base + 3], *prov = argv[base + 4];
  const char *msg = argc >= base + 6 ? argv[base + 5] : NULL;

  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) {
    perror("socket");
    return 1;
  }
  struct sockaddr_in a;
  memset(&a, 0, sizeof(a));
  a.sin_family = AF_INET;
  a.sin_port = htons((uint16_t)port);
  if (inet_pton(AF_INET, host, &a.sin_addr) != 1) {
    fprintf(stderr, "bad host\n");
    return 1;
  }
  if (connect(fd, (struct sockaddr *)&a, sizeof(a)) < 0) {
    perror("connect");
    return 1;
  }
  int one = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

  if (send_hello(fd, id, label, prov) < 0) return 1;
  if (msg && send_chunk(fd, msg, strlen(msg)) < 0) return 1;
  if (send_only) {
    /* Half-close, then drain, then close. Closing a socket that still has unread
     * data in its receive queue makes the kernel send RST instead of FIN, and the
     * hub announces PEER_UP to every peer that joins -- so --send-only always has
     * unread data. On Linux the hub then sees POLLERR and discards the frames we
     * just sent, which is precisely the loss this mode is supposed to be safe
     * from. SHUT_WR delivers a clean FIN; the drain empties the queue so the
     * close is orderly. Bounded by a receive timeout so a wedged hub cannot hang
     * a script. */
    shutdown(fd, SHUT_WR);
    struct timeval tv;
    tv.tv_sec = 2;
    tv.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    char sink[4096];
    while (read(fd, sink, sizeof(sink)) > 0) {
    }
    close(fd);
    return 0;
  }

  /* Parse server frames: PEER_UP / PEER_DOWN / RELAY. The buffer has to be able
   * to hold the largest legal frame (a RELAY carrying MAX_CHUNK), so it grows on
   * demand instead of capping at a size the protocol is allowed to exceed. */
  size_t cap = 65536;
  size_t have = 0;
  unsigned char *rb = (unsigned char *)malloc(cap);
  if (!rb) {
    perror("malloc");
    return 1;
  }
  for (;;) {
    if (have == cap) {
      if (cap >= RELAY_HDR + MAX_CHUNK) {
        fprintf(stderr, "[polyclient] frame exceeds %u-byte protocol limit\n", MAX_CHUNK);
        break;
      }
      size_t ncap = cap * 2;
      if (ncap > RELAY_HDR + MAX_CHUNK) ncap = RELAY_HDR + MAX_CHUNK;
      unsigned char *n = (unsigned char *)realloc(rb, ncap);
      if (!n) {
        perror("realloc");
        break;
      }
      rb = n;
      cap = ncap;
    }
    ssize_t r = read(fd, rb + have, cap - have);
    if (r < 0) {
      if (errno == EINTR) continue;
      perror("read");
      break;
    }
    if (r == 0) break;
    have += (size_t)r;
    size_t off = polyclient_parse(rb, have, print_relay, NULL);
    if (off > 0) {
      memmove(rb, rb + off, have - off);
      have -= off;
    }
  }
  free(rb);
  close(fd);
  return 0;
}
