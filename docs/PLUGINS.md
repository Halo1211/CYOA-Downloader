# Plugins (Rust port, F-044)

Python plugins were functions registered in-process (`register_asset_scanner`, `register_engine_detector`). The Rust port cannot load Python code. Instead, a plugin is an external program that exchanges JSON over stdin and stdout (owner decision D4, approved 2026-11-20). The built-in scanner and detector are native Rust and always run.

## Where plugins live

The manifest is `~/.cyoa_downloader/plugins/plugins.json`:

```json
{"plugins": [
  {"name": "my-scanner", "kind": "asset_scanner", "command": ["python", "my_scanner.py"]},
  {"name": "my-detector", "kind": "engine_detector", "command": ["detector.exe", "--flag"]}
]}
```

- `command` is started directly, never through a shell. Relative paths resolve inside the plugins folder, which is also the working directory.
- Names must be unique per kind. A duplicate is ignored, like Python's `register` without `override`.
- Without a manifest, no plugin runs and nothing extra is spawned.

## Protocol

Each call starts the program and writes one JSON line to stdin. The program prints one JSON line to stdout and exits. The reply is limited to 4 MiB and 20 seconds; a program that times out is killed. Failures are logged at debug level and ignored, as in Python.

| Kind | Request | Reply |
|---|---|---|
| `asset_scanner` | `{"kind":"asset_scanner","text":…,"file_url":…,"base_url":…,"file_ext":".js"}` | `{"urls":["…"]}`: every entry must be a non-empty string, otherwise the whole reply is discarded |
| `engine_detector` | `{"kind":"engine_detector","html":…,"mode":"auto"}` | `{"result":{…}}` or `{"result":null}`: the first non-empty object wins |

Asset scanners run for every text file during the deep scan (as Python's `run_asset_scanner_plugins` does). Their URLs are added to the built-in scanner's results. Engine detectors can be called through the API (`PluginRegistry::run_engine_detectors`). Python's download path never calls them either; only its self-test does.

## Security

A plugin runs with the user's permissions. Install only programs you trust. The downloader never fetches or installs plugins by itself.
