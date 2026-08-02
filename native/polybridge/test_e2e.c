/*
 * End-to-end regression test for the write-then-close message loss.
 *
 * A peer that sends CHUNK frames and immediately close()s -- the pattern
 * `polyclient --send-only` documents "for scripts / token emitters" -- must
 * still have those frames relayed. The hub used to test POLLHUP before POLLIN
 * and close the peer, discarding both the bytes buffered in p->in and the bytes
 * still queued in the socket.
 *
 * Portability note: this is a real guard on macOS/BSD, where poll(2) reports
 * POLLIN|POLLHUP together while unread data is still queued. Linux reports
 * POLLHUP differently for a peer close(), so this test may pass there even
 * against the old code; it is not a substitute for reading the fix.
 *
 * usage: test_e2e ./polybridge ./polyclient
 */

#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "polyproto.h"

static int failures = 0;

#define CHECK(cond, msg)                                                  \
  do {                                                                    \
    if (!(cond)) {                                                        \
      failures++;                                                         \
      fprintf(stdout, "    FAIL %s:%d: %s\n", __FILE__, __LINE__, (msg)); \
    }                                                                     \
  } while (0)

static void msleep(int ms) {
  struct timespec ts;
  ts.tv_sec = ms / 1000;
  ts.tv_nsec = (long)(ms % 1000) * 1000000L;
  nanosleep(&ts, NULL);
}

/* Ask the kernel for a free loopback port, then hand it to the bridge. */
static int pick_port(void) {
  int s = socket(AF_INET, SOCK_STREAM, 0);
  if (s < 0) return -1;
  struct sockaddr_in a;
  memset(&a, 0, sizeof(a));
  a.sin_family = AF_INET;
  a.sin_port = 0;
  a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (bind(s, (struct sockaddr *)&a, sizeof(a)) < 0) {
    close(s);
    return -1;
  }
  socklen_t alen = sizeof(a);
  if (getsockname(s, (struct sockaddr *)&a, &alen) < 0) {
    close(s);
    return -1;
  }
  int port = ntohs(a.sin_port);
  close(s);
  return port;
}

static pid_t spawn_bridge(const char *path, int port) {
  char ps[16];
  snprintf(ps, sizeof(ps), "%d", port);
  pid_t pid = fork();
  if (pid < 0) return -1;
  if (pid == 0) {
    int devnull = open("/dev/null", O_WRONLY);
    if (devnull >= 0) {
      dup2(devnull, STDERR_FILENO);
      close(devnull);
    }
    execl(path, path, "-p", ps, (char *)NULL);
    _exit(127);
  }
  return pid;
}

/* Connect and send HELLO; retries while the bridge is still starting up. */
static int connect_peer(int port, const char *id) {
  int fd = -1;
  for (int attempt = 0; attempt < 100; attempt++) {
    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons((uint16_t)port);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, (struct sockaddr *)&a, sizeof(a)) == 0) break;
    close(fd);
    fd = -1;
    msleep(20);
  }
  if (fd < 0) return -1;
  int one = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

  unsigned char h[64];
  size_t il = strlen(id);
  h[0] = (unsigned char)T_HELLO;
  put_u16be(h + 1, (uint16_t)il);
  put_u16be(h + 3, 1);
  put_u16be(h + 5, 1);
  memcpy(h + 7, id, il);
  h[7 + il] = 'L';
  h[8 + il] = 'P';
  if (write(fd, h, 9 + il) < 0) {
    close(fd);
    return -1;
  }
  return fd;
}

typedef struct {
  int n;
  uint32_t last_len;
} Got;

static void on_relay(void *ctx, uint16_t slot, const unsigned char *payload, uint32_t plen) {
  (void)slot;
  (void)payload;
  Got *g = (Got *)ctx;
  g->n++;
  g->last_len = plen;
}

/* Read until a RELAY of exactly want bytes shows up, or the socket goes quiet. */
static int await_relay(int fd, uint32_t want) {
  struct timeval tv;
  tv.tv_sec = 2;
  tv.tv_usec = 0;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

  size_t cap = RELAY_HDR + MAX_CHUNK;
  unsigned char *buf = (unsigned char *)malloc(cap);
  if (!buf) return 0;
  size_t have = 0;
  Got g;
  memset(&g, 0, sizeof(g));

  while (have < cap) {
    ssize_t r = read(fd, buf + have, cap - have);
    if (r <= 0) break;
    have += (size_t)r;
    size_t used = polyclient_parse(buf, have, on_relay, &g);
    memmove(buf, buf + used, have - used);
    have -= used;
    if (g.n > 0 && g.last_len == want) {
      free(buf);
      return 1;
    }
  }
  free(buf);
  return 0;
}

/* Sender writes HELLO + CHUNK then closes with no linger. */
static void send_then_close(int port, uint32_t plen) {
  int fd = connect_peer(port, "sender");
  if (fd < 0) return;
  msleep(150); /* let HELLO be processed before the chunk lands */
  unsigned char *frame = (unsigned char *)malloc(5 + plen);
  frame[0] = (unsigned char)T_CHUNK;
  put_u32be(frame + 1, plen);
  memset(frame + 5, 'Z', plen);
  size_t off = 0, tot = 5 + plen;
  while (off < tot) {
    ssize_t w = write(fd, frame + off, tot - off);
    if (w <= 0) break;
    off += (size_t)w;
  }
  free(frame);
  /* Mirror polyclient --send-only exactly: half-close, drain the PEER_UP the hub
   * pushed us, then close. A bare close() here leaves unread data in the receive
   * queue, which makes the kernel emit RST rather than FIN -- the hub then sees
   * POLLERR on Linux and drops the chunk we just sent. */
  shutdown(fd, SHUT_WR);
  struct timeval tv;
  tv.tv_sec = 2;
  tv.tv_usec = 0;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  char sink[4096];
  while (read(fd, sink, sizeof(sink)) > 0) {
  }
  close(fd);
}

static void run_case(const char *bridge, uint32_t plen) {
  int port = pick_port();
  CHECK(port > 0, "found a free loopback port");
  if (port <= 0) return;

  pid_t bp = spawn_bridge(bridge, port);
  CHECK(bp > 0, "spawned polybridge");
  if (bp <= 0) return;

  int rx = connect_peer(port, "receiver");
  CHECK(rx >= 0, "receiver connected and sent HELLO");
  if (rx < 0) {
    kill(bp, SIGKILL);
    waitpid(bp, NULL, 0);
    return;
  }
  msleep(200); /* receiver must be 'ready' before the sender's chunk arrives */

  send_then_close(port, plen);

  int got = await_relay(rx, plen);
  char msg[128];
  snprintf(msg, sizeof(msg), "%u-byte chunk survives write-then-close", plen);
  CHECK(got, msg);
  printf("%-4s e2e write-then-close, payload=%u\n", got ? "ok" : "FAIL", plen);

  close(rx);
  kill(bp, SIGKILL);
  waitpid(bp, NULL, 0);
}

static pid_t spawn_client(const char *path, int port, const char *outfile) {
  char ps[16];
  snprintf(ps, sizeof(ps), "%d", port);
  pid_t pid = fork();
  if (pid < 0) return -1;
  if (pid == 0) {
    int out = open(outfile, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (out >= 0) {
      dup2(out, STDOUT_FILENO);
      close(out);
    }
    execl(path, path, "127.0.0.1", ps, "b", "B", "x", (char *)NULL);
    _exit(127);
  }
  return pid;
}

/*
 * Regression for the client's fixed 64 KiB receive buffer: the protocol allows
 * MAX_CHUNK payloads, so a RELAY can be far larger than 65536. The old code
 * called read(fd, rb + have, sizeof(rb) - have - 1) with a zero-length count
 * once the buffer filled, read 0, and mistook that for EOF -- exiting 0 with
 * empty stdout and empty stderr. Drives the real polyclient binary.
 */
static void run_large_relay_case(const char *bridge, const char *client, uint32_t plen) {
  char outfile[64];
  // Per-process path: a fixed name means two concurrent runs (CI matrix, or a
  // developer and a test harness at once) unlink each other's output mid-read.
  snprintf(outfile, sizeof(outfile), "/tmp/polyclient_e2e_out.%ld.bin", (long)getpid());
  int port = pick_port();
  CHECK(port > 0, "found a free loopback port");
  if (port <= 0) return;

  pid_t bp = spawn_bridge(bridge, port);
  CHECK(bp > 0, "spawned polybridge");
  if (bp <= 0) return;
  msleep(300);

  pid_t cp = spawn_client(client, port, outfile);
  CHECK(cp > 0, "spawned polyclient");
  msleep(400); /* client HELLO must land before the chunk is relayed */

  int fd = connect_peer(port, "sender");
  CHECK(fd >= 0, "sender connected");
  if (fd >= 0) {
    msleep(150);
    unsigned char *frame = (unsigned char *)malloc(5 + plen);
    frame[0] = (unsigned char)T_CHUNK;
    put_u32be(frame + 1, plen);
    memset(frame + 5, 'Z', plen);
    size_t off = 0, tot = 5 + plen;
    while (off < tot) {
      ssize_t w = write(fd, frame + off, tot - off);
      if (w <= 0) break;
      off += (size_t)w;
    }
    free(frame);
    msleep(1200); /* stay connected: the loss must not need a disconnect */
    close(fd);
  }
  msleep(300);

  /* waitpid(WNOHANG), not kill(pid, 0): a client that already exited is still a
   * zombie here, and kill() would report it as alive. */
  int status = 0;
  int reaped = (cp > 0) ? (waitpid(cp, &status, WNOHANG) == cp) : 0;
  int alive = (cp > 0 && !reaped);
  struct stat st;
  off_t got = (stat(outfile, &st) == 0) ? st.st_size : -1;
  /* polyclient prints the payload plus one newline per RELAY. */
  off_t want = (off_t)plen + 1;
  CHECK(alive, "polyclient did not mistake a large frame for EOF");
  CHECK(got == want, "polyclient printed the whole large payload");
  printf("%-4s e2e large RELAY, payload=%u (client %s, stdout=%lld bytes, want %lld)\n",
         (alive && got == want) ? "ok" : "FAIL", plen, alive ? "alive" : "EXITED", (long long)got,
         (long long)want);

  if (alive) {
    kill(cp, SIGKILL);
    waitpid(cp, NULL, 0);
  }
  kill(bp, SIGKILL);
  waitpid(bp, NULL, 0);
  unlink(outfile);
}

int main(int argc, char **argv) {
  if (argc < 3) {
    fprintf(stderr, "usage: %s ./polybridge ./polyclient\n", argv[0]);
    return 2;
  }
  signal(SIGPIPE, SIG_IGN);

  /* 16 bytes races on whether data and FIN coalesce into one poll; 100000
   * bytes needs several read_more growth cycles, so the FIN has certainly
   * landed by the second one. Both were lost before the fix. */
  run_case(argv[1], 16);
  run_case(argv[1], 100000);
  run_large_relay_case(argv[1], argv[2], 100000);

  printf("\n%d failure(s)\n", failures);
  return failures ? 1 : 0;
}
