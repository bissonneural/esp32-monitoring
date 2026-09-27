# Repository Guidelines

## Project Structure & Module Organization

- `src/`: C++17 Arduino firmware for M5Stack Cardputer ADV. `main.cpp` manages device state, `dashboard_client.cpp` fetches/parses responses, and `screens.cpp` renders the display.
- `platformio.ini`: board, dependencies, and build settings for the `cardputer` environment.
- `proxy/main.py`: Python 3.12 Cloud Functions entry point; `proxy/app/` contains authentication, metrics, dashboard assembly, and request handling.
- `proxy/tests/`: pytest suite and shared test doubles in `conftest.py`.
- `tasks/prd-cardputer-gpw-monitor.md`: requirements and acceptance criteria. `proxy/README.md` documents the API and deployment.
- `tools/wifi_audit/`: auxiliary Wi-Fi utilities and capture files.

## Build, Test, and Development Commands

Run from the repository root unless stated otherwise:

- `pio run -e cardputer`: compile firmware; configure the ignored `src/secrets.h` first with Wi-Fi credentials, proxy URL/token, and `USE_MOCK_DATA`.
- `pio run -e cardputer --target upload`: flash a connected device.
- `pio device monitor -b 115200`: inspect serial output.
- `make -C proxy install`: create the proxy virtual environment and install development dependencies using Python 3.12.
- `make -C proxy format`: format Python with Ruff.
- `make -C proxy check`: check formatting, lint, and run pytest.
- `make -C proxy run`: start the local HTTP service on port 8080. Configure GCP credentials and environment variables as described in `proxy/README.md`.

## Coding Style & Naming Conventions

Use four-space indentation. Match C++ conventions: `PascalCase` types, `camelCase` functions, `kName` constants, `g_name` globals, and function braces on separate lines. Python uses type hints, `snake_case` functions/modules, and `PascalCase` classes. Ruff targets Python 3.12 with an 88-character line limit and import sorting.

## Testing Guidelines

Name tests `test_*.py` with `test_*` functions. Reuse fake clients; tests require neither network access nor GCP credentials. Cover missing/stale metrics, authentication, caching, and upstream failures when changing those behaviors. No numeric coverage threshold is configured. For firmware changes, compile and exercise relevant display/connectivity scenarios on hardware; `USE_MOCK_DATA=1` enables mock scenarios.

## Commit & Pull Request Guidelines

Git history is unavailable in this checkout. Use concise, imperative commit subjects, optionally scoped (`proxy: handle stale metrics`). PRs should describe behavior changes, reference relevant issues or PRD requirements, report validation, and include device photos/screenshots for display changes.

## Security & Data Contracts

Keep monitoring read-only. Preserve `null` as unknown, epoch timestamps in seconds, and proxy-owned alarm decisions. Never commit or log tokens, Wi-Fi passwords, `src/secrets.h`, or service-account keys.
