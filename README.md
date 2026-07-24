# JARVIS Desktop 2.1

JARVIS is a local-first Windows command center for voice and text commands,
application and website launching, window management, system telemetry,
reminders, routines, persistent memory, and optional AI reasoning.

## What it can do

- Open safe built-in apps, Start Menu apps, user folders, websites, and searches.
- List, focus, minimize, maximize, restore, or confirm-close visible windows.
- Control media, volume, brightness, notifications, shortcuts, and screenshots.
- Find files, inspect processes, and confirm before terminating a process.
- Save local memories, notes, reminders, routines, and bounded conversation context.
- Read the latest visible message from one open app and stage it in another through
  a two-confirmation workflow; transferred text is never sent automatically.
- Run bounded public-web research in a visible isolated browser, close the research
  session, and save a sourced Word document under `Documents\JARVIS Research`.
- Schedule relative or clock-time reminders, snooze them, and retry notification
  delivery instead of silently losing failed alerts.
- Use OmniRoute, Groq, Cerebras, or free OpenRouter models with bounded failover.
- Show honest ready, degraded, and offline states without leaking provider details.

Typing, private app reads/transfers, clipboard reads, process termination,
window closing, PowerShell, and
power actions require an explicit one-time confirmation. PowerShell is disabled
by default.

## Download

The current unsigned Windows build is `site/JARVIS.exe`. Windows SmartScreen may
show a warning because this release is not code-signed.

## Run from source

Prerequisites: Windows 10/11 and Python 3.12 or newer.

```powershell
.\Setup-JARVIS-Desktop.ps1
.\Start-JARVIS-Desktop.ps1
```

## Configure intelligence

Copy `.env.example` to `%LOCALAPPDATA%\JARVIS\.env`. Add only provider keys you
own. JARVIS never needs keys in the repository or website.

For OmniRoute:

```dotenv
JARVIS_BASE_URL=http://127.0.0.1:20128/v1
JARVIS_MODEL=your-model-id
JARVIS_AUTOSTART_OMNIROUTE=true
```

If an existing local agent configuration already points to OmniRoute, JARVIS
keeps compatibility with it. Direct `JARVIS_*` settings take priority.

## Verify and build

```powershell
npm.cmd test
npm.cmd run build
npm.cmd run release
```

`npm run release` creates `dist-portable/JARVIS.exe` and copies the verified
artifact into `site/JARVIS.exe`.

## Repository map

- `jarvis/` — complete Python backend and guarded Windows controls.
- `static/` — desktop command-center interface.
- `tests/` — unit tests for routing, safety, providers, and persistence.
- `site/` — Vercel download site and current Windows artifact.
- `scripts/` — static-site and release automation.
- `desktop/` — compatibility overlay retained for older repaired builds.

Runtime data stays under `%LOCALAPPDATA%\JARVIS`. Crash details are written to
`%LOCALAPPDATA%\JARVIS\data\crash.log`.

## Security

Never commit populated `.env` files or provider credentials. See
[`SECURITY.md`](SECURITY.md) for the local threat model and reporting guidance.
