"""Compile the indicator's Ghost/Ω functions as C++ and check they agree with the
Python engine bar by bar. Skipped when no C++ compiler is installed."""

import random
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from anon.config import Config
from anon.ghost import Ghost
from anon.omega import Omega
from anon.synthetic import waypoint_bars

MQ5 = Path(__file__).resolve().parents[1] / "mql5" / "Indicators" / "ANON_Levels_v2.mq5"
CXX = shutil.which("g++") or shutil.which("clang++")

HEADER = r"""
#include <cmath>
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>
typedef std::string string;
typedef long long datetime;
struct MqlRates { datetime time; double open, high, low, close; };
static double MathMin(double a, double b) { return a < b ? a : b; }
static double MathMax(double a, double b) { return a > b ? a : b; }
static double MathAbs(double a) { return std::fabs(a); }
static string DoubleToString(double v, int d) { char b[64]; std::snprintf(b, sizeof b, "%.*f", d, v); return b; }
static string IntegerToString(long long v) { return std::to_string(v); }
static int StringFind(const string& s, const char* t) { auto p = s.find(t); return p == string::npos ? -1 : (int)p; }
static string StringSubstr(const string& s, int a, int n = -1) { return n < 0 ? s.substr(a) : s.substr(a, n); }
static int StringLen(const string& s) { return (int)s.size(); }
"""

MAIN = r"""
#include <vector>
int main() {
  std::vector<MqlRates> r; string line;
  while (std::getline(std::cin, line)) {
    std::istringstream ss(line); MqlRates b; char c;
    ss >> b.time >> c >> b.open >> c >> b.high >> c >> b.low >> c >> b.close; r.push_back(b);
  }
  int n = (int)r.size();
  for (int i = 0; i < n; i++) {
    GhostCall g; GhostEvaluate(r.data(), i, g);
    std::printf("G %d %d %.6f %.6f %s\n", i, g.setup, g.stop, g.rr, g.reason.c_str());
  }
  for (int k = 1; k <= n; k++) {
    int from = k > WINDOW_BARS ? k - WINDOW_BARS : 0;
    RegimeInfo rg; RuleTag(r.data() + from, k - from, rg);
    std::printf("R %d %s %d\n", k - 1, rg.regime.c_str(), (int)rg.favorable);
  }
}
"""


def extract_cpp() -> str:
    src = MQ5.read_text(encoding="utf-8-sig")
    consts = [
        f"const {'long long' if typ == 'long' else typ} {name} = {value.strip()};"
        for typ, name, value in re.findall(r"^input\s+(double|int|long|bool)\s+(\w+)\s*=\s*([^;]+);", src, re.M)
    ]
    window = re.search(r"#define WINDOW_BARS \d+", src).group(0)
    body = src[src.index("struct GhostCall") : src.index("//| data refresh")]
    body = body[: body.rindex("//+")]
    globals_start = body.index("double     BufEmaFast")
    body = body[:globals_start] + body[body.index("//+", globals_start) :]
    body = re.sub(r"const MqlRates &(\w+)\[\]", r"const MqlRates *\1", body)
    body = re.sub(r"^int\s+LineHeight\(\).*$", "", body, flags=re.M)
    return HEADER + window + "\n" + "\n".join(consts) + "\n" + body + MAIN


@pytest.fixture(scope="module")
def mql_logic(tmp_path_factory):
    if CXX is None:
        pytest.skip("no C++ compiler")
    d = tmp_path_factory.mktemp("mql5")
    (d / "logic.cpp").write_text(extract_cpp(), encoding="utf-8")
    subprocess.run([CXX, "-std=c++17", "-O1", "-o", str(d / "logic"), str(d / "logic.cpp")], check=True)
    return d / "logic"


def test_indicator_ghost_and_omega_match_engine(mql_logic):
    cfg = Config()
    ghost, omega = Ghost(cfg.levels, cfg.ghost), Omega(cfg.levels, cfg.omega)
    rng = random.Random(42)
    outcomes: set[str] = set()
    regimes: set[str] = set()
    scenarios = []
    for trial in range(60):
        wps = [rng.uniform(82500, 86200) for _ in range(rng.randint(3, 40))]
        scenarios.append(waypoint_bars(wps, bars_per_leg=rng.randint(2, 9), noise=rng.uniform(5, 400), seed=trial))
    calm = waypoint_bars([84000, 84100], bars_per_leg=80, noise=20, seed=99)
    last = calm[-1]
    scenarios.append(calm + [replace(last, time=last.close_time, high=last.close + 1500, low=last.close - 1500)])
    for trial, bars in enumerate(scenarios):
        feed = "\n".join(f"{int(b.time.timestamp())},{b.open!r},{b.high!r},{b.low!r},{b.close!r}" for b in bars)
        out = subprocess.run([str(mql_logic)], input=feed, capture_output=True, text=True, check=True).stdout
        for line in out.splitlines():
            parts = line.split(" ")
            if parts[0] == "G":
                i, setup, stop, rr, reason = int(parts[1]), int(parts[2]), float(parts[3]), float(parts[4]), parts[5]
                res = ghost.evaluate(bars[: i + 1])
                assert res.reason == reason, (trial, i)
                if res.signal:
                    assert setup == (1 if res.signal.setup == "A" else 2)
                    assert res.signal.stop == pytest.approx(stop) and res.signal.rr == pytest.approx(rr)
                else:
                    assert setup == 0
                outcomes.add(reason.split(":")[0])
            else:
                k, regime, fav = int(parts[1]), parts[2], parts[3] == "1"
                tag = omega.rule_tag(bars[max(0, k - 299) : k + 1])
                assert (tag.regime, tag.favorable) == (regime, fav), (trial, k)
                regimes.add(regime)
    assert {"call_A_zone", "call_A_sweep_reclaim", "call_B_breakout_retest", "inside_gray_no_chase"} <= outcomes
    assert {"trend_up", "trend_down", "range", "shock"} <= regimes
