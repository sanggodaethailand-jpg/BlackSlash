"""The MQL5 compiler only runs on the owner's machine, so check the indicator's block
structure here: balanced braces, and no statements left at global scope (a stray "}"
once closed a function early and broke the compile)."""

import re
from pathlib import Path

MQ5 = Path(__file__).resolve().parents[1] / "mql5" / "Indicators" / "ANON_Levels_v2.mq5"


def code_lines(text):
    """Lines with comments, strings and char literals blanked out."""
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.S)
    out = []
    for line in text.splitlines():
        line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
        line = re.sub(r"'(?:\\.|[^'\\])*'", "''", line)
        out.append(line.split("//", 1)[0].rstrip())
    return out


def structure_problems(text):
    depth, previous, problems = 0, "", []
    for number, line in enumerate(code_lines(text), 1):
        if not line.strip():
            continue
        continuation = previous and previous[-1] not in ";{}"
        if depth == 0 and line[0].isspace() and not continuation and not line.lstrip().startswith(("{", "}")):
            problems.append(f"line {number}: statement at global scope: {line.strip()[:60]}")
        depth += line.count("{") - line.count("}")
        if depth < 0:
            problems.append(f"line {number}: more closing than opening braces")
            depth = 0
        previous = line.strip()
    if depth:
        problems.append(f"{depth} unclosed brace(s) at end of file")
    return problems


def test_indicator_has_sound_block_structure():
    assert structure_problems(MQ5.read_text(encoding="utf-8-sig")) == []


def test_the_check_catches_a_function_closed_early():
    broken = "void F()\n  {\n   if(x)\n     {\n      y();\n     }\n     }\n   z();\n  }\n"
    assert structure_problems(broken)
