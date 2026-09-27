from anon.config import GhostConfig, LevelsConfig
from anon.ghost import Ghost, reprice, reward_risk
from tests.helpers import flat, then

LV = LevelsConfig()
G = Ghost(LV, GhostConfig())


def test_inside_gray_is_no_chase():
    bars = flat(84700, 5)
    res = G.evaluate(bars)
    assert res.signal is None
    assert res.reason == "inside_gray_no_chase"


def test_a_zone_touch_calls_buy_with_numeric_stop():
    bars = then(flat(84000, 5), [(83900, 83950, 83300, 83420)])
    sig = G.evaluate(bars).signal
    assert sig is not None and sig.setup == "A" and sig.variant == "zone"
    assert sig.stop == 83000 - 50  # zone bottom minus buffer
    assert sig.tp1 == 85500 and sig.outside_gray


def test_a_sweep_and_reclaim_puts_stop_below_the_sweep_low():
    bars = then(flat(84000, 5), [(83400, 83450, 82800, 83100)])
    sig = G.evaluate(bars).signal
    assert sig.variant == "sweep_reclaim"
    assert sig.stop == 82800 - 50  # a stop at 83000 would have been swept


def test_a_not_called_when_close_is_below_zone():
    bars = then(flat(84000, 5), [(83400, 83450, 82800, 82900)])
    assert G.evaluate(bars).signal is None


def test_b_needs_breakout_then_retest_and_uses_swing_stop():
    base = flat(84700, 6)  # parked in gray
    bars = then(base, [(84700, 84990, 84690, 84950)])  # breakout close > 84850
    assert G.evaluate(bars).signal is None  # breakout bar itself is not the retest
    bars = then(bars, [(84950, 84960, 84830, 84880)])  # retest, close back above gray
    sig = G.evaluate(bars).signal
    assert sig is not None and sig.setup == "B"
    swing_low = min(b.low for b in bars[-6:])
    assert sig.stop == swing_low - 50
    assert sig.stop != 84400  # never pinned to the reference line


def test_b_disarmed_after_close_back_inside_gray():
    bars = then(flat(84700, 6), [(84700, 84990, 84690, 84950), (84950, 84960, 84700, 84760)])
    bars = then(bars, [(84760, 84900, 84840, 84890)])
    assert G.evaluate(bars).signal is None


def test_b_rejected_when_reward_risk_below_min():
    ghost = Ghost(LV, GhostConfig(min_rr=3.0))
    bars = then(flat(84700, 6), [(84700, 84990, 84690, 84950), (84950, 84960, 84830, 84880)])
    res = ghost.evaluate(bars)
    assert res.signal is None and "below_min" in res.reason


def test_reward_risk_and_reprice():
    assert reward_risk(83500, 83000, 85500) == 4.0
    assert reward_risk(83000, 83000, 85500) == 0.0
    bars = then(flat(84000, 5), [(83900, 83950, 83300, 83420)])
    sig = reprice(G.evaluate(bars).signal, 83440)
    assert sig.entry == 83440
    assert sig.rr == reward_risk(83440, sig.stop, sig.tp1)
