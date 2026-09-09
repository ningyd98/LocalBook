# apps/web/src/test

Frontend Vitest/RTL tests live in the repo-level `tests/frontend/` directory;
this directory intentionally contains no test files. The web package test
script is the intended entry point, while the configured Vitest include/setup
must be used so DOM and storage test environments are loaded.
