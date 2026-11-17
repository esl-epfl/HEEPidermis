"""Small shared Tk widgets; no acquisition, analysis or firmware imports."""

from __future__ import annotations

import tkinter as tk

from .theme import UI_SURFACE_RAISED, UI_TEXT


class Tooltip:
    """Show compact contextual help after the pointer rests for one second."""

    def __init__(self, widget: tk.Misc, text: str):
        self.widget = widget
        self.text = text
        self.job = None
        self.window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self.job = self.widget.after(1000, self._show)

    def _cancel(self):
        if self.job is not None:
            self.widget.after_cancel(self.job)
            self.job = None

    def _show(self):
        self.job = None
        if self.window is not None or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(self.window, text=self.text, justify="left", wraplength=360,
                 background=UI_SURFACE_RAISED, foreground=UI_TEXT, padx=12, pady=9,
                 relief="solid", borderwidth=1, font=("Helvetica", 9)).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self.window is not None:
            self.window.destroy()
            self.window = None
