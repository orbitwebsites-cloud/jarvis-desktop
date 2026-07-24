# JARVIS security

JARVIS binds its local HTTP service to `127.0.0.1`, rejects untrusted Host and
Origin values for state-changing requests, and never serves provider keys.

Actions that can expose private data or cause meaningful side effects require a
short-lived, single-use confirmation token. PowerShell is additionally disabled
until the user enables it in Settings.

Provider credentials are loaded from the process environment or
`%LOCALAPPDATA%\JARVIS\.env`. Do not add credentials to source files, releases,
screenshots, bug reports, or the hosted download site.

The downloadable EXE is currently unsigned. Verify the SHA-256 shown in the
release notes when distributing it outside the official download page.

For a private security report, contact the repository owner without opening a
public issue containing credentials or personal data.
