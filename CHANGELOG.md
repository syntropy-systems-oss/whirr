# Changelog

## [0.5.3](https://github.com/syntropy-systems-oss/whirr/compare/v0.5.2...v0.5.3) (2026-01-16)


### Bug Fixes

* handle PostgreSQL datetime objects in WorkerRecord ([b367f75](https://github.com/syntropy-systems-oss/whirr/commit/b367f751509e60e8d7f1f7f4b3c7f0645024216d))

## [0.5.2](https://github.com/syntropy-systems-oss/whirr/compare/v0.5.1...v0.5.2) (2026-01-16)


### Bug Fixes

* improve module docstring ([781ab6a](https://github.com/syntropy-systems-oss/whirr/commit/781ab6a84e43078c95a0241c189d5d576eac2231))
* trigger release ([e5a78ae](https://github.com/syntropy-systems-oss/whirr/commit/e5a78ae319982779485ec5abd9b04ed4bf6f227c))

## [0.5.1](https://github.com/syntropy-systems-oss/whirr/compare/v0.5.0...v0.5.1) (2026-01-14)


### Bug Fixes

* pre-existing test failures, add pre-commit hooks, fix type errors ([f32429c](https://github.com/syntropy-systems-oss/whirr/commit/f32429c6d368ada03f14db2fabeaa6e375450df4))

## [0.5.0](https://github.com/syntropy-systems-oss/whirr/compare/v0.4.1...v0.5.0) (2026-01-14)


### Features

* add whirr ablate for ablation studies ([1fbc2ae](https://github.com/syntropy-systems-oss/whirr/commit/1fbc2aeeeb26792aee9a8d126de5fec412d0bf7d))


### Bug Fixes

* add run_id to submit response and artifact retrieval API ([67324f7](https://github.com/syntropy-systems-oss/whirr/commit/67324f74a572cc21b839c90ebe889e2d53f73ce9))
* add wait_for_job and metrics API for programmatic job management ([f079502](https://github.com/syntropy-systems-oss/whirr/commit/f0795021f52649cb1a1c0139932dfe0896b1d8c4))

## [0.4.1](https://github.com/syntropy-systems-oss/whirr/compare/v0.4.0...v0.4.1) (2026-01-14)


### Bug Fixes

* shared storage bind mount and documentation improvements ([5453c2e](https://github.com/syntropy-systems-oss/whirr/commit/5453c2ec96cc21192a48c21cf61142ef6ac64d1d))
* use toml type for Cargo.toml in release-please config ([904ea25](https://github.com/syntropy-systems-oss/whirr/commit/904ea25101223a145695f3d4c5514e56d7c5e9dc))


### Documentation

* add server setup guide and update README ([77017c9](https://github.com/syntropy-systems-oss/whirr/commit/77017c95b68d4e2ca133806796b4fa1796f6ec44))

## [0.4.0](https://github.com/syntropy-systems-oss/whirr/compare/v0.3.0...v0.4.0) (2026-01-14)


### Features

* add Rust worker for minimal memory footprint ([f58c331](https://github.com/syntropy-systems-oss/whirr/commit/f58c331572e2315167d3eac6477a19814c820fb6))


### Bug Fixes

* remove default password from docker-compose.yml ([99c750e](https://github.com/syntropy-systems-oss/whirr/commit/99c750e3bf5e6413e1524bb8e2c34978719af2ed))

## [0.3.0](https://github.com/syntropy-systems-oss/whirr/compare/v0.2.0...v0.3.0) (2026-01-13)


### Features

* add dashboard, compare, export commands and reproducibility features ([fa63054](https://github.com/syntropy-systems-oss/whirr/commit/fa63054a61693d45abb33f7ee5d091b5cd5cf74e))
* add server mode for multi-machine orchestration (v0.4) ([8fc53aa](https://github.com/syntropy-systems-oss/whirr/commit/8fc53aaeeb9b596607401464871123d2a031ef44))


### Bug Fixes

* SQLite threading, test assertions, and version numbering ([5926b96](https://github.com/syntropy-systems-oss/whirr/commit/5926b96a4240327429c6025de2d3808c89ca2d0c))

## [0.2.0](https://github.com/syntropy-systems-oss/whirr/compare/v0.1.0...v0.2.0) (2026-01-13)


### Features

* add v0.2 features - sweep, retry, watch, orphan detection, system metrics ([0aaba35](https://github.com/syntropy-systems-oss/whirr/commit/0aaba356a75f024636ddac60e2e1198db357ad9c))


### Documentation

* add comprehensive documentation ([47b5067](https://github.com/syntropy-systems-oss/whirr/commit/47b50670e822ab201b04cc98fde579886c9fe9c9))
