---
name: feature-parity
description: Preserve observable Python behavior and validate equivalent Rust implementations against fixtures.
---
# Verification
Inventory each feature, source path, expected behavior, errors, fixtures, destination path and regression test before implementation. Keep original Python working. Use identical input for Python and Rust and compare normalized outputs, filenames, checksums, Unicode, configuration, retries, cancellation and failure behavior. Document intentional differences for owner approval. Mock HTTP; do not use production credentials. Record actual command results; never weaken tests or mark untested items passed. Update docs/FEATURE_PARITY.md and docs/MIGRATION_PROGRESS.md.
