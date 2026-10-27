import numpy as np
import pandas as pd
import pytest

from bot.copylab.biglab import exposure_proxy
from bot.copylab.skill import benjamini_hochberg, block_bootstrap_p, martingale_share, shrink


def fills(rows):
    """rows: (coin, start position, signed size, price)"""
    return pd.DataFrame([{"time": i, "coin": c, "startPosition": sp, "signed": sg, "px": px} for i, (c, sp, sg, px) in enumerate(rows)])


def test_bootstrap_separates_real_edge_from_noise():
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.01, 250)
    edge = noise + 0.004  # a steady +0.4%/day on top of the same noise
    assert block_bootstrap_p(edge) < 0.01
    assert block_bootstrap_p(noise - noise.mean()) > 0.3


def test_benjamini_hochberg_keeps_only_strong_results():
    p = pd.Series({"a": 0.001, "b": 0.004, "c": 0.03, "d": 0.4, "e": 0.9})
    passed = benjamini_hochberg(p, q=0.10)
    assert passed.to_dict() == {"a": True, "b": True, "c": True, "d": False, "e": False}
    assert not benjamini_hochberg(pd.Series({"x": 0.2, "y": 0.5}), q=0.10).any()


def test_shrinkage_pulls_noisy_estimates_harder():
    alpha = pd.Series({"precise": 0.01, "noisy": 0.01, "a": 0.0, "b": -0.002, "c": 0.001})
    se = pd.Series({"precise": 0.001, "noisy": 0.02, "a": 0.002, "b": 0.002, "c": 0.002})
    s = shrink(alpha, se)
    mean = alpha.mean()
    assert abs(s["noisy"] - mean) < abs(s["precise"] - mean)  # same raw alpha, the noisy one is trusted less
    assert s["precise"] > s["noisy"]


def test_martingale_share_detects_averaging_down():
    down = fills([("BTC", 0, 1, 100), ("BTC", 1, 1, 90), ("BTC", 2, 1, 80), ("BTC", 3, -3, 85)])  # buys more as it falls
    up = fills([("BTC", 0, 1, 100), ("BTC", 1, 1, 110), ("BTC", 2, 1, 120), ("BTC", 3, -3, 125)])   # adds only when winning
    assert martingale_share(down) == 1.0
    assert martingale_share(up) == 0.0


def test_exposure_proxy_is_gross_position_value():
    df = fills([("BTC", 0, 1, 100), ("ETH", 0, -10, 10), ("BTC", 1, -1, 100)])  # gross: 100, 200, 100
    assert exposure_proxy(df, 0, 10, q=1.0) == pytest.approx(200)
    assert exposure_proxy(df, 0, 10, q=0.0) == pytest.approx(100)
