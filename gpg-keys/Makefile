SRC_KEYS := $(shell find . -type f -not -name '*.gpg' -not -name '*~*' -not -name Makefile -not -name 'debian*' -not -name 'ubuntu-*' -not -name 'fedora-asahi*' -not -name 'archlinux*' -not -name 'RPM-GPG-KEY-fedora-*')
BIN_KEYS := $(patsubst %,%.gpg,$(SRC_KEYS))

all: $(BIN_KEYS) debian.gpg ubuntu.gpg asahi.gpg archlinux.gpg fedora.gpg

%.gpg: %
	gpg --dearmor <$< >$@

DEBIAN_KEYS := $(shell echo debian-*)
debian.gpg: $(DEBIAN_KEYS)
	gpg --no-default-keyring --keyring=./$@ --import $^ || true

UBUNTU_KEYS := $(shell echo ubuntu-*)
ubuntu.gpg: $(UBUNTU_KEYS)
	gpg --no-default-keyring --keyring=./$@ --import $^ || true

ASAHI_KEYS := $(shell echo fedora-asahi*)
asahi.gpg: $(ASAHI_KEYS)
	gpg --no-default-keyring --keyring=./$@ --import $^ || true

ARCH_KEYS := $(shell echo archlinux-*)
archlinux.gpg: $(ARCH_KEYS)
	gpg --no-default-keyring --keyring=./$@ --import $^ || true

FEDORA_KEYS := $(shell echo RPM-GPG-KEY-fedora-*)
fedora.gpg: $(FEDORA_KEYS)
	gpg --no-default-keyring --keyring=./$@ --import $^ || true
