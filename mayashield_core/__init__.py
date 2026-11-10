# -*- coding: utf-8 -*-
"""MayaShield engine.

Everything importable without Maya lives directly in this package (rules,
analyzer, parsers, scanner, cleaner, persistence, pool).  Modules that need a
running Maya (live, guard, integration, ui, command) import ``maya`` themselves
and are only imported from inside Maya.

Language level: must stay valid on Python 3.7 (Maya 2022) through 3.13
(Maya 2027), so no walrus, no positional-only arguments, no ``match``.
"""

__version__ = "4.1.1"
VENDOR = "4rest"
PLUGIN_NAME = "mayashield"
