/*
 * polybridge — Deepiri Polylogue native streaming fan-out hub (C99, POSIX).
 *
 * What this does: multiplex opaque framed chunks among N TCP peers in real time.
 * What this does NOT do: read vendor chat UIs. Each LLM surface needs a thin
 * adapter that opens localhost:PORT and speaks the wire protocol below.
 *
 * Wire (client -> server):
 *   HELLO 0x01 | u16be id_len | u16be label_len | u16be prov_len | id | label | prov
 *   CHUNK 0x02 | u32be payload_len | payload
 *
 * Wire (server -> client):
 *   PEER_UP   0x81 | u16be slot | u16be id_len | id | u16be label_len | label | u16be prov_len | prov
 *   PEER_DOWN 0x82 | u16be slot | u16be id_len | id
 *   RELAY     0x83 | u16be from_slot | u32be len | payload   (payload = peer's CHUNK bytes)
 */

#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

#define MAX_PEERS 64
#define MAX_CHUNK (1u << 20)
#define MAX_NAME 256
#define INBUF_CAP (MAX_CHUNK + 64)

#define T_HELLO 0x01u
#define T_CHUNK 0x02u
#define S_PEER_UP 0x81u
#define S_PEER_DOWN 0x82u
#define S_RELAY 0x83u

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

static void die(const char *msg) {
  perror(msg);
  exit(1);
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

static int read_more(Peer *p) {
  if (p->in_len >= p->in_cap) {
    if (p->in_cap >= INBUF_CAP) return -1;
    size_t ncap = p->in_cap ? p->in_cap * 2 : 4096;
    if (ncap > INBUF_CAP) ncap = INBUF_CAP;
    unsigned char *n = realloc(p->in, ncap);
    if (!n) return -1;
    p->in = n;
    p->in_cap = ncap;
  }
  ssize_t r = read(p->fd, p->in + p->in_len, p->in_cap - p->in_len);
  if (r < 0) {
    if (errno == EINTR) return 0;
    return -1;
  }
  if (r == 0) return -1;
  p->in_len += (size_t)r;
  return 1;
}

static void consume(Peer *p, size_t n) {
  if (n >= p->in_len) {
    p->in_len = 0;
    return;
  }
  memmove(p->in, p->in + n, p->in_len - n);
  p->in_len -= n;
}

static int u16be(const unsigned char *b) {
  return (int)((b[0] << 8) | b[1]);
}

static uint32_t u32be(const unsigned char *b) {
  return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | (uint32_t)b[3];
}

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

static int send_frame(int fd, const unsigned char *buf, size_t len) {
  return write_all(fd, buf, len) < 0 ? -1 : 0;
}

static int build_peer_up(unsigned char *out, size_t *olen, uint16_t slot, const Peer *info) {
  size_t idl = strlen(info->id);
  size_t lb = strlen(info->label);
  size_t pr = strlen(info->prov);
  if (idl > 65535 || lb > 65535 || pr > 65535) return -1;
  size_t need = 1 + 2 + 2 + idl + 2 + lb + 2 + pr;
  if (need > MAX_CHUNK) return -1;
  unsigned char *w = out;
  *w++ = S_PEER_UP;
  put_u16be(w, slot);
  w += 2;
  put_u16be(w, (uint16_t)idl);
  w += 2;
  memcpy(w, info->id, idl);
  w += idl;
  put_u16be(w, (uint16_t)lb);
  w += 2;
  memcpy(w, info->label, lb);
  w += lb;
  put_u16be(w, (uint16_t)pr);
  w += 2;
  memcpy(w, info->prov, pr);
  w += pr;
  *olen = (size_t)(w - out);
  return 0;
}

static int build_peer_down(unsigned char *out, size_t *olen, uint16_t slot, const char *id) {
  size_t idl = strlen(id);
  if (idl > 65535) return -1;
  unsigned char *w = out;
  *w++ = S_PEER_DOWN;
  put_u16be(w, slot);
  w += 2;
  put_u16be(w, (uint16_t)idl);
  w += 2;
  memcpy(w, id, idl);
  w += idl;
  *olen = (size_t)(w - out);
  return 0;
}

static int build_relay(unsigned char *out, size_t *olen, uint16_t from_slot, const unsigned char *payload,
                       uint32_t plen) {
  if (plen > MAX_CHUNK) return -1;
  size_t need = 1 + 2 + 4 + plen;
  if (need > MAX_CHUNK + 16) return -1;
  unsigned char *w = out;
  *w++ = S_RELAY;
  put_u16be(w, from_slot);
  w += 2;
  put_u32be(w, plen);
  w += 4;
  memcpy(w, payload, plen);
  w += plen;
  *olen = (size_t)(w - out);
  return 0;
}

static void close_peer(Peer *peers, int n, int idx);

/* If id already connected, kick the older socket (session steal). */
static void dedupe_id(Peer *peers, int n, int new_idx) {
  Peer *nup = &peers[new_idx];
  for (int i = 0; i < n; i++) {
    if (i == new_idx || !peers[i].alive || !peers[i].ready) continue;
    if (strcmp(peers[i].id, nup->id) == 0) {
      fprintf(stderr, "[polybridge] id '%s' reconnected; closing old fd %d\n", nup->id, peers[i].fd);
      close_peer(peers, n, i);
      return;
    }
  }
}

static void announce_peer_up(Peer *peers, int n, int new_idx) {
  unsigned char buf[MAX_NAME * 3 + 32];
  size_t flen;
  Peer *nu = &peers[new_idx];
  /* Tell newcomer about everyone else already ready */
  for (int i = 0; i < n; i++) {
    if (!peers[i].alive || !peers[i].ready || i == new_idx) continue;
    if (build_peer_up(buf, &flen, peers[i].slot, &peers[i]) != 0) continue;
    if (send_frame(nu->fd, buf, flen) < 0) return;
  }
  /* Tell everyone else about newcomer */
  if (build_peer_up(buf, &flen, nu->slot, nu) != 0) return;
  for (int i = 0; i < n; i++) {
    if (!peers[i].alive || !peers[i].ready || i == new_idx) continue;
    if (send_frame(peers[i].fd, buf, flen) < 0) { /* ignore */ }
  }
}

static void announce_peer_down(Peer *peers, int n, int gone_idx) {
  Peer *g = &peers[gone_idx];
  if (!g->ready) return;
  unsigned char buf[512];
  size_t flen;
  if (build_peer_down(buf, &flen, g->slot, g->id) != 0) return;
  for (int i = 0; i < n; i++) {
    if (!peers[i].alive || !peers[i].ready || i == gone_idx) continue;
    if (send_frame(peers[i].fd, buf, flen) < 0) { /* ignore */ }
  }
}

static void relay_chunk(Peer *peers, int n, int from_idx, const unsigned char *payload, uint32_t plen) {
  size_t flen;
  size_t need = 11u + (size_t)plen;
  unsigned char *buf = (unsigned char *)malloc(need);
  if (!buf) return;
  if (build_relay(buf, &flen, peers[from_idx].slot, payload, plen) != 0) {
    free(buf);
    return;
  }
  for (int i = 0; i < n; i++) {
    if (!peers[i].alive || !peers[i].ready || i == from_idx) continue;
    if (send_frame(peers[i].fd, buf, flen) < 0) { /* ignore */ }
  }
  free(buf);
}

static void close_peer(Peer *peers, int n, int idx) {
  Peer *p = &peers[idx];
  if (!p->alive) return;
  announce_peer_down(peers, n, idx);
  if (p->fd >= 0) close(p->fd);
  p->fd = -1;
  p->alive = 0;
  p->ready = 0;
  free(p->in);
  p->in = NULL;
  p->in_len = p->in_cap = 0;
  p->phase = 0;
  p->hdr_got = 0;
}

static int process_peer(Peer *peers, int n, int idx) {
  Peer *p = &peers[idx];
  while (1) {
    if (p->phase == 0) {
      if (p->in_len < 1) return 0;
      unsigned char t = p->in[0];
      if (t != T_HELLO && t != T_CHUNK) {
        fprintf(stderr, "[polybridge] bad type 0x%02x from slot %d\n", t, idx);
        return -1;
      }
      if (t == T_HELLO) {
        p->phase = 1;
        p->hdr_got = 0;
      } else {
        if (!p->ready) return -1;
        p->phase = 2;
        p->hdr_got = 0;
      }
      consume(p, 1);
      continue;
    }
    if (p->phase == 1) {
      /* hello: 3xu16 + strings */
      if (p->hdr_got < 6) {
        size_t need = 6 - p->hdr_got;
        if (p->in_len < need) return 0;
        memcpy(p->hdr + p->hdr_got, p->in, need);
        p->hdr_got += need;
        consume(p, need);
      }
      if (p->hdr_got == 6) {
        p->hid = (uint16_t)u16be(p->hdr);
        p->hlab = (uint16_t)u16be(p->hdr + 2);
        p->hpr = (uint16_t)u16be(p->hdr + 4);
        if (p->hid == 0 || p->hid >= MAX_NAME || p->hlab >= MAX_NAME || p->hpr >= MAX_NAME) return -1;
        p->want_chunk = (uint32_t)(p->hid + p->hlab + p->hpr);
        p->phase = 11;
        p->hdr_got = 0;
      }
      continue;
    }
    if (p->phase == 11) {
      if (p->in_len < p->want_chunk) return 0;
      memcpy(p->id, p->in, p->hid);
      p->id[p->hid] = 0;
      memcpy(p->label, p->in + p->hid, p->hlab);
      p->label[p->hlab] = 0;
      memcpy(p->prov, p->in + p->hid + p->hlab, p->hpr);
      p->prov[p->hpr] = 0;
      consume(p, p->want_chunk);
      p->slot = (uint16_t)idx;
      p->ready = 1;
      p->phase = 0;
      fprintf(stderr, "[polybridge] HELLO slot=%d id=%s label=%s prov=%s\n", idx, p->id, p->label, p->prov);
      dedupe_id(peers, n, idx);
      announce_peer_up(peers, n, idx);
      continue;
    }
    if (p->phase == 2) {
      if (p->hdr_got < 4) {
        size_t need = 4 - p->hdr_got;
        if (p->in_len < need) return 0;
        memcpy(p->hdr + p->hdr_got, p->in, need);
        p->hdr_got += need;
        consume(p, need);
      }
      if (p->hdr_got == 4) {
        p->want_chunk = u32be(p->hdr);
        if (p->want_chunk > MAX_CHUNK) return -1;
        p->phase = 3;
        p->hdr_got = 0;
      }
      continue;
    }
    if (p->phase == 3) {
      if (p->in_len < p->want_chunk) return 0;
      relay_chunk(peers, n, idx, p->in, p->want_chunk);
      consume(p, p->want_chunk);
      p->phase = 0;
      continue;
    }
    return -1;
  }
}

static int find_free(Peer *peers, int n) {
  for (int i = 0; i < n; i++)
    if (!peers[i].alive) return i;
  return -1;
}

static void usage(void) {
  fprintf(stderr,
          "usage: polybridge [-l bindaddr] [-p port]\n"
          "  Default: 127.0.0.1:7847 (set POLYBRIDGE_BIND / POLYBRIDGE_PORT to override)\n");
}

int main(int argc, char **argv) {
  const char *bindhost = getenv("POLYBRIDGE_BIND");
  if (!bindhost || !*bindhost) bindhost = "127.0.0.1";
  int port = 7847;
  const char *pe = getenv("POLYBRIDGE_PORT");
  if (pe && *pe) port = atoi(pe);

  int opt;
  while ((opt = getopt(argc, argv, "l:p:h")) != -1) {
    switch (opt) {
      case 'l':
        bindhost = optarg;
        break;
      case 'p':
        port = atoi(optarg);
        break;
      case 'h':
      default:
        usage();
        return opt == 'h' ? 0 : 1;
    }
  }

  int ls = socket(AF_INET, SOCK_STREAM, 0);
  if (ls < 0) die("socket");
  int one = 1;
  setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
#ifdef TCP_NODELAY
  setsockopt(ls, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
#endif

  struct sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons((uint16_t)port);
  if (inet_pton(AF_INET, bindhost, &addr.sin_addr) != 1) {
    fprintf(stderr, "inet_pton failed for %s\n", bindhost);
    return 1;
  }
  if (bind(ls, (struct sockaddr *)&addr, sizeof(addr)) < 0) die("bind");
  if (listen(ls, 32) < 0) die("listen");

  fprintf(stderr, "[polybridge] listening on %s:%d (max %d peers)\n", bindhost, port, MAX_PEERS);

  Peer peers[MAX_PEERS];
  memset(peers, 0, sizeof(peers));
  for (int i = 0; i < MAX_PEERS; i++) peers[i].fd = -1;

  struct pollfd fds[MAX_PEERS + 1];
  memset(fds, 0, sizeof(fds));

  for (;;) {
    int nf = 0;
    fds[nf].fd = ls;
    fds[nf].events = POLLIN;
    nf++;
    for (int i = 0; i < MAX_PEERS; i++) {
      if (!peers[i].alive || peers[i].fd < 0) continue;
      fds[nf].fd = peers[i].fd;
      fds[nf].events = POLLIN;
      fds[nf].revents = 0;
      nf++;
    }
    int pr = poll(fds, (nfds_t)nf, -1);
    if (pr < 0) {
      if (errno == EINTR) continue;
      die("poll");
    }
    if (fds[0].revents & (POLLIN | POLLERR | POLLHUP)) {
      int cfd = accept(ls, NULL, NULL);
      if (cfd < 0) {
        if (errno == EINTR || errno == EAGAIN) continue;
        perror("accept");
        continue;
      }
      int slot = find_free(peers, MAX_PEERS);
      if (slot < 0) {
        fprintf(stderr, "[polybridge] max peers, rejecting\n");
        close(cfd);
        continue;
      }
      Peer *p = &peers[slot];
      memset(p, 0, sizeof(*p));
      p->fd = cfd;
      p->alive = 1;
      p->ready = 0;
      p->phase = 0;
      p->slot = (uint16_t)slot;
      fprintf(stderr, "[polybridge] accept -> slot %d fd %d\n", slot, cfd);
    }
    for (int i = 0; i < MAX_PEERS; i++) {
      if (!peers[i].alive || peers[i].fd < 0) continue;
      int fi = -1;
      for (int j = 1; j < nf; j++) {
        if (fds[j].fd == peers[i].fd) {
          fi = j;
          break;
        }
      }
      if (fi < 0) continue;
      if (fds[fi].revents & (POLLERR | POLLHUP | POLLNVAL)) {
        close_peer(peers, MAX_PEERS, i);
        continue;
      }
      if (fds[fi].revents & POLLIN) {
        int rr = read_more(&peers[i]);
        if (rr < 0) {
          close_peer(peers, MAX_PEERS, i);
          continue;
        }
        if (process_peer(peers, MAX_PEERS, i) < 0) {
          close_peer(peers, MAX_PEERS, i);
          continue;
        }
      }
    }
  }
}
