"""Windows desktop integration helpers (DPI awareness)."""

import ctypes

user32 = ctypes.windll.user32


def set_dpi_aware():
    """Set process DPI awareness: per-monitor when possible, else system aware.

    Must run before any window is created; safe to call multiple times
    (the first call wins process-wide). All modules must use the SAME mode —
    mixing system-aware and per-monitor-aware calls in one process gives
    wrong coordinates on mixed-DPI setups.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()  # system aware fallback
    except Exception:
        pass


set_dpi_aware()
