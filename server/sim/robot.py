"""整机级模型：电源母线、CAN 总线负载与安全链。

课程作者：Connor He 和 Astra。

第 6 周（通信与实时性）和第 9 周（整机系统设计）用到。
全部是纯函数/小状态机，不依赖仿真内核。
"""
from __future__ import annotations

import math

__all__ = ['BusModel', 'CanBus', 'SafetyChain', 'power_budget', 'thermal_derate']

_CAN_OVERHEAD_BITS = 47  # 标准帧仲裁+控制+CRC+ACK+EOF+IFS 的近似开销


class BusModel:
    """电池—母线—驱动的一阶模型。

    包含：内阻压降、线损、一阶热模型与降额。
    真实机器人"空载正常、一动就复位"通常就是这里的压降问题。
    """

    def __init__(self, nominal_voltage=24.0, resistance=0.02,
                 thermal_r=2.0, thermal_c=8.0, ambient_c=25.0, derate_c=90.0):
        if nominal_voltage <= 0:
            raise ValueError('母线电压必须为正。')
        if resistance < 0:
            raise ValueError('内阻不能为负。')
        if thermal_r <= 0 or thermal_c <= 0:
            raise ValueError('热阻与热容必须为正。')
        self.nominal_voltage = float(nominal_voltage)
        self.resistance = float(resistance)
        self.thermal_r = float(thermal_r)
        self.thermal_c = float(thermal_c)
        self.ambient_c = float(ambient_c)
        self.derate_c = float(derate_c)
        self.temperature_c = float(ambient_c)

    def terminal_voltage(self, current):
        """端电压 = 空载电压 − 内阻·电流。"""
        return self.nominal_voltage - self.resistance * abs(current)

    def step_temperature(self, current, dt):
        """一阶热模型：``C·dT/dt = I²·R − (T − T_amb)/R_th``。"""
        if dt <= 0:
            raise ValueError('时间步必须为正。')
        loss = current * current * self.resistance
        cooling = (self.temperature_c - self.ambient_c) / self.thermal_r
        self.temperature_c += (loss - cooling) * dt / self.thermal_c
        return self.temperature_c

    def derate_factor(self):
        """温度降额系数（1.0 → 0.0），用于限制可用电流。"""
        if self.temperature_c <= self.derate_c * 0.6:
            return 1.0
        span = self.derate_c - self.derate_c * 0.6
        if span <= 0:
            return 1.0
        factor = 1.0 - (self.temperature_c - self.derate_c * 0.6) / span
        return max(0.0, min(1.0, factor))

    def snapshot(self, current):
        return {'terminal_voltage': self.terminal_voltage(current),
                'sag': self.nominal_voltage - self.terminal_voltage(current),
                'temperature_c': self.temperature_c,
                'derate_factor': self.derate_factor()}


class CanBus:
    """CAN 总线负载与报文抖动估算（标准帧，11 位标识符）。

    位填充、仲裁与错误帧的精确影响不在这里建模；这里给的是
    工程上够用的上界估算：``负载率 = Σ(每帧位数 × 频率) / 波特率``。
    """

    def __init__(self, bitrate=1_000_000, nodes=1):
        if bitrate <= 0:
            raise ValueError('波特率必须为正。')
        if nodes < 1:
            raise ValueError('节点数至少为 1。')
        self.bitrate = int(bitrate)
        self.nodes = int(nodes)

    def frame_bits(self, payload_bits=64):
        """一帧占用的总位数（含帧开销与位填充上界约 20%）。"""
        if payload_bits < 0:
            raise ValueError('有效位数不能为负。')
        raw = _CAN_OVERHEAD_BITS + payload_bits
        return int(math.ceil(raw * 1.2))

    def load(self, messages):
        """``messages`` 是 ``[{'bits': ..., 'period': ...}]``，返回负载率。"""
        total = 0.0
        for index, message in enumerate(messages):
            period = message.get('period')
            bits = message.get('bits')
            if not isinstance(period, (int, float)) or period <= 0:
                raise ValueError(f'第 {index + 1} 条报文的周期必须为正数。')
            if not isinstance(bits, (int, float)) or bits < 0:
                raise ValueError(f'第 {index + 1} 条报文的位数不能为负。')
            total += bits / period
        return total / self.bitrate

    def jitter_us(self, load_ratio):
        """粗略估计高负载下的排队抖动（µs），用于"为什么控制会抖"的教学。"""
        if not (0.0 <= load_ratio <= 1.0):
            raise ValueError('负载率必须在 0 到 1 之间。')
        if load_ratio >= 1.0:
            return math.inf
        # 用 M/D/1 排队思想的简化式：抖动随 1/(1−ρ) 增长
        worst_frame_us = (self.frame_bits(64)) / self.bitrate * 1e6
        return worst_frame_us * (1.0 / max(1e-3, 1.0 - load_ratio) - 1.0) / 2.0

    def budget(self, messages):
        ratio = self.load(messages)
        return {'load_ratio': ratio, 'load_percent': ratio * 100.0,
                'jitter_us': self.jitter_us(min(ratio, 1.0)),
                'worst_frame_us': self.frame_bits(64) / self.bitrate * 1e6,
                'verdict': _load_verdict(ratio)}


def _load_verdict(ratio):
    if ratio > 1.0:
        return '不可行：总线已过载，报文会大量延迟与丢失。'
    if ratio > 0.8:
        return '危险：超过 80% 负载率，最高优先级报文之外的延迟难以保证。'
    if ratio > 0.5:
        return '偏紧：需要确认最坏情况下的整帧延迟是否满足控制周期。'
    return '健康：留有足够的仲裁余量。'


class SafetyChain:
    """安全链状态机：急停 → 断电 → 抱闸 → 就绪。

    这是 ROBOCON 现场最容易出事、也最容易被新队员忽略的一环。
    任何时刻只要急停按下或看门狗超时，输出必须立刻回安全态。
    """

    STATES = ('init', 'ready', 'running', 'stopping', 'fault', 'estop')

    def __init__(self, watchdog_ms=100.0):
        if watchdog_ms <= 0:
            raise ValueError('看门狗时间必须为正。')
        self.watchdog_ms = float(watchdog_ms)
        self.state = 'init'
        self.last_heartbeat_ms = 0.0
        self.events = []

    def _log(self, time_ms, event):
        self.events.append({'t_ms': round(time_ms, 3), 'event': event, 'state': self.state})

    def heartbeat(self, time_ms):
        self.last_heartbeat_ms = float(time_ms)
        if self.state == 'init':
            self.state = 'ready'
            self._log(time_ms, '自检通过，进入就绪态')
        return self.state

    def start(self, time_ms, conditions_ok=True):
        if not conditions_ok:
            self.state = 'fault'
            self._log(time_ms, '启动条件不满足，进入故障态')
            return self.state
        if self.state not in ('ready', 'stopping'):
            self.state = 'fault'
            self._log(time_ms, f'从 {self.state} 态非法启动')
            return self.state
        self.state = 'running'
        self._log(time_ms, '使能输出')
        return self.state

    def emergency_stop(self, time_ms):
        self.state = 'estop'
        self._log(time_ms, '急停：输出立即断开')
        return self.state

    def release(self, time_ms):
        if self.state != 'estop':
            raise ValueError('只有在急停状态下才能复位。')
        self.state = 'init'
        self._log(time_ms, '急停复位，需要重新自检')
        return self.state

    def stop(self, time_ms):
        if self.state == 'running':
            self.state = 'stopping'
            self._log(time_ms, '正常停机')
            self.state = 'ready'
            self._log(time_ms, '停机完成')
        return self.state

    def check_watchdog(self, time_ms):
        """返回 ``True`` 表示看门狗触发（已进入故障态）。"""
        if self.state in ('running', 'ready') and time_ms - self.last_heartbeat_ms > self.watchdog_ms:
            self.state = 'fault'
            self._log(time_ms, f'看门狗超时（> {self.watchdog_ms:.0f} ms）')
            return True
        return False

    def outputs_enabled(self):
        return self.state == 'running'


def power_budget(loads, margin=0.2):
    """电源预算：``loads`` 是 ``[{'name':..., 'current_a':..., 'duty':...}]``。

    返回峰值/平均电流与按裕度推荐的电源容量，用于第 9 周的整机设计。
    """
    if margin < 0:
        raise ValueError('裕度不能为负。')
    peak = 0.0
    average = 0.0
    detail = []
    for load in loads:
        name = load.get('name', '未命名负载')
        current = load.get('current_a')
        duty = load.get('duty', 1.0)
        if not isinstance(current, (int, float)) or current < 0:
            raise ValueError(f'{name} 的电流必须是非负数。')
        if not isinstance(duty, (int, float)) or not (0.0 <= duty <= 1.0):
            raise ValueError(f'{name} 的占空比必须在 0 到 1 之间。')
        peak += float(current)
        average += float(current) * float(duty)
        detail.append({'name': name, 'current_a': float(current), 'duty': float(duty),
                       'average_a': float(current) * float(duty)})
    return {'peak_current_a': peak, 'average_current_a': average,
            'recommended_a': peak * (1.0 + margin), 'margin': margin, 'detail': detail}


def thermal_derate(temperature_c, derate_c=90.0, knee=0.6):
    """温度降额系数：低于拐点不降额，到 ``derate_c`` 降为零。"""
    if not (0.0 < knee < 1.0):
        raise ValueError('拐点比例必须在 (0, 1) 内。')
    start = derate_c * knee
    if temperature_c <= start:
        return 1.0
    if temperature_c >= derate_c:
        return 0.0
    return 1.0 - (temperature_c - start) / (derate_c - start)
