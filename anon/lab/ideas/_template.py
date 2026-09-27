"""Template: copy to anon/lab/ideas/<name>.py, rename the class and fill every field.

This example (buy a new 20-day high, trail under the 10-day low) only shows the API;
it is not registered because the file name starts with "_".
"""

from __future__ import annotations

from anon.lab.core import Entry, Idea
from anon.lab.frames import resample


class TemplateBreakout(Idea):
    name = "template_breakout"
    family = "trend"
    hypothesis = (
        "Who pays: late sellers who fade a fresh multi-week high. Why it could work: BTC trends "
        "persist for weeks. Why it could fail: most breakouts on BTC fail and the trail gives back gains."
    )
    primary = {"entry_days": 20, "exit_days": 10}
    grid = {"entry_days": [15, 20, 30], "exit_days": [5, 10, 15]}

    def prepare(self, bars, p):
        days, last_closed = resample(bars, 24)  # D1 from H1, each day visible only after it closed
        return days, last_closed

    def entry(self, i, bars, state, p):
        days, last_closed = state
        d = last_closed[i]
        if d < p["entry_days"] or bars[i].close_time.hour != 0:  # decide once a day, right after the daily close
            return None
        window = days[d - p["entry_days"] : d]
        if days[d].close > max(x.high for x in window):
            stop = min(x.low for x in days[d - p["exit_days"] + 1 : d + 1])
            return Entry("buy", stop)
        return None

    def update(self, i, bars, state, p, trade):
        days, last_closed = state
        d = last_closed[i]
        if d < p["exit_days"] or bars[i].close_time.hour != 0:
            return None
        return min(x.low for x in days[d - p["exit_days"] + 1 : d + 1])  # the trail only ever tightens


IDEA = TemplateBreakout()
