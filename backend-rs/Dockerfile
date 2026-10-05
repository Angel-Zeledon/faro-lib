# StockAI Rust API image (the strangler-fig half, see docs/rust-migration.md).
#
# Build context is the REPO ROOT, like backend/Dockerfile:
#   docker build -f backend-rs/Dockerfile -t stockai-api-rs .
#
# Three stages with cargo-chef: the dependency graph is compiled in its own
# layer from a "recipe" (Cargo.toml + Cargo.lock only), so an ordinary code
# change never recompiles the ~250 crates of axum/sqlx/rustls, only this
# crate plus the LTO link (72 s on 8 cores locally against 266 s clean;
# extrapolated, minutes rather than a quarter of an hour on 2 vCPUs).

FROM lukemathwalker/cargo-chef:latest-rust-1.91-slim-bookworm AS chef
WORKDIR /src

FROM chef AS planner
COPY backend-rs/ .
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS builder
COPY --from=planner /src/recipe.json recipe.json
# Dependencies only: cached until Cargo.toml / Cargo.lock change.
RUN cargo chef cook --release --recipe-path recipe.json
COPY backend-rs/ .
RUN cargo build --release --bin stockai-api

# distroless/cc: glibc + libgcc and nothing else (no shell, no package
# manager). TLS is rustls, so there is no OpenSSL to ship either.
FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=builder /src/target/release/stockai-api /usr/local/bin/stockai-api
ENV RUST_API_BIND=0.0.0.0:8021 \
    RUST_LOG=info
EXPOSE 8021
USER nonroot
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=5s \
    CMD ["/usr/local/bin/stockai-api", "healthcheck"]
ENTRYPOINT ["/usr/local/bin/stockai-api"]
