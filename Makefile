UV ?= uv
BUN ?= bun
PYTEST ?= $(UV) run --no-project --python '>=3.14' --with pytest python -m pytest
VERSION = $(shell $(UV) version --short)
RELEASE_FILES = dist/hexa_pipelines-$(VERSION).tar.gz dist/hexa_pipelines-$(VERSION)-py3-none-any.whl
GIT_REMOTE ?= origin
GIT_BRANCH ?= main

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
	@set -eu; \
		status=$$(git status --porcelain --untracked-files=normal); \
		if [ -n "$$status" ]; then \
			echo "release requires a clean Git checkout (commit or stash changes first)" >&2; exit 1; \
		fi
	@if git rev-parse -q --verify "refs/tags/v$(VERSION)" >/dev/null; then \
		echo "tag v$(VERSION) already exists" >&2; exit 1; \
	fi
	$(MAKE) test-release
	@set -eu; \
		commit=$$(git rev-parse HEAD); \
		$(UV) publish $(RELEASE_FILES); \
		git tag -a "v$(VERSION)" -m "Release $(VERSION)" "$$commit"; \
		git push --atomic "$(GIT_REMOTE)" "$$commit:refs/heads/$(GIT_BRANCH)" "refs/tags/v$(VERSION)"
