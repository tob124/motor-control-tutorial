"""仿真请求的参数校验。

课程作者：Connor He 和 Astra。

原则（沿用姊妹项目 linux-interactive-tutorial 已验证的约定）：

* **白名单**：只接受登记过的字段，未知字段直接报错，绝不静默忽略。
* 数字必须是有限实数，拒绝 ``bool`` 与字符串；不接受 NaN / Infinity。
* 时长、子步、控制周期都有硬上限，防止把本机服务拖死。
* 越界参数要么报错（危险量），要么进 ``warnings``（教学上有意义的量）。
  绝不做"悄悄钳位后假装没发生"。

``validate`` 返回规范化后的配置字典，可以直接交给 ``scenario.run``。
"""
from __future__ import annotations

import math

__all__ = ['validate', 'DEFAULTS', 'LIMITS', 'KINDS', 'CONTROLLERS', 'BRIDGES', 'LOADS',
           'SENSORS', 'SCENARIOS', 'FAULTS', 'EPARAMS', 'param_key', 'describe_limits']

KINDS = ('dc', 'induction', 'pmsm_bldc')
BRIDGES = ('ideal', 'pwm', 'svpwm')
CONTROLLERS = ('open', 'current', 'speed', 'position', 'cascade', 'pure_pursuit')
LOADS = ('constant', 'step', 'ramp', 'fan', 'none')
SENSORS = ('ideal', 'quantized', 'biased', 'jitter', 'reversed')
SCENARIOS = ('open_loop', 'steady_state', 'speed', 'torque_speed', 'vf_scan', 'slip_scan',
             'current_loop', 'bode', 'position_step', 'path_follow', 'bus_can', 'fault_injection')
FAULTS = ('none', 'encoder_reversed', 'phase_swap', 'vbus_sag', 'can_dropout',
          'sensor_bias', 'mechanical_jam', 'units_confusion')

#: 每种电机能做的实验。做不了的组合在这里就报错，而不是跑到一半崩掉。
SCENARIOS_BY_KIND = {
    'dc': ('open_loop', 'steady_state', 'speed', 'torque_speed', 'current_loop', 'bode',
           'position_step', 'path_follow', 'bus_can', 'fault_injection'),
    'pmsm_bldc': ('open_loop', 'steady_state', 'speed', 'current_loop', 'bode',
                  'position_step', 'bus_can', 'fault_injection'),
    'induction': ('open_loop', 'steady_state', 'speed', 'torque_speed', 'vf_scan', 'slip_scan'),
}

#: 只考察稳态的场景：强制零负载，否则"稳态"就没有意义。
_STEADY_ONLY = ('steady_state', 'torque_speed', 'slip_scan', 'vf_scan', 'open_loop')

LIMITS = {
    'duration': (0.01, 10.0),
    'control_period': (0.00005, 0.01),
    'substep': (0.000005, 0.0005),
    'sample_period': (0.001, 0.1),
    'vdc': (5.0, 400.0),
    'pole_pairs': (1, 12),
    'encoder_lines': (8, 100000),
    'threshold': (0.0, 100000.0),
}

# 每个电机类型允许出现的参数键及其范围、(默认值, 单位, 中文说明)。
EPARAMS = {
    'dc': {
        'R': ((0.01, 100.0), (2.0, 'Ω', '电枢电阻')),
        'L': ((1e-5, 1.0), (0.002, 'H', '电枢电感')),
        'J': ((1e-8, 10.0), (2e-4, 'kg·m²', '转动惯量')),
        'b': ((0.0, 10.0), (1e-4, 'N·m·s/rad', '粘性阻尼')),
        'Kt': ((1e-4, 20.0), (0.05, 'N·m/A', '转矩常数')),
        'Ke': ((1e-4, 20.0), (0.05, 'V·s/rad', '反电势常数')),
    },
    'induction': {
        'Rs': ((0.01, 100.0), (0.5, 'Ω', '定子电阻')),
        'Rr': ((0.01, 100.0), (0.4, 'Ω', '折算后转子电阻')),
        'Ls': ((1e-5, 10.0), (0.012, 'H', '定子全电感')),
        'Lr': ((1e-5, 10.0), (0.012, 'H', '转子全电感')),
        'Lm': ((1e-5, 10.0), (0.0112, 'H', '互感')),
        'J': ((1e-8, 10.0), (5e-3, 'kg·m²', '转动惯量')),
        'b': ((0.0, 10.0), (1e-3, 'N·m·s/rad', '粘性阻尼')),
        'f': ((1.0, 400.0), (50.0, 'Hz', '额定频率')),
        'p': ((1, 12), (2, '', '极对数')),
        'i_max': ((0.1, 200.0), (8.5, 'A', '额定相电流峰值（仅用于超限告警）')),
    },
    'pmsm_bldc': {
        'Rs': ((0.01, 100.0), (0.4, 'Ω', '相电阻')),
        'Ld': ((1e-5, 10.0), (0.001, 'H', 'd 轴电感')),
        'Lq': ((1e-5, 10.0), (0.001, 'H', 'q 轴电感')),
        'Lm': ((1e-5, 10.0), (0.001, 'H', 'BLDC 相电感')),
        'lambda_m': ((1e-5, 1.0), (0.012, 'Wb', '永磁磁链幅值')),
        'J': ((1e-8, 10.0), (2e-4, 'kg·m²', '转动惯量')),
        'b': ((0.0, 10.0), (1e-4, 'N·m·s/rad', '粘性阻尼')),
        'p': ((1, 12), (4, '', '极对数')),
        'Kt': ((1e-4, 20.0), (0.0717, 'N·m/A', 'BLDC 转矩常数')),
        'Ke': ((1e-4, 20.0), (0.0717, 'V·s/rad', 'BLDC 反电势常数')),
    },
}

# 所有类型共用但不属于"植物参数"的键。
_GENERIC = ('kind', 'bridge', 'controller', 'load', 'sensor', 'scenario', 'faults',
            'duration', 'control_period', 'substep', 'sample_period', 'vdc',
            'params', 'target', 'load_value', 'ramp_rate', 'fault', 'notes',
            'path', 'gains', 'limits', 'sweep', 'bus', 'can',
            'scenario_params', 'robot_params')

_GAIN_KEYS = ('current_kp', 'current_ki', 'speed_kp', 'speed_ki', 'position_kp',
              'kspeed_kp', 'kspeed_ki', 'vq_kp', 'vq_ki', 'vd_kp', 'vd_ki', 'anti_windup',
              'ff_delay')

_LIMIT_KEYS = ('voltage', 'current_phase', 'target_rpm', 'iq_limit', 'current_band',
               'encoder_lines')

_PATH_KEYS = ('shape', 'length', 'radius', 'speed', 'lookahead')
_BUS_KEYS = ('resistance', 'voltage_droop_per_a', 'thermal_r', 'thermal_c', 'ambient_c', 'derate_c')
_CAN_KEYS = ('bitrate', 'frame_bits', 'period', 'payload_bits', 'nodes', 'dropout')

DEFAULTS = {
    'kind': 'dc',
    'bridge': 'ideal',
    'controller': 'speed',
    'load': 'step',
    'sensor': 'ideal',
    'scenario': 'open_loop',
    'faults': (),
    'duration': 5.0,
    'control_period': 0.001,
    'substep': 0.0001,
    'sample_period': 0.005,
    'vdc': 24.0,
    'target': None,
    'load_value': None,
    'ramp_rate': None,
    'fault': 'none',
}

_KIND_DEFAULT_DURATION = {'dc': 5.0, 'induction': 6.0, 'pmsm_bldc': 2.0}

_DEFAULT_CONTROLLER = {
    'dc': 'speed',
    'induction': 'speed',
    'pmsm_bldc': 'current',
}


def param_key(kind, name):
    return f'{kind}.{name}'


def describe_limits():
    """给前端"参数说明"面板用的人类可读范围表。"""
    out = {}
    for kind, params in EPARAMS.items():
        out[kind] = {name: {'min': r[0][0], 'max': r[0][1], 'default': r[1][0],
                            'unit': r[1][1], 'label': r[1][2]}
                     for name, r in params.items()}
    out['_common'] = {name: {'min': r[0], 'max': r[1]} for name, r in LIMITS.items()}
    return out


def _number(value, field, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} 必须是数字。')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{field} 必须是有限数，不接受 NaN 或 Infinity。')
    if low is not None and value < low:
        raise ValueError(f'{field} 不能小于 {low}。')
    if high is not None and value > high:
        raise ValueError(f'{field} 不能大于 {high}。')
    return value


def _integer(value, field, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} 必须是整数。')
    if float(value) != int(value):
        raise ValueError(f'{field} 必须是整数。')
    value = int(value)
    if low is not None and value < low:
        raise ValueError(f'{field} 不能小于 {low}。')
    if high is not None and value > high:
        raise ValueError(f'{field} 不能大于 {high}。')
    return value


def _choice(value, field, allowed):
    if not isinstance(value, str):
        raise ValueError(f'{field} 必须是字符串。')
    if value not in allowed:
        raise ValueError(f'{field} 只允许：{"、".join(allowed)}。')
    return value


def _subset(raw, field, allowed):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f'{field} 必须是对象。')
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ValueError(f'{field} 里有未登记字段：{"、".join(unknown)}。')
    return dict(raw)


def _bool(value, field):
    if not isinstance(value, bool):
        raise ValueError(f'{field} 必须是 true 或 false。')
    return value


def validate(raw):
    """校验并规范化一份仿真请求。非法输入抛 ``ValueError``（上层转 400）。"""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError('仿真请求必须是 JSON 对象。')
    unknown = sorted(set(raw) - set(_GENERIC))
    if unknown:
        raise ValueError(f'未登记字段：{"、".join(unknown)}。')

    cfg = dict(DEFAULTS)
    warnings = []

    if 'kind' in raw:
        cfg['kind'] = _choice(raw['kind'], 'kind', KINDS)
    kind = cfg['kind']

    if 'bridge' in raw:
        cfg['bridge'] = _choice(raw['bridge'], 'bridge', BRIDGES)
    if 'controller' in raw:
        cfg['controller'] = _choice(raw['controller'], 'controller', CONTROLLERS)
    else:
        cfg['controller'] = _DEFAULT_CONTROLLER[kind]
    if 'load' in raw:
        cfg['load'] = _choice(raw['load'], 'load', LOADS)
    if 'sensor' in raw:
        cfg['sensor'] = _choice(raw['sensor'], 'sensor', SENSORS)
    if 'scenario' in raw:
        cfg['scenario'] = _choice(raw['scenario'], 'scenario', SCENARIOS)
    if cfg['scenario'] not in SCENARIOS_BY_KIND[kind]:
        raise ValueError(
            f'{kind} 不支持 {cfg["scenario"]} 实验；可用的有：{"、".join(SCENARIOS_BY_KIND[kind])}。')
    if 'fault' in raw:
        cfg['fault'] = _choice(raw['fault'], 'fault', FAULTS)

    faults = raw.get('faults', ())
    if faults is None:
        faults = ()
    if not isinstance(faults, (list, tuple)):
        raise ValueError('faults 必须是数组。')
    clean = []
    for item in faults:
        if not isinstance(item, str):
            raise ValueError('faults 里只能放故障名称字符串。')
        clean.append(_choice(item, 'faults', FAULTS))
    if cfg['fault'] != 'none':
        if cfg['fault'] not in clean:
            clean.append(cfg['fault'])
        cfg['fault'] = 'none'
    cfg['faults'] = tuple(clean)

    # 时间量。默认时长按电机类型取，避免小时间常数的机器跑太久。
    if 'duration' in raw:
        cfg['duration'] = _number(raw['duration'], 'duration', *LIMITS['duration'])
    else:
        cfg['duration'] = _KIND_DEFAULT_DURATION[kind]
    if 'control_period' in raw:
        cfg['control_period'] = _number(raw['control_period'], 'control_period', *LIMITS['control_period'])
    if 'substep' in raw:
        cfg['substep'] = _number(raw['substep'], 'substep', *LIMITS['substep'])
    if 'sample_period' in raw:
        cfg['sample_period'] = _number(raw['sample_period'], 'sample_period', *LIMITS['sample_period'])
    if cfg['control_period'] < cfg['substep']:
        raise ValueError('control_period 不能小于 substep。')
    if cfg['sample_period'] < cfg['control_period']:
        warnings.append('输出采样周期小于控制周期，曲线不会被平滑，只是重复同一控制输出。')

    # 直流母线
    if 'vdc' in raw:
        cfg['vdc'] = _number(raw['vdc'], 'vdc', *LIMITS['vdc'])

    # 目标值
    if 'target' in raw and raw['target'] is not None:
        cfg['target'] = _number(raw['target'], 'target', -1e6, 1e6)
    if 'load_value' in raw and raw['load_value'] is not None:
        cfg['load_value'] = _number(raw['load_value'], 'load_value', -1e4, 1e4)
    if 'ramp_rate' in raw and raw['ramp_rate'] is not None:
        cfg['ramp_rate'] = _number(raw['ramp_rate'], 'ramp_rate', -1e5, 1e5)

    # 植物参数
    params = _subset(raw.get('params'), 'params', EPARAMS[kind])
    resolved = {}
    for name, (rng, meta) in EPARAMS[kind].items():
        default = meta[0]
        value = params.get(name, default)
        if name not in ('p',) and not isinstance(value, str):
            value = _number(value, f'params.{name}', rng[0], rng[1])
        elif name == 'p':
            value = _integer(value, f'params.{name}', rng[0], rng[1])
        else:
            raise ValueError(f'params.{name} 必须是数字。')
        resolved[name] = value
    if kind in ('dc', 'pmsm_bldc') and 'Kt' in resolved and 'Ke' in resolved:
        if abs(resolved['Kt'] - resolved['Ke']) > 0.5 * max(resolved['Kt'], resolved['Ke']):
            warnings.append('Kt 与 Ke 相差较大：公制单位下理想电机两者数值应接近，请确认数据手册口径。')
    if kind == 'induction':
        if resolved['Lm'] >= min(resolved['Ls'], resolved['Lr']):
            raise ValueError('互感 Lm 必须小于定子/转子全电感 Ls、Lr。')
    if kind == 'pmsm_bldc':
        if cfg['bridge'] == 'ideal' and cfg['controller'] in ('current', 'cascade'):
            warnings.append('电流环在理想电压源下没有母线约束，观察解耦与跟踪即可；要研究限幅请选 svpwm 或 pwm。')
    cfg['params'] = resolved

    # 控制器增益
    gains_raw = _subset(raw.get('gains'), 'gains', _GAIN_KEYS)
    gains = {}
    for name in _GAIN_KEYS:
        if name in gains_raw:
            if name == 'anti_windup':
                gains[name] = _bool(gains_raw[name], 'gains.anti_windup')
            elif name == 'ff_delay':
                gains[name] = _number(gains_raw[name], 'gains.ff_delay', 0.0, 0.05)
            else:
                gains[name] = _number(gains_raw[name], f'gains.{name}', 0.0, 1e5)
    gains.setdefault('anti_windup', True)
    cfg['gains'] = gains

    # 限幅
    lim_raw = _subset(raw.get('limits'), 'limits', _LIMIT_KEYS)
    limits = {}
    for name in _LIMIT_KEYS:
        if name in lim_raw:
            limits[name] = _number(lim_raw[name], f'limits.{name}', 0.0, 1e6)
    cfg['limits'] = limits

    # 路径
    path_raw = _subset(raw.get('path'), 'path', _PATH_KEYS)
    path = {}
    if path_raw.get('shape') is not None:
        path['shape'] = _choice(path_raw['shape'], 'path.shape', ('line', 'circle', 'rectangle', 'sine'))
    else:
        path['shape'] = 'circle'
    path['length'] = _number(path_raw.get('length', 6.0), 'path.length', 0.1, 200.0)
    path['radius'] = _number(path_raw.get('radius', 1.5), 'path.radius', 0.05, 100.0)
    path['speed'] = _number(path_raw.get('speed', 1.0), 'path.speed', 0.01, 20.0)
    path['lookahead'] = _number(path_raw.get('lookahead', 0.4), 'path.lookahead', 0.02, 5.0)
    cfg['path'] = path

    # 电源与总线
    bus = _subset(raw.get('bus'), 'bus', _BUS_KEYS)
    cfg['bus'] = {name: _number(bus.get(name, default), f'bus.{name}', 0.0, 1e4)
                  for name, default in (('resistance', 0.02), ('voltage_droop_per_a', 0.0),
                                        ('thermal_r', 2.0), ('thermal_c', 8.0),
                                        ('ambient_c', 25.0), ('derate_c', 90.0))}

    can = _subset(raw.get('can'), 'can', _CAN_KEYS)
    cfg['can'] = {
        'bitrate': _integer(can.get('bitrate', 1_000_000), 'can.bitrate', 10_000, 8_000_000),
        'frame_bits': _integer(can.get('frame_bits', 130), 'can.frame_bits', 40, 200),
        'period': _number(can.get('period', 0.002), 'can.period', 0.0001, 1.0),
        'payload_bits': _integer(can.get('payload_bits', 64), 'can.payload_bits', 0, 64),
        'nodes': _integer(can.get('nodes', 1), 'can.nodes', 1, 64),
        'dropout': _number(can.get('dropout', 0.0), 'can.dropout', 0.0, 1.0),
    }

    # 扫频
    sweep = _subset(raw.get('sweep'), 'sweep', ('start_hz', 'stop_hz', 'points'))
    cfg['sweep'] = {
        'start_hz': _number(sweep.get('start_hz', 1.0), 'sweep.start_hz', 0.01, 5000.0),
        'stop_hz': _number(sweep.get('stop_hz', 500.0), 'sweep.stop_hz', 0.02, 20000.0),
        'points': _integer(sweep.get('points', 24), 'sweep.points', 3, 200),
    }
    if cfg['sweep']['stop_hz'] <= cfg['sweep']['start_hz']:
        raise ValueError('sweep.stop_hz 必须大于 sweep.start_hz。')

    # 场景自定义参数（开环电压、V/f 斜率、前馈开关、六步模式等）
    sp = raw.get('scenario_params') or {}
    if not isinstance(sp, dict):
        raise ValueError('scenario_params 必须是对象。')
    allowed_sp = ('open_loop_voltage', 'vf_ratio', 'vf_boost', 'f_start_hz', 'f_stop_hz',
                  'vector', 'six_step', 'id_ref', 'warmup', 'reference_step',
                  'small_signal', 'omega', 'ratios', 'loads',
                  'crank_len', 'coupler_len', 'rocker_len', 'ground_len')
    unknown_sp = sorted(set(sp) - set(allowed_sp))
    if unknown_sp:
        raise ValueError(f'scenario_params 里有未登记字段：{"、".join(unknown_sp)}。')
    scenario_params = {}
    for key, value in sp.items():
        if key in ('vector', 'six_step'):
            scenario_params[key] = _bool(value, f'scenario_params.{key}')
        elif key == 'reference_step':
            scenario_params[key] = _number(value, 'scenario_params.reference_step', 0.0, 1.0)
        elif key in ('ratios', 'loads'):
            if not isinstance(value, list):
                raise ValueError(f'scenario_params.{key} 必须是数组。')
            if key == 'ratios':
                scenario_params[key] = [_number(item, f'scenario_params.{key}[]', 0.01, 2.0)
                                        for item in value]
            else:
                cleaned = []
                for index, item in enumerate(value):
                    if not isinstance(item, dict):
                        raise ValueError('scenario_params.loads 里每一项必须是对象。')
                    unknown_item = sorted(set(item) - {'name', 'current_a', 'duty'})
                    if unknown_item:
                        raise ValueError(f'负载项里有未登记字段：{"、".join(unknown_item)}。')
                    cleaned.append({
                        'name': str(item.get('name', f'负载 {index + 1}'))[:60],
                        'current_a': _number(item.get('current_a', 0.0),
                                             'scenario_params.loads[].current_a', 0.0, 1e4),
                        'duty': _number(item.get('duty', 1.0),
                                        'scenario_params.loads[].duty', 0.0, 1.0)})
                scenario_params[key] = cleaned
        else:
            scenario_params[key] = _number(value, f'scenario_params.{key}', -1e6, 1e6)
    cfg['scenario_params'] = scenario_params

    # 机器人参数：只有显式给出 robot_params 时才装配底盘（否则是单电机实验）
    rp = raw.get('robot_params')
    if rp is None:
        cfg['robot_params'] = None
    else:
        if not isinstance(rp, dict):
            raise ValueError('robot_params 必须是对象。')
        allowed_rp = ('track', 'wheel_radius', 'gear')
        unknown_rp = sorted(set(rp) - set(allowed_rp))
        if unknown_rp:
            raise ValueError(f'robot_params 里有未登记字段：{"、".join(unknown_rp)}。')
        cfg['robot_params'] = {
            'track': _number(rp.get('track', 0.4), 'robot_params.track', 0.05, 5.0),
            'wheel_radius': _number(rp.get('wheel_radius', 0.0625), 'robot_params.wheel_radius', 0.005, 1.0),
            'gear': _number(rp.get('gear', 1.0), 'robot_params.gear', 0.05, 200.0),
        }

    # 与场景相关的默认目标
    _apply_scenario_defaults(cfg, raw)

    # 教学提示：子步相对电气时间常数是否过粗
    tau = _electrical_tau(cfg)
    if tau is not None and cfg['substep'] > tau / 5.0:
        warnings.append(f'积分子步 {cfg["substep"] * 1e6:.0f} µs 相对电气时间常数 {tau * 1e3:.2f} ms 偏大，'
                        f'建议子步不大于 {tau / 5.0 * 1e6:.1f} µs。')
    cfg['warnings'] = tuple(warnings)
    return cfg


def _electrical_tau(cfg):
    p = cfg['params']
    kind = cfg['kind']
    try:
        if kind == 'dc':
            return p['L'] / p['R']
        if kind == 'induction':
            return p['Ls'] / p['Rs']
        return min(p['Ld'], p['Lq']) / p['Rs']
    except (KeyError, ZeroDivisionError):
        return None


def _apply_scenario_defaults(cfg, raw):
    """按场景补上合理的目标值，但绝不覆盖用户显式给的值。"""
    kind = cfg['kind']
    scenario = cfg['scenario']
    rps = 2.0 * math.pi
    defaults = {
        'open_loop': {'target': 0.0},
        'steady_state': {'target': 0.0},
        'torque_speed': {'target': 0.0},
        'vf_scan': {'target': 0.0},
        'slip_scan': {'target': 0.0},
        'current_loop': {'target': 2.0 if kind != 'dc' else 1.0},
        'speed': {'target': 100.0},
        'position_step': {'target': 0.5},
        'path_follow': {'target': 1.0},
        'bode': {'target': 0.0},
        'bus_can': {'target': 100.0},
        'fault_injection': {'target': 100.0},
    }.get(scenario, {'target': 0.0})
    if cfg['target'] is None:
        cfg['target'] = float(defaults.get('target', 0.0))
    if cfg['load_value'] is None:
        if cfg['load'] in ('constant', 'step'):
            cfg['load_value'] = 0.05 if kind in ('dc', 'pmsm_bldc') else 1.0
        else:
            cfg['load_value'] = 0.0
    if cfg['ramp_rate'] is None:
        cfg['ramp_rate'] = cfg['load_value'] / max(cfg['duration'] / 2.0, 1e-9)
    # 用户显式给出的增益优先：只用默认值补齐"没给的那几个"。
    # （早期版本无条件 update 默认值，会把用户调好的参数悄悄覆盖掉。）
    merged = dict(_default_gains(kind))
    merged.update(cfg['gains'])
    cfg['gains'] = merged
    # 只考察稳态的场景：零负载，否则稳态无从谈起（否则"加阶跃负载"会
    # 让学员看到一条永远不收敛的曲线，还以为模型坏了）。
    if cfg['scenario'] in _STEADY_ONLY:
        cfg['load'] = 'none'
        cfg['load_value'] = 0.0
        cfg['ramp_rate'] = 0.0


def _default_gains(kind):
    """默认整定起点（由数值搜索确定，tests/test_control.py 有回归断言）。

    内环（电流）与 1 ms 采样一起给出约 200 rad/s 的环路带宽；外环（速度）
    约 25 rad/s，两者相差约 8 倍，满足串级控制"内环比外环快 5–10 倍"的
    工程常识。电流环再快就会撞上采样延时的稳定边界——学员把增益往上调
    两次就能亲眼看到振荡，这正是第 4 周要教的现象。

    * 直流机：电流到角加速度 ``Kt/J = 250 (rad/s²)/A``，电流限幅 8 A；
      速度环比例 0.05 对应约 5 rad/s 带宽，比 2 s 的机械时间常数快一个量级。
    * 永磁机：电气时间常数 ``L/R = 2.5 ms``，电流环 ``Kp = 0.8``。
    * 异步机：转差频率环输出的是电角速度增量，量级不同，单独给。
    """
    if kind == 'dc':
        return {'current_kp': 0.4, 'current_ki': 1000.0,
                'speed_kp': 0.05, 'speed_ki': 0.15, 'position_kp': 8.0}
    if kind == 'induction':
        return {'speed_kp': 0.4, 'speed_ki': 2.0, 'kspeed_kp': 30.0, 'kspeed_ki': 400.0}
    return {'vq_kp': 0.8, 'vq_ki': 800.0, 'vd_kp': 0.8, 'vd_ki': 800.0,
            'speed_kp': 0.05, 'speed_ki': 1.2}
