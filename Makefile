UV ?= uv
BUN ?= bun
PYTEST ?= $(UV) run --no-project --python '>=3.14' --with pytest python -m pytest
VERSION = $(shell $(UV) version --short)
RELEASE_FILES = dist/hexa_pipelines-$(VERSION).tar.gz dist/hexa_pipelines-$(VERSION)-py3-none-any.whl

.PHONY: test test-py test-js test-all test-release release

test: test-py

test-py:
	$(PYTEST) -q

test-js:
	$(BUN) test tests/js

test-all: test-py test-js

test-release:
	$(UV) build --no-sources

release: test
	$(MAKE) test-release
	$(UV) publish $(RELEASE_FILES)
