# USBYPASS — top-level convenience targets.
#
# Build packages locally:
#   make deb        — produce ../usbypass_VERSION_all.deb
#   make rpm        — produce dist/usbypass-VERSION-1.noarch.rpm
#   make arch       — produce dist/usbypass-VERSION-1-any.pkg.tar.zst
#   make tarball    — produce dist/usbypass-VERSION.tar.gz (used by RPM/Arch)
#   make wheel      — produce dist/usbypass-VERSION-py3-none-any.whl
#
# System operations:
#   sudo make install   — equivalent to ./install.sh
#   sudo make uninstall — equivalent to ./uninstall.sh
#
# Dev:
#   make test       — run pytest
#   make smoke      — run the import + functional smoke tests with no pytest
#   make clean      — remove build artifacts

PYTHON ?= python3
VERSION := $(shell $(PYTHON) -c 'import re,sys; \
    f=open("pyproject.toml").read(); \
    print(re.search(r"version *= *\"([^\"]+)\"", f).group(1))')
DIST := dist

.PHONY: all help install uninstall test smoke clean wheel tarball deb rpm arch

all: help

help:
	@echo "USBYPASS $(VERSION) — Make targets:"
	@echo "  make install     — install via install.sh (needs sudo)"
	@echo "  make uninstall   — remove via uninstall.sh (needs sudo)"
	@echo "  make wheel       — build a wheel into ./$(DIST)/"
	@echo "  make tarball     — build a source tarball for distros"
	@echo "  make deb         — build a Debian package (needs dpkg-buildpackage)"
	@echo "  make rpm         — build an RPM (needs rpmbuild)"
	@echo "  make arch        — build an Arch package (needs makepkg)"
	@echo "  make test        — run pytest (if installed)"
	@echo "  make smoke       — run pytest-free smoke tests"
	@echo "  make clean       — remove build artifacts"

install:
	./install.sh

uninstall:
	./uninstall.sh

test:
	@$(PYTHON) -m pytest tests/ -q || \
		(echo "pytest not installed; falling back to smoke tests" && $(MAKE) smoke)

smoke:
	@PYTHONPATH=src $(PYTHON) tests/smoke.py

wheel:
	@mkdir -p $(DIST)
	$(PYTHON) -m pip wheel --no-deps -w $(DIST) .

tarball:
	@mkdir -p $(DIST)
	@tar \
	    --exclude='./.git' \
	    --exclude='./.github' \
	    --exclude='./.claude' \
	    --exclude='./.remember' \
	    --exclude='./dist' \
	    --exclude='./build' \
	    --exclude='./debian/usbypass' \
	    --exclude='./debian/.debhelper' \
	    --exclude='./debian/files' \
	    --exclude='./debian/debhelper-build-stamp' \
	    --exclude='./debian/usbypass.substvars' \
	    --exclude='./debian/usbypass.debhelper.log' \
	    --exclude='./.venv' \
	    --exclude='./venv' \
	    --exclude='*.pyc' \
	    --exclude='__pycache__' \
	    --exclude='*.egg-info' \
	    --transform 's,^\.,usbypass-$(VERSION),' \
	    -czf $(DIST)/usbypass-$(VERSION).tar.gz .
	@echo "wrote $(DIST)/usbypass-$(VERSION).tar.gz"

deb:
	@command -v dpkg-buildpackage >/dev/null || \
		(echo "ERROR: dpkg-buildpackage not found. Install: sudo apt install build-essential devscripts debhelper dh-python python3-all" && exit 1)
	dpkg-buildpackage -us -uc -b
	@echo
	@echo "Built: $$(ls ../usbypass_*.deb 2>/dev/null | head -1)"
	@echo "Install with: sudo dpkg -i ../usbypass_$(VERSION)-1_all.deb && sudo apt -f install"

rpm: tarball
	@command -v rpmbuild >/dev/null || \
		(echo "ERROR: rpmbuild not found. Install: sudo dnf install rpm-build pyproject-rpm-macros python3-devel" && exit 1)
	@mkdir -p $(HOME)/rpmbuild/SOURCES $(HOME)/rpmbuild/SPECS
	cp $(DIST)/usbypass-$(VERSION).tar.gz $(HOME)/rpmbuild/SOURCES/
	cp packaging/rpm/usbypass.spec $(HOME)/rpmbuild/SPECS/
	rpmbuild -ba $(HOME)/rpmbuild/SPECS/usbypass.spec
	@cp $(HOME)/rpmbuild/RPMS/noarch/usbypass-$(VERSION)*.rpm $(DIST)/ 2>/dev/null || true
	@echo
	@echo "Install with: sudo dnf install $(DIST)/usbypass-$(VERSION)-*.noarch.rpm"

arch: tarball
	@command -v makepkg >/dev/null || \
		(echo "ERROR: makepkg not found. Run on Arch/Manjaro." && exit 1)
	@mkdir -p $(DIST)/arch-build
	cp packaging/arch/PKGBUILD $(DIST)/arch-build/
	cp packaging/arch/99-usbypass.rules $(DIST)/arch-build/
	cp packaging/arch/usbypass.install $(DIST)/arch-build/
	cp $(DIST)/usbypass-$(VERSION).tar.gz $(DIST)/arch-build/
	cd $(DIST)/arch-build && makepkg -f --skipchecksums
	@cp $(DIST)/arch-build/*.pkg.tar.* $(DIST)/ 2>/dev/null || true
	@echo
	@echo "Install with: sudo pacman -U $(DIST)/usbypass-$(VERSION)-1-any.pkg.tar.zst"

clean:
	rm -rf $(DIST) build *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	rm -rf debian/usbypass debian/.debhelper debian/files debian/debhelper-build-stamp
	rm -rf debian/usbypass.substvars debian/usbypass.debhelper.log
