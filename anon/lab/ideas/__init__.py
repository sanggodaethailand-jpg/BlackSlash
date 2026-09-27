"""Registered ideas: every module here (not starting with "_") exposes ``IDEA``.

Add one file per idea; copy ``_template.py``. Never edit an idea after running it: an edit
is a new attempt (new hash) and raises the bar for every idea (see AGENTS.md)."""

from __future__ import annotations

import importlib
import pkgutil

from anon.lab.core import Idea, validate_idea


def load_ideas() -> dict[str, Idea]:
    ideas: dict[str, Idea] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        idea = importlib.import_module(f"{__name__}.{info.name}").IDEA
        validate_idea(idea)
        if idea.name in ideas:
            raise ValueError(f"duplicate idea name {idea.name}")
        ideas[idea.name] = idea
    return ideas
