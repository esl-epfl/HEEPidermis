"""The existing HEEPidermis palette and Tk styling, without device dependencies."""

from __future__ import annotations

from tkinter import font as tkfont, ttk

UI_BACKGROUND = "#071019"
UI_SURFACE = "#0e1b25"
UI_SURFACE_RAISED = "#132530"
UI_PLOT = "#09151e"
UI_BORDER = "#294552"
UI_BORDER_ACTIVE = "#3a6673"
UI_TEXT = "#e8f1f4"
UI_MUTED = "#91a9b4"
UI_ACCENT = "#55d3dd"
UI_ACCENT_DARK = "#1d5661"
UI_WARNING = "#f2bd62"
UI_DISABLED_BACKGROUND = "#0b171f"
UI_DISABLED_BORDER = "#1c313a"
UI_DISABLED_TEXT = "#526873"


def configure_style(root):
    root.configure(background=UI_BACKGROUND)
    for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name).configure(family="Helvetica")
    root.option_add("*TCombobox*Listbox.background", UI_PLOT)
    root.option_add("*TCombobox*Listbox.foreground", UI_TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", UI_ACCENT_DARK)
    root.option_add("*TButton.cursor", "hand2")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=UI_SURFACE, foreground=UI_TEXT, font=("Helvetica", 10))
    style.configure("TFrame", background=UI_SURFACE)
    style.configure("App.TFrame", background=UI_BACKGROUND)
    style.configure("Workspace.TFrame", background=UI_BACKGROUND)
    style.configure("Card.TFrame", background=UI_SURFACE_RAISED)
    style.configure("TLabel", background=UI_SURFACE, foreground=UI_TEXT)
    style.map("TLabel", foreground=[("disabled", UI_DISABLED_TEXT)], background=[("disabled", UI_SURFACE)])
    style.configure("Workspace.TLabel", background=UI_BACKGROUND, foreground=UI_TEXT)
    style.configure("PlotTitle.TLabel", background=UI_BACKGROUND, foreground=UI_TEXT, font=("Helvetica", 11, "bold"))
    style.configure("Muted.TLabel", foreground=UI_MUTED, font=("Helvetica", 8))
    style.configure("WorkspaceMuted.TLabel", background=UI_BACKGROUND, foreground=UI_MUTED, font=("Helvetica", 8))
    style.configure("TButton", padding=(12, 7), background=UI_SURFACE_RAISED, foreground=UI_TEXT,
                    bordercolor=UI_BORDER, focusthickness=1, focuscolor=UI_BORDER_ACTIVE)
    style.map("TButton", background=[("active", UI_ACCENT_DARK), ("pressed", "#17444d"),
                                     ("disabled", UI_DISABLED_BACKGROUND)],
              foreground=[("disabled", UI_DISABLED_TEXT)],
              bordercolor=[("disabled", UI_DISABLED_BORDER), ("active", UI_BORDER_ACTIVE)])
    style.configure("Accent.TButton", background=UI_ACCENT_DARK, foreground="#effdff",
                    bordercolor=UI_ACCENT, font=("Helvetica", 10, "bold"))
    style.map("Accent.TButton", background=[("active", "#27717d"), ("pressed", "#17444d"),
                                            ("disabled", UI_DISABLED_BACKGROUND)],
              foreground=[("disabled", UI_DISABLED_TEXT)], bordercolor=[("disabled", UI_DISABLED_BORDER)])
    style.configure("TEntry", padding=(7, 5), fieldbackground=UI_PLOT, foreground=UI_TEXT,
                    insertcolor=UI_TEXT, bordercolor=UI_BORDER)
    style.map("TEntry", fieldbackground=[("disabled", UI_DISABLED_BACKGROUND)],
              foreground=[("disabled", UI_DISABLED_TEXT)], bordercolor=[("disabled", UI_DISABLED_BORDER)])
    style.configure("TSpinbox", padding=(7, 5), fieldbackground=UI_PLOT, foreground=UI_TEXT,
                    arrowcolor=UI_ACCENT, bordercolor=UI_BORDER)
    style.map("TSpinbox", fieldbackground=[("disabled", UI_DISABLED_BACKGROUND)],
              foreground=[("disabled", UI_DISABLED_TEXT)], arrowcolor=[("disabled", UI_DISABLED_TEXT)],
              bordercolor=[("disabled", UI_DISABLED_BORDER)])
    style.configure("TCombobox", padding=(7, 5), fieldbackground=UI_PLOT, foreground=UI_TEXT,
                    arrowcolor=UI_ACCENT, bordercolor=UI_BORDER)
    style.map("TCombobox", fieldbackground=[("disabled", UI_DISABLED_BACKGROUND), ("readonly", UI_PLOT)],
              foreground=[("disabled", UI_DISABLED_TEXT), ("readonly", UI_TEXT)],
              arrowcolor=[("disabled", UI_DISABLED_TEXT)], bordercolor=[("disabled", UI_DISABLED_BORDER)])
    style.configure("TCheckbutton", background=UI_SURFACE, foreground=UI_TEXT,
                    indicatorbackground=UI_PLOT, indicatorforeground=UI_ACCENT)
    style.map("TCheckbutton", background=[("active", UI_SURFACE)], foreground=[("disabled", UI_DISABLED_TEXT)],
              indicatorbackground=[("disabled", UI_DISABLED_BACKGROUND), ("selected", UI_ACCENT_DARK)],
              indicatorforeground=[("disabled", UI_DISABLED_TEXT)])
    style.configure("TLabelframe", background=UI_SURFACE, bordercolor=UI_BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=UI_SURFACE, foreground=UI_ACCENT, font=("Helvetica", 9, "bold"))
    style.configure("Warning.TLabel", background=UI_SURFACE, foreground=UI_WARNING, font=("Helvetica", 9, "bold"))
    style.configure("TScrollbar", background=UI_SURFACE_RAISED, troughcolor=UI_PLOT,
                    bordercolor=UI_BACKGROUND, arrowcolor=UI_MUTED)
    return style
