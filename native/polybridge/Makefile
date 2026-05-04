CC ?= gcc
CFLAGS ?= -Wall -Wextra -O2 -std=c99 -D_DEFAULT_SOURCE

.PHONY: all clean test

all: polybridge polyclient

polybridge: polybridge.c
	$(CC) $(CFLAGS) -o $@ polybridge.c

polyclient: polyclient.c
	$(CC) $(CFLAGS) -o $@ polyclient.c

clean:
	rm -f polybridge polyclient

# Two clients: B listens, A sends (run from same dir with polybridge already up on 7848).
test-local:
	@echo "Run in three terminals: make polybridge && ./polybridge -p 7848" \
		"then ./polyclient 127.0.0.1 7848 b B x && ./polyclient 127.0.0.1 7848 a A x hello"
