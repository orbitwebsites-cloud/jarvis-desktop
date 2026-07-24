# Changelog

## 2.1.1

- Fixed the cross-app transfer timing out while an app was still launching: the
  destination is now opened and settled before the paste confirmation, so the
  confirmed action never has to launch an app and wait on it mid-open.
- Made window discovery and focus resilient to a slow-launching app, and reused
  a single UI Automation desktop handle across scans.
- Added a Windows CI build that rebuilds the downloadable JARVIS.exe from source.

## 2.0.0

- Added the complete backend source and reproducible Windows release pipeline.
- Added persistent bounded conversation context and a true new-conversation reset.
- Added direct OmniRoute configuration and optional local gateway autostart.
- Added bounded provider deadlines, failover, cooldowns, response normalization,
  active-model telemetry, and latency reporting.
- Added visible-window discovery and focus switching.
- Fixed relative user-folder resolution and the File Explorer routing collision.
- Added single-instance protection and persistent crash logging.
- Added command history, draft recovery, keyboard shortcuts, and richer status.
- Expanded the automated suite to 28 tests.
- Rebuilt the download site for the 2.0 release.
