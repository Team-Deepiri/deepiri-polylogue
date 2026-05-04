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
#include <unistd.h>

#define T_HELLO 0x01u
#define T_CHUNK 0x02u
#define S_PEER_UP 0x81u
#define S_PEER_DOWN 0x82u
#define S_RELAY 0x83u

static void put_u16be(unsigned char *d, uint16_t v) {
  d[0] = (unsigned char)((v >> 8) & 0xff);
  d[1] = (unsigned char)(v & 0xff);
}

static void put_u32be(unsigned char *d, uint32_t v) {
  d[0] = (unsigned char)((v >> 24) & 0xff);
  d[1] = (unsigned char)((v >> 16) & 0xff);
  d[2] = (unsigned char)((v >> 8) & 0xff);
  d[3] = (unsigned char)(v & 0xff);
}

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

static uint16_t rd_u16(const unsigned char *b) {
  return (uint16_t)((b[0] << 8) | b[1]);
}

static uint32_t rd_u32(const unsigned char *b) {
  return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | (uint32_t)b[3];
}

static void usage(void) {
  fprintf(stderr,
          "usage: polyclient [--send-only] HOST PORT ID LABEL PROVIDER [MESSAGE]\n"
          "  Sends HELLO, optional UTF-8 MESSAGE as one CHUNK.\n"
          "  Default: print RELAY payloads until EOF.\n"
          "  --send-only: exit after sending (for scripts / token emitters).\n");
}

static size_t frame_len_peer_up(const unsigned char *p, size_t rem) {
  if (rem < 3) return 0;
  size_t o = 1u + 2u;
  if (rem < o + 2) return 0;
  uint16_t idl = rd_u16(p + o);
  o += 2u;
  if (rem < o + idl + 2) return 0;
  o += idl;
  uint16_t lb = rd_u16(p + o);
  o += 2u;
  if (rem < o + lb + 2) return 0;
  o += lb;
  uint16_t pr = rd_u16(p + o);
  o += 2u;
  if (rem < o + pr) return 0;
  o += pr;
  return o;
}

static size_t frame_len_peer_down(const unsigned char *p, size_t rem) {
  if (rem < 5) return 0;
  uint16_t idl = rd_u16(p + 3);
  if (rem < 5u + idl) return 0;
  return 5u + idl;
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
    close(fd);
    return 0;
  }

  /* Parse server frames: PEER_UP / PEER_DOWN / RELAY */
  unsigned char rb[65536];
  size_t have = 0;
  for (;;) {
    ssize_t r = read(fd, rb + have, sizeof(rb) - have - 1);
    if (r < 0) {
      if (errno == EINTR) continue;
      perror("read");
      break;
    }
    if (r == 0) break;
    have += (size_t)r;
    size_t off = 0;
    while (off < have) {
      unsigned char *p = rb + off;
      size_t rem = have - off;
      if (rem < 1) break;
      unsigned char t = p[0];
      if (t == S_PEER_UP) {
        size_t fl = frame_len_peer_up(p, rem);
        if (fl == 0) break;
        off += fl;
        continue;
      }
      if (t == S_PEER_DOWN) {
        size_t fl = frame_len_peer_down(p, rem);
        if (fl == 0) break;
        off += fl;
        continue;
      }
      if (t == S_RELAY) {
        if (rem < 11) break;
        uint32_t plen = rd_u32(p + 3);
        if (rem < 11u + plen) break;
        fwrite(p + 7, 1, plen, stdout);
        fputc('\n', stdout);
        fflush(stdout);
        off += 11u + plen;
        continue;
      }
      fprintf(stderr, "[polyclient] unknown byte 0x%02x, skipping\n", t);
      off++;
    }
    if (off > 0) {
      memmove(rb, rb + off, have - off);
      have -= off;
    }
  }
  close(fd);
  return 0;
}
