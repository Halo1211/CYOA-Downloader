# Python-to-Rust migration

Keep the original Python runnable. Audit first, plan second, implement approved modules only. Prefer safe idiomatic Rust, minimal dependencies, bounded concurrency, and non-blocking GUI. Never access secrets, push, change billing or delete unrelated code. Use feature-parity and gui-performance skills as relevant. No recursive agent delegation. Respect allowed file scope. Run cargo fmt --all -- --check, cargo check --all-targets, cargo clippy --all-targets -- -D warnings, cargo test; report actual results. Use Python baseline fixtures and record untested behaviors. Maintain docs/MIGRATION\_PROGRESS.md and docs/FEATURE\_PARITY.md. Wait for approval on architecture changes.

# Routing (approved 2026-11-18)

MiMo/Qwen: only narrow, mechanically checkable tasks; never trust their numbers or code without compiling/testing. Luna: implementation. Sol: review of network, concurrency, security, browser. Rust code lives in rust/; Python stays the reference. Goal: all features kept (Rust replacements allowed), outputs identical or better, differences documented in docs/FEATURE\_PARITY.md.



\## Windows Codex File Editing Compatibility



Confirmed environment:



\- Codex CLI 0.169.0

\- Windows 11

\- Workspace-write sandbox

\- Repository: D:\\Projects\\CYOA-Downloader



Reproducible diagnostic results:



\- apply\_patch successfully modifies existing files on drive C.

\- apply\_patch repeatedly fails to modify an existing probe file inside the project on drive D.

\- PowerShell successfully modifies the same file within the same workspace-write sandbox.

\- The affected file is not read-only.



This is a file-editing tool compatibility issue until proven otherwise.



\### Mandatory editing policy



When apply\_patch fails with "Failed to write file":



1\. Stop retrying the same failing operation.

2\. Verify the target file and assigned workspace scope.

3\. Use an alternative permitted editing method within the existing sandbox.

4\. Prefer precise Python-based replacements that preserve encoding and line endings.

5\. PowerShell editing is acceptable when encoding and file integrity are preserved.

6\. Consider git apply --check followed by git apply for validated unified diffs.

7\. Verify the actual file content after editing.

8\. Inspect git diff.

9\. Run cargo fmt and relevant compilation and regression tests.



Never rewrite an entire large file unnecessarily.



Never create unnecessary modules merely to avoid editing existing files.



Never bypass sandbox restrictions or genuine permission denials.



Never modify system permissions, disable antivirus, or elevate privileges.



If alternative permitted editing methods also fail, return a complete unified diff to Claude for integration.



Do not abandon an implementation merely because apply\_patch is unavailable.



Preserve the original Python application and all unrelated changes.



Never push to GitHub.

