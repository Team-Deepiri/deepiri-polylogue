/* polyproto — wire codec shared by polybridge and polyclient. See polyproto.h. */

#include "polyproto.h"

#include <stdio.h>
#include <string.h>

void consume(Peer *p, size_t n) {
  if (n >= p->in_len) {
    p->in_len = 0;
    return;
  }
  memmove(p->in, p->in + n, p->in_len - n);
  p->in_len -= n;
}

int u16be(const unsigned char *b) {
  return (int)((b[0] << 8) | b[1]);
}

uint32_t u32be(const unsigned char *b) {
  return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | (uint32_t)b[3];
}

void put_u16be(unsigned char *d, uint16_t v) {
  d[0] = (unsigned char)((v >> 8) & 0xff);
  d[1] = (unsigned char)(v & 0xff);
}

void put_u32be(unsigned char *d, uint32_t v) {
  d[0] = (unsigned char)((v >> 24) & 0xff);
  d[1] = (unsigned char)((v >> 16) & 0xff);
  d[2] = (unsigned char)((v >> 8) & 0xff);
  d[3] = (unsigned char)(v & 0xff);
}

int build_peer_up(unsigned char *out, size_t *olen, uint16_t slot, const Peer *info) {
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

int build_peer_down(unsigned char *out, size_t *olen, uint16_t slot, const char *id) {
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

int build_relay(unsigned char *out, size_t *olen, uint16_t from_slot, const unsigned char *payload,
                uint32_t plen) {
  if (plen > MAX_CHUNK) return -1;
  size_t need = RELAY_HDR + (size_t)plen;
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

int process_peer(Peer *p, int idx, const HubSink *sink, void *ctx) {
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
      sink->on_hello(ctx, idx);
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
      sink->on_chunk(ctx, idx, p->in, p->want_chunk);
      consume(p, p->want_chunk);
      p->phase = 0;
      continue;
    }
    return -1;
  }
}

static size_t frame_len_peer_up(const unsigned char *p, size_t rem) {
  if (rem < 3) return 0;
  size_t o = 1u + 2u;
  if (rem < o + 2) return 0;
  uint16_t idl = (uint16_t)u16be(p + o);
  o += 2u;
  if (rem < o + idl + 2) return 0;
  o += idl;
  uint16_t lb = (uint16_t)u16be(p + o);
  o += 2u;
  if (rem < o + lb + 2) return 0;
  o += lb;
  uint16_t pr = (uint16_t)u16be(p + o);
  o += 2u;
  if (rem < o + pr) return 0;
  o += pr;
  return o;
}

static size_t frame_len_peer_down(const unsigned char *p, size_t rem) {
  if (rem < 5) return 0;
  uint16_t idl = (uint16_t)u16be(p + 3);
  if (rem < 5u + idl) return 0;
  return 5u + idl;
}

size_t polyclient_parse(const unsigned char *buf, size_t len, relay_cb on_relay, void *ctx) {
  size_t off = 0;
  while (off < len) {
    const unsigned char *p = buf + off;
    size_t rem = len - off;
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
      if (rem < RELAY_HDR) break;
      uint32_t plen = u32be(p + 3);
      if (rem < RELAY_HDR + (size_t)plen) break;
      on_relay(ctx, (uint16_t)u16be(p + 1), p + RELAY_HDR, plen);
      off += RELAY_HDR + (size_t)plen;
      continue;
    }
    fprintf(stderr, "[polyclient] unknown byte 0x%02x, skipping\n", t);
    off++;
  }
  return off;
}
