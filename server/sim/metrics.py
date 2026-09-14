"""曲线指标：超调、调节时间、稳态误差、跟踪误差、扫频带宽。

课程作者：Connor He 和 Astra。

这些函数只读取采样后的轨迹，不参与物理求解，因此可以
独立用构造数据测试。所有指标都返回 ``None`` 而不是瞎猜一个数，
前端会显示为"—"。
"""
from __future__ import annotations

import cmath
import math

__all__ = ['percent_overshoot', 'settling_time', 'steady_state_error', 'tracking_error',
           'rms', 'peak', 'following_error', 'first_order_response', 'sine_response']


def _finite(values):
    return [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]


def _final(values):
    tail = values[-max(1, len(values) // 20):]
    return sum(tail) / len(tail)


def percent_overshoot(values, target):
    """相对阶跃终值的最大超调百分比。

    参考值为 0 或非有限时不定义，返回 ``None``。
    """
    vals = _finite(values)
    if not vals or not math.isfinite(target) or abs(target) < 1e-12:
        return None
    if target > 0:
        peak = max(vals)
        over = peak - target
    else:
        peak = min(vals)
        over = target - peak
    if over <= 0:
        return 0.0
    return 100.0 * over / abs(target)


def settling_time(times, values, target, tolerance=0.02):
    """进入并保持在 ±tolerance 相对带内的时间；从未进入返回 ``None``。

    ``tolerance`` 是相对终值的比例（默认 2%）。
    """
    if not (tolerance > 0):
        raise ValueError('容差必须为正数。')
    if not math.isfinite(target) or abs(target) < 1e-12:
        return None
    band = abs(target) * tolerance
    last_out = None
    for t, v in zip(times, values):
        if not math.isfinite(v):
            return None
        if abs(v - target) > band:
            last_out = t
    if last_out is None:
        return times[0] if times else None
    if last_out >= times[-1]:
        return None
    return last_out


def steady_state_error(target, measured):
    """绝对稳态误差（取轨迹尾部平均）。"""
    vals = _finite(measured)
    if not vals or not math.isfinite(target):
        return None
    return target - _final(vals)


def tracking_error(times, reference, measured):
    """跟踪误差统计：RMS、最大绝对值、以及最大误差出现的时刻。"""
    pairs = [(t, r - m) for t, r, m in zip(times, reference, measured)
             if math.isfinite(t) and math.isfinite(r) and math.isfinite(m)]
    if not pairs:
        return {'rms': None, 'max_abs': None, 'max_at': None}
    errs = [e for _, e in pairs]
    worst = max(pairs, key=lambda p: abs(p[1]))
    return {'rms': math.sqrt(sum(e * e for e in errs) / len(errs)),
            'max_abs': abs(worst[1]), 'max_at': worst[0]}


def rms(values):
    vals = _finite(values)
    if not vals:
        return None
    return math.sqrt(sum(v * v for v in vals) / len(vals))


def peak(values):
    vals = _finite(values)
    if not vals:
        return None
    return max(abs(v) for v in vals)


def following_error(times, reference, measured, from_t=None, to_t=None):
    """限定时间窗内的平均绝对跟踪误差（用于路径跟踪的稳态段）。"""
    total = 0.0
    count = 0
    for t, r, m in zip(times, reference, measured):
        if from_t is not None and t < from_t:
            continue
        if to_t is not None and t > to_t:
            continue
        if math.isfinite(r) and math.isfinite(m):
            total += abs(r - m)
            count += 1
    if not count:
        return None
    return total / count


def first_order_response(gain, tau, t):
    """一阶惯性环节阶跃响应，用于理论对照。"""
    if not (tau > 0):
        raise ValueError('时间常数必须为正数。')
    return gain * (1.0 - math.exp(-t / tau))


def sine_response(gain, tau, omega):
    """一阶惯性环节在频率 ``omega`` 处的复数频率响应。

    ``gain`` 是直流增益，``tau`` 是时间常数（秒）。
    """
    if not (tau > 0):
        raise ValueError('时间常数必须为正数。')
    if not math.isfinite(omega) or omega < 0:
        raise ValueError('频率必须是非负有限数。')
    return gain / complex(1.0, omega * tau)


def bode_points(gain, tau, omegas):
    """给定频率列表，返回 ``[(omega, |H|, arg(H) 度)]``。"""
    points = []
    for w in omegas:
        h = sine_response(gain, tau, w)
        points.append((w, abs(h), math.degrees(cmath.phase(h))))
    return points
