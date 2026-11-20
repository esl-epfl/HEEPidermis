"""Small shared Tk widgets; no acquisition, analysis or firmware imports."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import UI_SURFACE, UI_SURFACE_RAISED, UI_TEXT


class Collapsible(ttk.Frame):
    """Disclosure section with a title button and a separately filled body.

    The body is gridded by callers into ``self.body``; toggling only packs and
    unpacks that frame, so widget ownership and callbacks never change.
    """

    def __init__(self, master: tk.Misc, title: str, opened: bool = True):
        super().__init__(master)
        self.title = title
        self.opened = opened
        self.button = ttk.Button(self, command=self.toggle)
        self.button.pack(fill="x")
        self.body = ttk.Frame(self, padding=(8, 4))
        self.set_open(opened)

    def set_open(self, opened: bool) -> None:
        self.opened = bool(opened)
        self.button.configure(text=("▾  " if self.opened else "▸  ") + self.title)
        if self.opened:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()

    def toggle(self) -> None:
        self.set_open(not self.opened)


class ScrollPane(ttk.Frame):
    """Scrollable container (vertical always, horizontal when needed).

    Content is gridded into ``self.body``. The content is never cropped: when
    it is wider than the viewport a horizontal scrollbar appears, and the inner
    window otherwise stretches to fill the canvas width.
    """

    def __init__(self, master: tk.Misc, width: int = 500):
        super().__init__(master)
        self.canvas = tk.Canvas(self, width=width, highlightthickness=0,
                                background=UI_SURFACE)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self._xview_changed)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.hbar.grid_remove()
        self.body = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._track, add="+")
        self.canvas.bind("<Configure>", self._track, add="+")
        # Wheel scrolls the pane the pointer is over; Shift+wheel scrolls it
        # horizontally. Widget bindings (plots, comboboxes) keep precedence.
        self.bind_all("<Button-4>", self._wheel, add="+")
        self.bind_all("<Button-5>", self._wheel, add="+")
        self.bind_all("<MouseWheel>", self._wheel, add="+")
        self.bind_all("<Shift-Button-4>", self._hwheel, add="+")
        self.bind_all("<Shift-Button-5>", self._hwheel, add="+")
        self.bind_all("<Shift-MouseWheel>", self._hwheel, add="+")

    def _contains(self, event) -> bool:
        widget = getattr(event, "widget", None)
        while widget is not None:
            if widget is self.canvas or widget is self.body:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _wheel(self, event):
        if not self._contains(event):
            return
        if event.num == 4 or getattr(event, "delta", 0) > 0:
            self.canvas.yview_scroll(-3, "units")
        elif event.num == 5 or getattr(event, "delta", 0) < 0:
            self.canvas.yview_scroll(3, "units")

    def _hwheel(self, event):
        if not self._contains(event):
            return
        if event.num == 4 or getattr(event, "delta", 0) > 0:
            self.canvas.xview_scroll(-3, "units")
        elif event.num == 5 or getattr(event, "delta", 0) < 0:
            self.canvas.xview_scroll(3, "units")

    def _xview_changed(self, first, last):
        self.hbar.set(first, last)
        # Only when the content is wider than the viewport.
        (self.hbar.grid if float(last) - float(first) < 0.999 else self.hbar.grid_remove)()

    def _track(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        required = self.body.winfo_reqwidth()
        if required:
            # Grow beyond the canvas instead of squeezing/cropping the content.
            self.canvas.itemconfigure(self.window,
                                      width=max(self.canvas.winfo_width(), required))

    def scroll_to(self, widget: tk.Misc) -> None:
        """Scroll the pane so that ``widget`` (a descendant of body) is visible."""
        try:
            top = widget.winfo_rooty() - self.body.winfo_rooty()
            height = widget.winfo_height()
        except tk.TclError:
            return
        self.canvas.yview_moveto(max(0.0, (top - 40) / max(1, self.body.winfo_height())))
        _ = height


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
