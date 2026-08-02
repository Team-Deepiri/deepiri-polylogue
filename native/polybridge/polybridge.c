/*
 * polybridge — Deepiri Polylogue native streaming fan-out hub (C99, POSIX).
 *
 * What this does: multiplex opaque framed chunks among N TCP peers in real time.
 * What this does NOT do: read vendor chat UIs. Each LLM surface needs a thin
 * adapter that opens localhost:PORT and speaks the wire protocol, which lives
 * in polyproto.h (and docs/STREAMING_BRIDGE.md). This file is the event loop.
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

#include "polyproto.h"

#define MAX_PEERS 64

/* read_more outcomes: EOF is not an error, it means "flush, then close". */
#define RM_ERR (-1)
#define RM_RETRY 0
#define RM_DATA 1
#define RM_EOF 2

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
    if (p->in_cap >= INBUF_CAP) return RM_ERR;
    size_t ncap = p->in_cap ? p->in_cap * 2 : 4096;
    if (ncap > INBUF_CAP) ncap = INBUF_CAP;
    unsigned char *n = realloc(p->in, ncap);
    if (!n) return RM_ERR;
    p->in = n;
    p->in_cap = ncap;
  }
  ssize_t r = read(p->fd, p->in + p->in_len, p->in_cap - p->in_len);
  if (r < 0) {
    if (errno == EINTR) return RM_RETRY;
    return RM_ERR;
  }
  if (r == 0) return RM_EOF;
  p->in_len += (size_t)r;
  return RM_DATA;
}

static int send_frame(int fd, const unsigned char *buf, size_t len) {
  return write_all(fd, buf, len) < 0 ? -1 : 0;
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
  size_t need = RELAY_HDR + (size_t)plen;
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

/* Sink handed to process_peer: ctx is the peers array. */
static void hub_on_hello(void *ctx, int idx) {
  Peer *peers = (Peer *)ctx;
  Peer *p = &peers[idx];
  fprintf(stderr, "[polybridge] HELLO slot=%d id=%s label=%s prov=%s\n", idx, p->id, p->label, p->prov);
  dedupe_id(peers, MAX_PEERS, idx);
  announce_peer_up(peers, MAX_PEERS, idx);
}

static void hub_on_chunk(void *ctx, int idx, const unsigned char *payload, uint32_t plen) {
  relay_chunk((Peer *)ctx, MAX_PEERS, idx, payload, plen);
}

static const HubSink hub_sink = {hub_on_hello, hub_on_chunk};

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
      if (fds[fi].revents & (POLLERR | POLLNVAL)) {
        close_peer(peers, MAX_PEERS, i);
        continue;
      }
      /* Drain before honouring a hangup. BSD/macOS poll(2) reports POLLIN and
       * POLLHUP together while unread bytes are still queued, so closing on
       * POLLHUP first threw away frames a peer had already sent -- exactly the
       * write-then-close pattern --send-only uses. Reading until EOF is
       * spin-free: a hung-up socket yields its backlog, then 0. */
      if (fds[fi].revents & (POLLIN | POLLHUP)) {
        int rr = read_more(&peers[i]);
        if (rr == RM_ERR) {
          close_peer(peers, MAX_PEERS, i);
          continue;
        }
        if (process_peer(&peers[i], i, &hub_sink, peers) < 0) {
          close_peer(peers, MAX_PEERS, i);
          continue;
        }
        if (rr == RM_EOF) {
          if (peers[i].in_len)
            fprintf(stderr, "[polybridge] slot %d closed mid-frame, %zu byte(s) dropped\n", i,
                    peers[i].in_len);
          close_peer(peers, MAX_PEERS, i);
          continue;
        }
      }
    }
  }
}
