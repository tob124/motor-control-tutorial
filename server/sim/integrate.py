"""定步长数值积分工具。

课程作者：Connor He 和 Astra。

本模块只做一件事：把一个显式的一阶常微分方程组用四阶
Runge-Kutta 法推进一步。所有物理模型都写成
``f(t, state) -> dstate`` 的形式，由这里统一积分。

状态一律是定长浮点元组，方便做步长收敛测试。
"""
from __future__ import annotations

import math

__all__ = ['add', 'scale', 'combine', 'rk4_step', 'substep_plan', 'MAX_SUBSTEPS']

# 单次仿真允许的最大子步数，防止误传极小步长导致服务挂死。
MAX_SUBSTEPS = 4_000_000


def add(a, b):
    """逐元素相加。"""
    return tuple(x + y for x, y in zip(a, b))


def scale(a, k):
    """逐元素乘以标量。"""
    return tuple(x * k for x in a)


def combine(*pairs):
    """加权求和：combine((k1, s1), (k2, s2), ...) -> k1*s1 + k2*s2 + ..."""
    if not pairs:
        return ()
    total = scale(pairs[0][1], pairs[0][0])
    for k, s in pairs[1:]:
        total = add(total, scale(s, k))
    return total


def rk4_step(f, t, state, step):
    """经典四阶 Runge-Kutta 单步。

    ``f(t, state)`` 返回与 ``state`` 等长的导数元组。
    ``step`` 必须为正的有限数，调用方负责校验。
    """
    if not (step > 0.0) or not math.isfinite(step):
        raise ValueError('积分步长必须是正的有限数。')
    k1 = f(t, state)
    k2 = f(t + step / 2.0, add(state, scale(k1, step / 2.0)))
    k3 = f(t + step / 2.0, add(state, scale(k2, step / 2.0)))
    k4 = f(t + step, add(state, scale(k3, step)))
    slope = combine((1.0, k1), (2.0, k2), (2.0, k3), (1.0, k4))
    return add(state, scale(slope, step / 6.0))


def substep_plan(duration, control_period, substep):
    """把 ``[0, duration]`` 切成控制周期与子步都能整除的网格。

    返回 ``(n_control, n_sub, dt_control, dt_sub)``。

    物理模型按 ``dt_sub`` 积分、控制器按 ``dt_control`` 更新，
    这样"控制器采样周期"与"植物积分精度"就是两个独立可调的量，
    对应真实数字控制器的行为。
    """
    for name, value in (('duration', duration), ('control_period', control_period), ('substep', substep)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0.0:
            raise ValueError(f'{name} 必须是正的有限数。')
    if control_period < substep:
        raise ValueError('控制周期不能小于积分子步。')
    n_control = int(round(duration / control_period))
    if n_control < 1:
        raise ValueError('仿真时长至少需要一个控制周期。')
    ratio = control_period / substep
    n_sub = int(round(ratio))
    if n_sub < 1:
        raise ValueError('每个控制周期至少需要一个子步。')
    if n_sub > MAX_SUBSTEPS // n_control if n_control else True:
        raise ValueError('子步数过多，请增大积分子步或缩短仿真时长。')
    # 用实际取整后的步长回代，保证网格严格对齐、不漂移。
    return n_control, n_sub, duration / n_control, control_period / n_sub
