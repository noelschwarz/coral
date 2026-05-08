# ADR-007: Daemon PID file location and signal handling

## Status

Accepted — Week 1 Track A (2026).

## Context

`coral start` writes a PID file so `coral stop` can locate and signal the daemon. Two locations were considered:

1. **`$CORAL_HOME/coral.pid`** — sits next to `vault.db` and `vault_meta.json`; one directory holds all per-user state.
2. **`/var/run/user/<uid>/coral.pid`** — Linux convention for per-user runtime state; cleaned on logout.

Constraints from the engineering spec:

- Cross-platform parity (macOS, Linux, Windows). `/var/run/user/<uid>` does not exist on macOS or Windows.
- Local-first, no system-wide state. The daemon must run without root or admin privileges.
- The CLI must locate the PID file given only `CORAL_HOME` (or its default).

## Decision

Use **`$CORAL_HOME/coral.pid`** on every platform. The path is exposed via `coral.paths.daemon_pid_path` and `Config.daemon_pid_file`.

## Consequences

- Identical behavior across platforms; one path to document and test.
- Stale PID files survive a reboot (they are not on tmpfs). `coral start` defends against this by checking liveness with `psutil.pid_exists` before refusing to launch (cli.py:`start`).
- Signals: `loop.add_signal_handler` on POSIX, `signal.signal` fallback on Windows where `add_signal_handler` does not support `SIGTERM` (daemon.py).
- Two concurrent `coral start` invocations can race the PID-file check. We accept the race for v1: the second invocation's port bind on `127.0.0.1:8765` fails fast with a clear error.

## When to revisit

- If users complain about stale PID files surviving reboots, switch the Linux path to `$XDG_RUNTIME_DIR/coral.pid` and keep `$CORAL_HOME/coral.pid` as a macOS/Windows fallback.
- If we ever support a system-installed daemon (out of scope for v1), revisit and use platform-native service managers instead of a PID file.
