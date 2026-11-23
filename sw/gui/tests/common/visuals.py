"""Shared Stroop/MIST verdict symbols, using the test family's palette."""

GREEN = "#40c060"
RED = "#e04040"


def lighten(color, amount=.10):
    """A lighter variant of a ``#rrggbb`` color (instruction backdrops)."""
    value = color.lstrip("#")
    red, green, blue = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda channel: min(255, round(channel + (255 - channel) * amount))
    return f"#{mix(red):02x}{mix(green):02x}{mix(blue):02x}"


def draw_info_icon(canvas, cx, cy, radius, color="#f2f2f2"):
    """The big (i) that marks an instructions screen at a glance."""
    canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                       outline=color, width=max(2, int(radius / 9)))
    try:
        from tkinter import font as tkfont
        family = tkfont.nametofont("TkDefaultFont").actual("family") or "Helvetica"
    except Exception:
        family = "Helvetica"
    canvas.create_text(cx, cy, text="i", fill=color,
                       font=(family, max(14, int(radius * 1.25)), "bold italic"))


def draw_instructions_panel(canvas, w, h, background):
    """Lighter backdrop + (i) icon shared by every test's instructions.

    Drawn first, so the test's own instruction text renders on top of it.
    """
    canvas.create_rectangle(w * .07, h * .09, w * .93, h * .91,
                            fill=lighten(background, .07),
                            outline=lighten(background, .16), width=2)
    draw_info_icon(canvas, w / 2, h * .16, min(w, h) * .032)


def draw_verdict(canvas, cx, cy, arm, correct):
    if correct:
        canvas.create_line(cx - arm, cy, cx - arm * 0.25, cy + arm * 0.75,
                           cx + arm * 1.1, cy - arm * 0.9, fill=GREEN,
                           width=max(6, int(arm / 5)), capstyle="round", joinstyle="round")
        draw_smiley(canvas, cx, cy + arm * 2.6, arm * 0.85)
    else:
        for side in (-1, 1):
            canvas.create_line(cx - arm, cy - side * arm, cx + arm, cy + side * arm,
                               fill=RED, width=max(8, int(arm / 4)), capstyle="round")


def draw_smiley(canvas, cx, cy, radius):
    canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                       fill="#f0c020", outline="#7a5c00", width=2)
    eye, offset = radius * 0.13, radius * 0.42
    for side in (-1, 1):
        canvas.create_oval(cx + side * offset - eye, cy - radius * 0.25 - eye,
                           cx + side * offset + eye, cy - radius * 0.25 + eye,
                           fill="#101014", outline="")
    mouth = radius * 0.62
    canvas.create_arc(cx - mouth, cy - mouth * 0.45, cx + mouth, cy + mouth * 0.95,
                      start=200, extent=140, style="arc", outline="#101014",
                      width=max(3, int(radius / 10)))
