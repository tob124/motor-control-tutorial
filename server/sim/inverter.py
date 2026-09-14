"""两电平电压源逆变器：载波 PWM 与空间矢量 PWM（SVPWM）。

课程作者：Connor He 和 Astra。

教学要点：

* 线性调制区上界是 ``Vdc/√3``（相电压幅值），也就是 ``Vdc/√3`` 的
  空间矢量长度。超过这个值进入过调制，输出的基波不再与指令成比例。
* 七段式 SVPWM 的等效实现就是**零序注入**：``v*_x = v_x − (max+min)/2``，
  它的线性区比正弦 PWM 宽 15.5%（``Vdc/√3`` 对 ``Vdc/2``）。
* 仿真里我们按"电压参考 → 母线限幅 → 饱和后的实际电压"处理，
  不做 100 kHz 级的开关级仿真；开关序列单独用纯函数算出来给学员看。

所有函数都是纯函数，方便单元测试。
"""
from __future__ import annotations

import math

__all__ = ['clamp_vector', 'svpwm_duties', 'svpwm_sector', 'sine_pwm_duties',
           'dq_to_alphabeta', 'alphabeta_to_dq', 'abc_to_alphabeta', 'alphabeta_to_abc',
           'carrier_pwm', 'linear_limit']

SQRT3 = math.sqrt(3.0)


def linear_limit(vdc):
    """线性调制区内的最大相电压幅值（V）。"""
    if not (vdc > 0):
        raise ValueError('母线电压必须为正。')
    return vdc / SQRT3


def sine_limit(vdc):
    """正弦 PWM 的线性区上限（V）。"""
    if not (vdc > 0):
        raise ValueError('母线电压必须为正。')
    return vdc / 2.0


def clamp_vector(v_alpha, v_beta, vdc, mode='svpwm'):
    """把 dq/αβ 电压指令按母线约束限幅，返回 ``(v_a, v_b, mod_index, limited)``。

    ``mode='svpwm'`` 用 ``Vdc/√3``，``mode='pwm'`` 用 ``Vdc/2``，
    ``mode='ideal'`` 不做任何限幅（理想电压源，教学对照用）。
    """
    if mode == 'ideal':
        return v_alpha, v_beta, math.hypot(v_alpha, v_beta) / 1.0, False
    limit = linear_limit(vdc) if mode == 'svpwm' else sine_limit(vdc)
    magnitude = math.hypot(v_alpha, v_beta)
    if magnitude <= limit or magnitude < 1e-12:
        return v_alpha, v_beta, magnitude / limit, False
    k = limit / magnitude
    return v_alpha * k, v_beta * k, 1.0, True


def dq_to_alphabeta(d, q, theta):
    """旋转坐标系 → 静止坐标系（Park 反变换）。

    采用幅值不变约定（与 ``abc_to_alphabeta`` 的 2/3 系数配套）：
    ``x_α = d·cosθ − q·sinθ``，``x_β = d·sinθ + q·cosθ``。
    """
    c, s = math.cos(theta), math.sin(theta)
    return d * c - q * s, d * s + q * c


def alphabeta_to_dq(alpha, beta, theta):
    """静止坐标系 → 旋转坐标系（Park 变换）。"""
    c, s = math.cos(theta), math.sin(theta)
    return alpha * c + beta * s, -alpha * s + beta * c


def abc_to_alphabeta(a, b, c_in):
    """幅值不变 Clarke 变换。"""
    alpha = (2.0 * a - b - c_in) / 3.0
    beta = (b - c_in) / SQRT3
    return alpha, beta


def alphabeta_to_abc(alpha, beta):
    """幅值不变 Clarke 反变换。"""
    a = alpha
    b = -0.5 * alpha + SQRT3 / 2.0 * beta
    c_in = -0.5 * alpha - SQRT3 / 2.0 * beta
    return a, b, c_in


def carrier_pwm(v_alpha, v_beta, vdc):
    """正弦（载波比较）PWM 的三相占空比，归一化到 ``[0, 1]``。

    按"有效矢量"给法：占空比以 0.5 为中心，线电压由占空比之差决定。
    """
    if not (vdc > 0):
        raise ValueError('母线电压必须为正。')
    a, b, c_in = alphabeta_to_abc(v_alpha, v_beta)
    return tuple(_duty(x, vdc) for x in (a, b, c_in))


def _duty(v, vdc):
    d = 0.5 + v / vdc
    return min(1.0, max(0.0, d))


def svpwm_duties(v_alpha, v_beta, vdc):
    """七段式 SVPWM 占空比：等效于零序（min-max）注入。

    返回 ``(d_a, d_b, d_c)``，已归一化到 ``[0, 1]``。
    超过线性区时按母线极限饱和，返回的占空比仍在合法范围内。
    """
    if not (vdc > 0):
        raise ValueError('母线电压必须为正。')
    v_alpha, v_beta, _, _ = clamp_vector(v_alpha, v_beta, vdc, 'svpwm')
    a, b, c_in = alphabeta_to_abc(v_alpha, v_beta)
    offset = (max(a, b, c_in) + min(a, b, c_in)) / 2.0
    return tuple(min(1.0, max(0.0, 0.5 + (x - offset) / vdc)) for x in (a, b, c_in))


def svpwm_sector(v_alpha, v_beta):
    """判断参考矢量所在扇区（1–6）；零矢量返回 0。"""
    if abs(v_alpha) < 1e-12 and abs(v_beta) < 1e-12:
        return 0
    angle = math.atan2(v_beta, v_alpha) % (2.0 * math.pi)
    return int(angle // (math.pi / 3.0)) + 1


def active_vector(k):
    """第 k 个有效开关状态（``k = 1..6``）在 αβ 平面上的电压矢量。

    约定：``V1 = (100)`` 对应 ``(2·Vdc/3, 0)``，逆时针每 60° 一个。
    这里返回**单位母线电压下的系数**，实际矢量等于该系数乘 ``vdc``。
    """
    if not 1 <= k <= 6:
        raise ValueError('有效矢量编号必须是 1–6。')
    angle = (k - 1) * math.pi / 3.0
    amplitude = 2.0 / 3.0
    return amplitude * math.cos(angle), amplitude * math.sin(angle)


def svpwm_dwell(v_alpha, v_beta, vdc):
    """把参考矢量严格分解成两个相邻有效矢量的时间占比。

    直接解 αβ 平面上的 2×2 伏秒平衡方程（不做任何三角恒等式变形）：

        T1·V_k + T2·V_{k+1} = T·V_ref

    返回 ``(sector, t1_ratio, t2_ratio)``，两个比值之和不超过 1，
    其余时间是零矢量。

    **比例的正确性由 tests/test_machines.py 的伏秒平衡测试卡住**：
    用这两个占比乘上真实的开关矢量重建，必须精确还原输入指令。
    相位角在哪个扇区，就用 ``svpwm_sector`` 定义的同一个扇区编号。
    """
    if not (vdc > 0):
        raise ValueError('母线电压必须为正。')
    sector = svpwm_sector(v_alpha, v_beta)
    if sector == 0:
        return 0, 0.0, 0.0
    a1, b1 = active_vector(sector)
    a2, b2 = active_vector(sector % 6 + 1)
    det = a1 * b2 - a2 * b1          # active_vector 已含 1/Vdc 的系数
    if abs(det) < 1e-18:
        raise ValueError('两个有效矢量共线，无法分解参考矢量。')
    # 解 [a1 a2; b1 b2]·[r1; r2] = [v_alpha; v_beta] / vdc
    ua, ub = v_alpha / vdc, v_beta / vdc
    t1_full = (ua * b2 - a2 * ub) / det
    t2_full = (a1 * ub - ua * b1) / det
    # 超出线性区时按比例回缩（等价于母线削顶）
    total = t1_full + t2_full
    if total > 1.0:
        scale = 1.0 / total
        t1_full *= scale
        t2_full *= scale
    return sector, max(0.0, min(1.0, t1_full)), max(0.0, min(1.0, t2_full))


def svpwm_vector_times(v_alpha, v_beta, vdc, period):
    """返回七段式对称开关序列的 ``[(矢量, 持续时间)]``。

    矢量用字符串表示：``'V0'``、``'V7'`` 是零矢量，``'V1'``–``'V6'``
    是六个有效矢量（V1 = (100)，按逆时针排列）。
    时间单位为与 ``period`` 相同的任意单位，序列总长等于 ``period``。
    """
    if not (period > 0):
        raise ValueError('开关周期必须为正。')
    sector, r1, r2 = svpwm_dwell(v_alpha, v_beta, vdc)
    if sector == 0:
        return [('V0', period / 2.0), ('V7', period / 2.0)]
    t1 = period * r1
    t2 = period * r2
    t0 = period - t1 - t2
    return [(f'V{sector}', t1), (f'V{sector % 6 + 1}', t2),
            ('V7', t0 / 2.0), ('V0', t0 / 2.0)]


def carrier_pwm_waveform(v_alpha, v_beta, vdc, cycles=2, points=600):
    """生成"参考波 vs 三角载波"的对照波形，供前端画图。

    返回 ``(t, carrier, duty_a)`` 三条归一化序列，横轴是载波周期数。
    """
    if not (vdc > 0):
        raise ValueError('母线电压必须为正。')
    if cycles <= 0 or points < 2:
        raise ValueError('cycles 必须为正，points 至少为 2。')
    d_a, d_b, d_c = svpwm_duties(v_alpha, v_beta, vdc)
    carrier, ref = [], []
    for index in range(points):
        x = cycles * index / (points - 1)
        frac = x % 1.0
        carrier.append(2.0 * (2.0 * abs(frac - 0.5)) - 1.0)
        ref.append(2.0 * d_a - 1.0)
    return [i * cycles / (points - 1) for i in range(points)], carrier, ref
