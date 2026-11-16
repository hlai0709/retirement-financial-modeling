"""Native file dialogs for the pywebview shell."""

from __future__ import annotations

import webview


def pick_open_file(*, title: str = "Select file", file_types: tuple[str, ...] = ("All files (*.*)",)) -> str | None:
    """
    Show an open-file panel.

    pywebview's Cocoa backend already marshals ``create_file_dialog`` onto the
    AppKit main thread (callAfter + semaphore). An extra main-thread wrapper
    nests two hops and deadlocks the UI.
    """
    paths = pick_open_files(title=title, file_types=file_types, allow_multiple=False)
    return paths[0] if paths else None


def pick_open_files(
    *,
    title: str = "Select file(s)",
    file_types: tuple[str, ...] = ("All files (*.*)",),
    allow_multiple: bool = True,
) -> list[str]:
    """Show an open-file panel; return zero or more selected paths."""
    if not webview.windows:
        return []
    result = webview.windows[0].create_file_dialog(
        webview.OPEN_DIALOG,
        allow_multiple=allow_multiple,
        file_types=file_types,
    )
    if not result:
        return []
    if isinstance(result, (list, tuple)):
        return [str(p) for p in result if p]
    return [str(result)]


def pick_save_file(
    *,
    title: str = "Save file",
    file_types: tuple[str, ...] = ("All files (*.*)",),
    save_filename: str = "",
) -> str | None:
    if not webview.windows:
        return None
    kwargs: dict = {
        "allow_multiple": False,
        "file_types": file_types,
    }
    if save_filename:
        kwargs["save_filename"] = save_filename
    result = webview.windows[0].create_file_dialog(webview.SAVE_DIALOG, **kwargs)
    if not result:
        return None
    path = result[0] if isinstance(result, (list, tuple)) else result
    return str(path)