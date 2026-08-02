CC ?= gcc
CFLAGS ?= -Wall -Wextra -O2 -std=c99 -D_DEFAULT_SOURCE
SANFLAGS ?= -fsanitize=address,undefined -fno-omit-frame-pointer -g

.PHONY: all clean test test-asan fuzz fuzz-standalone

all: polybridge polyclient

polybridge: polybridge.c polyproto.c polyproto.h
	$(CC) $(CFLAGS) -o $@ polybridge.c polyproto.c

polyclient: polyclient.c polyproto.c polyproto.h
	$(CC) $(CFLAGS) -o $@ polyclient.c polyproto.c

test_polyproto: test_polyproto.c polyproto.c polyproto.h
	$(CC) $(CFLAGS) -o $@ test_polyproto.c polyproto.c

test_e2e: test_e2e.c polyproto.c polyproto.h
	$(CC) $(CFLAGS) -o $@ test_e2e.c polyproto.c

test: test_polyproto test_e2e polybridge polyclient
	./test_polyproto
	./test_e2e ./polybridge ./polyclient

test-asan: polyproto.c polyproto.h test_polyproto.c test_e2e.c polybridge.c polyclient.c
	$(CC) $(CFLAGS) $(SANFLAGS) -o test_polyproto_asan test_polyproto.c polyproto.c
	$(CC) $(CFLAGS) $(SANFLAGS) -o test_e2e_asan test_e2e.c polyproto.c
	$(CC) $(CFLAGS) $(SANFLAGS) -o polybridge_asan polybridge.c polyproto.c
	$(CC) $(CFLAGS) $(SANFLAGS) -o polyclient_asan polyclient.c polyproto.c
	./test_polyproto_asan
	./test_e2e_asan ./polybridge_asan ./polyclient_asan

# Real libFuzzer. Apple Command Line Tools ship no libclang_rt.fuzzer runtime,
# so this needs Linux or Homebrew LLVM; use fuzz-standalone elsewhere.
fuzz: fuzz/fuzz_frames.c fuzz/fuzz_client.c polyproto.c
	$(CC) $(CFLAGS) -fsanitize=fuzzer,address,undefined -DPOLYPROTO_QUIET -g -o fuzz_frames fuzz/fuzz_frames.c polyproto.c
	$(CC) $(CFLAGS) -fsanitize=fuzzer,address,undefined -DPOLYPROTO_QUIET -g -o fuzz_client fuzz/fuzz_client.c polyproto.c

# Same LLVMFuzzerTestOneInput entry points, driven by our own main so the
# harnesses still run under ASan/UBSan where libFuzzer is unavailable.
fuzz-standalone: fuzz/fuzz_frames.c fuzz/fuzz_client.c fuzz/standalone_main.c polyproto.c
	$(CC) $(CFLAGS) $(SANFLAGS) -DPOLYPROTO_QUIET -o fuzz_frames_standalone fuzz/fuzz_frames.c fuzz/standalone_main.c polyproto.c
	$(CC) $(CFLAGS) $(SANFLAGS) -DPOLYPROTO_QUIET -o fuzz_client_standalone fuzz/fuzz_client.c fuzz/standalone_main.c polyproto.c

clean:
	rm -f polybridge polyclient test_polyproto test_e2e \
		test_polyproto_asan test_e2e_asan polybridge_asan polyclient_asan \
		fuzz_frames fuzz_client fuzz_frames_standalone fuzz_client_standalone
	rm -rf *.dSYM

# Two clients: B listens, A sends (run from same dir with polybridge already up on 7848).
test-local:
	@echo "Run in three terminals: make polybridge && ./polybridge -p 7848" \
		"then ./polyclient 127.0.0.1 7848 b B x && ./polyclient 127.0.0.1 7848 a A x hello"
