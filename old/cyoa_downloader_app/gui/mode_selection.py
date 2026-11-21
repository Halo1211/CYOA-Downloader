"""Keep sidebar hover local; reserve selection and settings work for clicks."""

from tkinter import TclError


class ModeView:
    def __init__(self, gui):
        self.gui = gui
        self.styles = {}

    def paint(self, value, *, hovered=False):
        widgets = self.gui._mode_btns.get(value)
        if widgets is None:
            return
        row, bar, icon, name, description = widgets
        palette = self.gui._p()
        selected = value == self.gui._mode_var
        background = palette["sel_row"] if selected else palette["surface"] if hovered else "transparent"
        targets = (
            (row, {"fg_color": background}),
            (bar, {"fg_color": palette["sel_bar"] if selected else "transparent"}),
            (icon, {"fg_color": palette["sel_icon"] if selected else background,
                    "text_color": palette["sel_nm"] if selected else palette["muted"]}),
            (name.master, {"fg_color": background}),
            (name, {"fg_color": background, "text_color": palette["sel_nm"] if selected else palette["fg"]}),
            (description, {"fg_color": background,
                           "text_color": palette["sel_desc"] if selected else palette["muted"]}),
        )
        for index, (widget, options) in enumerate(targets):
            key = (value, index)
            old = self.styles.get(key)
            if old is not None and old[0] is widget and old[1] == options:
                continue
            try:
                widget.configure(**options)
            except (AttributeError, RuntimeError, TclError):
                self.styles.pop(key, None)
            else:
                self.styles[key] = (widget, options)

    def select(self):
        for value in self.gui._mode_btns:
            self.paint(value)


def view(gui):
    current = getattr(gui, "_mode_view", None)
    if current is None:
        current = gui._mode_view = ModeView(gui)
    return current
