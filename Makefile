UV ?= uv
PYTEST ?= $(UV) run --no-project --python '>=3.14' --with pytest python -m pytest
VERSION = $(shell $(UV) version --short)
RELEASE_FILES = dist/hexa_pipelines-$(VERSION).tar.gz dist/hexa_pipelines-$(VERSION)-py3-none-any.whl

.PHONY: test test-release release

test:
	$(PYTEST) -q

test-release:
	$(UV) build --no-sources

release: test
	$(MAKE) test-release
	$(UV) publish $(RELEASE_FILES)
