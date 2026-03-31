"""Launch microphone testing UI."""

import asyncio
import sys

from app.ui import launch_ui


def _patch_windows_connection_reset_noise() -> None:
    if sys.platform == "win32":
        try:
            from asyncio import proactor_events

            original = proactor_events._ProactorBasePipeTransport._call_connection_lost

            def _safe_call_connection_lost(self, exc):
                try:
                    return original(self, exc)
                except OSError as err:
                    if getattr(err, "winerror", None) == 10054:
                        return None
                    raise

            proactor_events._ProactorBasePipeTransport._call_connection_lost = (  # type: ignore[attr-defined]
                _safe_call_connection_lost
            )
        except Exception:
            # Best-effort patch: if internals change, keep default behavior.
            pass


if __name__ == "__main__":
    _patch_windows_connection_reset_noise()
    launch_ui()
