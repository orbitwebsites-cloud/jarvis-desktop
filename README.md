# JARVIS Desktop

JARVIS is a local Windows command center for voice and text commands, app and
browser launching, system telemetry, reminders, routines, and optional AI
reasoning.

## Current release

`JARVIS-Fixed.exe` is the reliability-hardened Windows build. It adds:

- bounded provider retries and alternate-model failover;
- recovery from empty provider responses;
- optional local-model fallback;
- real online, degraded, and offline connection states;
- automatic health polling and reconnection;
- request timeouts and persistent crash logging;
- improved voice errors and common wake-word transcription handling.

The original executable is unsigned, so this repaired build is also unsigned.
Windows may show a SmartScreen warning.

## Architecture

JARVIS runs locally because its computer-control features require access to the
user's Windows session. Vercel hosts the release/download page only; it does not
execute the local-control backend.

The `desktop/` folder contains the reliability overlay and repaired dashboard
assets. The original build was supplied only as a PyInstaller executable, so
the overlay expects the recovered Python 3.12 bytecode during a rebuild.

## Build the release site

```powershell
npm.cmd run build
```

The static output is written to `dist/`.

## Security

Never commit provider API keys. Existing JARVIS provider keys are loaded from
the user's local JARVIS data directory at runtime.
