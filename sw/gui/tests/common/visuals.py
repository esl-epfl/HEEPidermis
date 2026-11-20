"""Shared Stroop/MIST verdict symbols, using the test family's palette."""

GREEN = "#40c060"
RED = "#e04040"


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
