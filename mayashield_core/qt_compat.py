# -*- coding: utf-8 -*-
"""One import surface for PySide6 (Maya 2025+) and PySide2 (Maya 2022-2024)."""
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    import shiboken6 as shiboken
    QT_API = "PySide6"
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    import shiboken2 as shiboken
    QT_API = "PySide2"


def exec_dialog(dialog):
    """Run a modal dialog: exec_ is deprecated in PySide6, exec is new to it."""
    runner = getattr(dialog, "exec", None) or dialog.exec_
    return runner()


def exec_menu(menu, position):
    runner = getattr(menu, "exec", None) or menu.exec_
    return runner(position)


def wrap_pointer(pointer, base):
    return shiboken.wrapInstance(int(pointer), base)


def is_alive(widget):
    return widget is not None and shiboken.isValid(widget)


__all__ = ["QtCore", "QtGui", "QtWidgets", "QT_API", "exec_dialog", "exec_menu", "wrap_pointer", "is_alive"]
