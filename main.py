# main.py - Entry point
from __future__ import annotations

import argparse
import sys
import traceback
import tkinter as tk
from pathlib import Path


def _log_launch_error(exc: BaseException) -> None:
    log_dir = Path.home() / "Library" / "Application Support" / "RetirementPlanner"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "launch_error.log"
    log_path.write_text(traceback.format_exc(), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retirement Planner")
    parser.add_argument(
        "--web",
        action="store_true",
        help="Launch Liquid Glass web UI (matches HTML mockups)",
    )
    parser.add_argument(
        "--tk",
        action="store_true",
        help="Launch legacy Tkinter UI (default)",
    )
    args = parser.parse_args()
    use_web = args.web or (__import__("os").environ.get("RETIREMENT_PLANNER_WEB") == "1")
    if getattr(sys, "frozen", False) and not args.tk:
        use_web = True

    try:
        if use_web and not args.tk:
            from gui_web import run_web_app

            run_web_app()
        else:
            from gui import RetirementGUI
            from gui.design_system import install_theme

            root = tk.Tk()
            install_theme(root)
            app = RetirementGUI(root)
            root.mainloop()
    except Exception as exc:
        _log_launch_error(exc)
        if getattr(sys, "frozen", False):
            import tkinter.messagebox as mb

            mb.showerror(
                "Retirement Planner",
                f"The app could not start.\n\n{exc}\n\nDetails saved to:\n"
                "~/Library/Application Support/RetirementPlanner/launch_error.log",
            )
        raise