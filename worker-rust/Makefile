# Makefile for whirr-worker

.PHONY: build release clean install linux

# Default target: debug build
build:
	cargo build

# Release build (optimized for size)
release:
	cargo build --release
	@echo "Binary: target/release/whirr-worker"
	@ls -lh target/release/whirr-worker

# Cross-compile for Linux (for deploying to GPU nodes)
# Requires: rustup target add x86_64-unknown-linux-gnu
linux:
	cargo build --release --target x86_64-unknown-linux-gnu
	@echo "Binary: target/x86_64-unknown-linux-gnu/release/whirr-worker"
	@ls -lh target/x86_64-unknown-linux-gnu/release/whirr-worker

# Cross-compile for Linux musl (fully static, no glibc dependency)
# Requires: rustup target add x86_64-unknown-linux-musl
linux-musl:
	cargo build --release --target x86_64-unknown-linux-musl
	@echo "Binary: target/x86_64-unknown-linux-musl/release/whirr-worker"
	@ls -lh target/x86_64-unknown-linux-musl/release/whirr-worker

# Install locally
install: release
	cp target/release/whirr-worker /usr/local/bin/

# Clean build artifacts
clean:
	cargo clean

# Run tests
test:
	cargo test

# Check code without building
check:
	cargo check

# Format code
fmt:
	cargo fmt

# Lint
lint:
	cargo clippy -- -D warnings
