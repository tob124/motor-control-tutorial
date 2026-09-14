"""曲线类实验：扫出一条曲线，而不是跑一条时间轨迹。

课程作者：Connor He 和 Astra。

有些教学图本来就是"一族曲线"而不是"一次运行"，例如：

* 直流机的机械特性（不同电压下的转矩—转速直线族）；
* 异步机的转矩—转差曲线与不同电压下的对照；
* 恒压频比（V/f）控制下同步转速与最大转矩的变化；
* 电流环频率响应（Bode 图）；
* CAN 总线负载率与报文周期、节点数的关系。

这些都在这里用**和主仿真同一套模型**算出来，不退化成手写近似公式，
所以曲线和轨迹永远自洽。
"""
from __future__ import annotations

import math

from . import engine
from . import inverter
from . import metrics as mt
from . import robot as rb
from .machines import make_machine

__all__ = ['curve', 'CURVES']

RPM_PER_RAD_S = 60.0 / (2.0 * math.pi)
CURVES = ('dc_mechanical', 'dc_speed_response', 'induction_torque_slip', 'vf_family',
          'current_loop_bode', 'can_budget', 'encoder_resolution', 'four_bar_motion',
          'svpwm_spectrum', 'power_budget')


def curve(cfg, name):
    if name not in CURVES:
        raise ValueError(f'未知曲线实验：{name}。可用：{"、".join(CURVES)}。')
    return globals()[f'_{name}'](cfg)


def _dc_mechanical(cfg):
    """直流机机械特性：一族"电压 → 转矩—转速"直线。

    用解析稳态（``Kt·i = b·ω + τL``、``u = R·i + Ke·ω``）算，
    并对每个工作点用一次短仿真核对，确保解析式和动态模型一致。
    """
    params = cfg['params']
    machine = make_machine('dc', params)
    voltages = _voltages(cfg, default=(0.25, 0.5, 0.75, 1.0))
    vdc = cfg['vdc']
    families = []
    checks = []
    for fraction in voltages:
        voltage = fraction * vdc
        points = []
        for index in range(41):
            load = (-0.2 + 0.4 * index / 40.0) * abs(params['Kt']) * 40.0
            current, omega = machine.steady_state(voltage, load)
            points.append({'load_nm': round(load, 6),
                           'torque_nm': round(params['Kt'] * current, 6),
                           'speed_rpm': round(omega * RPM_PER_RAD_S, 4),
                           'current_a': round(current, 6)})
        families.append({'voltage_v': round(voltage, 4),
                         'ratio': round(fraction, 4), 'points': points})
        # 独立核对：让动态模型跑到稳态，与解析值比较
        check_cfg = dict(cfg)
        check_cfg.update({'kind': 'dc', 'scenario': 'steady_state', 'controller': 'open',
                          'duration': 3.0, 'load': 'none', 'substep': 0.0001})
        check_cfg['scenario_params'] = {'open_loop_voltage': voltage}
        check_cfg['params'] = dict(params)
        simulated = engine.simulate(check_cfg)
        final = simulated['series'][-1] if simulated['series'] else {}
        analytic_current, analytic_omega = machine.steady_state(voltage, 0.0)
        checks.append({'voltage_v': round(voltage, 4),
                       'analytic_rpm': round(analytic_omega * RPM_PER_RAD_S, 3),
                       'simulated_rpm': round(final.get('rpm', 0.0), 3),
                       'analytic_current_a': round(analytic_current, 5),
                       'simulated_current_a': round(final.get('current', 0.0), 5),
                       'note': '动态仿真的终值应与解析稳态一致；不一致说明模型或积分有问题。'})
    return {'kind': 'family', 'x_label': '负载转矩 N·m', 'y_label': '转速 rpm',
            'families': families, 'checks': checks,
            'notes': ['直流机的机械特性是一条直线：斜率由电枢电阻 R 决定，'
                      '截距（理想空载转速）由 Ke 与电压决定。',
                      '把电压降到额定值以下，直线整体下移——这就是"调压调速"的物理图像。']}


def _dc_speed_response(cfg):
    """同一台电机在不同速度环增益下的阶跃响应族。

    这正是第 5 周"整定"要看的图：太小则慢，太大则振荡。
    """
    base = dict(cfg)
    base.update({'kind': 'dc', 'scenario': 'speed', 'controller': 'speed',
                 'duration': min(5.0, cfg['duration'])})
    gains = [(0.01, 0.05), (0.03, 0.1), (0.05, 0.15), (0.1, 0.3), (0.2, 0.6)]
    if cfg.get('gains', {}).get('speed_kp'):
        gains = [(cfg['gains']['speed_kp'], cfg['gains'].get('speed_ki', 0.15))]
    families = []
    for kp, ki in gains:
        run = dict(base)
        run['gains'] = {'speed_kp': kp, 'speed_ki': ki,
                        'current_kp': cfg['gains'].get('current_kp', 0.4),
                        'current_ki': cfg['gains'].get('current_ki', 1000.0)}
        result = engine.simulate(run)
        series = result['series']
        families.append({
            'label': f'Kp={kp:g} Ki={ki:g}',
            'gains': {'speed_kp': kp, 'speed_ki': ki},
            'points': [{'t': row['t'], 'rpm': row['rpm'], 'current_a': row['current'],
                        'voltage_v': row['voltage']} for row in series],
            'metrics': {'overshoot_percent': result['metrics'].get('overshoot_percent'),
                        'settling_time_s': result['metrics'].get('settling_time_s'),
                        'peak_current_a': result['metrics'].get('peak_current_a'),
                        'diverged': result['metrics'].get('diverged')}})
    return {'kind': 'traces', 'x_label': '时间 s', 'y_label': '转速 rpm',
            'families': families,
            'notes': ['Kp 决定"反应有多快"，Ki 决定"最终能不能消除静差"。',
                      'Kp 调太大、或 Ki 相对 Kp 太强，都会出现上冲与持续振荡——'
                      '因为 1 ms 的采样周期给出了相位裕度上限。']}


def _induction_torque_slip(cfg):
    """异步机转矩—转差曲线族：不同端电压下的对照。"""
    params = cfg['params']
    machine = make_machine('induction', params)
    fractions = _voltages(cfg, default=(0.5, 0.75, 1.0))
    families = []
    for fraction in fractions:
        rms = fraction * inverter.linear_limit(cfg['vdc']) / math.sqrt(2.0)
        raw = machine.torque_slip_curve(rms, points=160, slip_max=1.0)
        families.append({
            'label': f'{fraction * 100:.0f}% 电压（相电压有效值 {rms:.2f} V）',
            'voltage_rms': round(rms, 4),
            'points': [{'slip': round(s, 6), 'torque_nm': round(torque, 6),
                        'speed_rpm': round(omega * RPM_PER_RAD_S, 4)}
                       for s, torque, omega in raw]})
    sync_rpm = machine.synchronous_speed() * RPM_PER_RAD_S
    return {'kind': 'family', 'x_label': '转速 rpm', 'y_label': '电磁转矩 N·m',
            'families': families,
            'markers': [{'x': sync_rpm, 'label': f'同步转速 {sync_rpm:.0f} rpm'}],
            'notes': ['转矩与端电压的平方成正比：电压降到 70%，最大转矩只剩 49%。'
                      '这就是"欠压时带不动负载"的定量解释。',
                      f'最大转矩出现在转差 s_m = {machine.max_torque_slip(inverter.linear_limit(cfg["vdc"]) / math.sqrt(2.0)):.3f} 附近，'
                      '不是转差越大转矩越大——超过 s_m 后转矩反而下降，进入不稳定区。']}


def _vf_family(cfg):
    """恒压频比（V/f）控制：不同频率下的"电压—频率"直线与同步转速。"""
    params = cfg['params']
    machine = make_machine('induction', params)
    f_nom = params['f']
    limit = inverter.linear_limit(cfg['vdc'])
    families = []
    for fraction in (0.25, 0.5, 0.75, 1.0):
        f = fraction * f_nom
        rms = fraction * limit / math.sqrt(2.0)
        raw = machine.torque_slip_curve(rms, points=80, slip_max=1.0)
        families.append({
            'label': f'{f:.1f} Hz（同步转速 {60 * f / params["p"]:.0f} rpm）',
            'frequency_hz': round(f, 3),
            'voltage_rms': round(rms, 4),
            'synchronous_rpm': round(60 * f / params['p'], 3),
            'points': [{'slip': round(s, 6), 'torque_nm': round(torque, 6),
                        'speed_rpm': round(omega * RPM_PER_RAD_S, 4)}
                       for s, torque, omega in raw]})
    return {'kind': 'family', 'x_label': '转速 rpm', 'y_label': '电磁转矩 N·m',
            'families': families,
            'notes': ['保持 V/f 恒定，就是保持气隙磁通基本恒定——'
                      '这是变频调速能用"一条曲线平移"来理解的原因。',
                      '频率很低时定子电阻压降占比变大，转矩会明显下降，'
                      '所以真实变频器都要加"低频电压补偿"（本实验可在参数里打开 boost）。']}


def _current_loop_bode(cfg):
    """电流环频率响应：把闭环当作一阶/二阶环节扫频。

    做法：给电流给定叠加小信号正弦，测稳态幅值比与相位差。
    这是"带宽"这个概念唯一诚实的测法——不靠公式猜。
    """
    gains = cfg['gains']
    kind = cfg['kind'] if cfg['kind'] in ('dc', 'pmsm_bldc') else 'pmsm_bldc'
    sweep = cfg['sweep']
    frequencies = _log_space(sweep['start_hz'], sweep['stop_hz'], int(sweep['points']))
    points = []
    for frequency in frequencies:
        omega = 2.0 * math.pi * frequency
        # 至少跑 6 个周期，取后 4 个周期算稳态幅相
        duration = min(10.0, max(0.4, 6.0 / frequency))
        run = dict(cfg)
        run.update({'kind': kind, 'scenario': 'current_loop', 'controller': 'current',
                    'duration': duration, 'target': 0.0,
                    'gains': dict(gains)})
        run['scenario_params'] = {'small_signal': 0.2, 'omega': omega,
                                  'open_loop_voltage': 0.0}
        # 小信号正弦给定由 reference 提供，这里用 scenario_params 传频率
        run['_sine'] = True
        try:
            result = engine.simulate(run)
        except ValueError as error:
            points.append({'frequency_hz': frequency, 'error': str(error)})
            continue
        series = result['series']
        measured = _demodulate(series, omega, key='iq' if kind == 'pmsm_bldc' else 'current')
        points.append({'frequency_hz': round(frequency, 4),
                       'gain_db': round(measured['gain_db'], 4) if measured else None,
                       'phase_deg': round(measured['phase_deg'], 3) if measured else None,
                       'reference_amplitude': 0.2})
    usable = [p for p in points if p.get('gain_db') is not None]
    bandwidth = None
    for previous, current in zip(usable, usable[1:]):
        if previous['gain_db'] >= -3.0 > current['gain_db']:
            # 直线插值找 −3 dB 交点
            span = previous['gain_db'] - current['gain_db']
            frac = (previous['gain_db'] + 3.0) / span if span else 0.0
            bandwidth = math.exp(math.log(previous['frequency_hz'])
                                 + frac * (math.log(current['frequency_hz'])
                                           - math.log(previous['frequency_hz'])))
            break
    return {'kind': 'bode', 'x_label': '频率 Hz', 'points': points,
            'bandwidth_hz': round(bandwidth, 3) if bandwidth else None,
            'notes': ['−3 dB 点就是常说的"闭环带宽"：在这个频率上，输出只能跟到输入的 70.7%。',
                      '带宽越高响应越快，但也越容易撞上采样延时与噪声放大；'
                      '工程上一般取开关频率的 1/10 量级作为电流环带宽上限。']}


def _demodulate(series, omega, key):
    """从轨迹里解调出给定频率下的幅值比与相位差。

    用相关性（正交解调）而不是峰值搜索：抗噪、也不怕采样点没落在峰上。
    只取后 2/3 段，避开启动瞬态。
    """
    usable = [row for row in series if 't' in row]
    if len(usable) < 40:
        return None
    start = len(usable) // 3
    segment = usable[start:]
    amplitude = 0.2
    sum_sin = sum_cos = sum_ref_sin = sum_ref_cos = 0.0
    for row in segment:
        phase = omega * row['t']
        value = row.get(key, 0.0)
        reference = amplitude * math.sin(phase)
        sum_sin += value * math.sin(phase)
        sum_cos += value * math.cos(phase)
        sum_ref_sin += reference * math.sin(phase)
        sum_ref_cos += reference * math.cos(phase)
    count = len(segment)
    out_sin, out_cos = 2.0 * sum_sin / count, 2.0 * sum_cos / count
    ref_sin, ref_cos = 2.0 * sum_ref_sin / count, 2.0 * sum_ref_cos / count
    if abs(ref_sin) < 1e-12 and abs(ref_cos) < 1e-12:
        return None
    out_mag = math.hypot(out_sin, out_cos)
    ref_mag = math.hypot(ref_sin, ref_cos)
    if ref_mag < 1e-12:
        return None
    gain_db = 20.0 * math.log10(max(out_mag / ref_mag, 1e-9))
    phase = math.degrees(math.atan2(out_cos, out_sin) - math.atan2(ref_cos, ref_sin))
    while phase > 180.0:
        phase -= 360.0
    while phase < -180.0:
        phase += 360.0
    return {'gain_db': gain_db, 'phase_deg': phase}


def _can_budget(cfg):
    """CAN 总线负载率：报文周期与节点数的影响。"""
    can = cfg['can']
    bus = rb.CanBus(bitrate=can['bitrate'], nodes=can['nodes'])
    period_base = can['period']
    frames = _log_space(0.0002, 0.02, 20)
    curves = []
    for name, count, bits in (('单电机：1 条状态帧', 1, 64),
                              ('四电机底盘：4 条状态帧 + 1 条指令', 5, 64),
                              ('整机：8 条状态帧 + 2 条指令 + 1 条心跳', 11, 48)):
        points = []
        for period in frames:
            messages = [{'bits': min(64, bits), 'period': period} for _ in range(count)]
            budget = bus.budget(messages)
            points.append({'period_ms': round(period * 1e3, 4),
                           'load_percent': round(budget['load_percent'], 3),
                           'jitter_us': round(budget['jitter_us'], 3)
                           if math.isfinite(budget['jitter_us']) else None})
        curves.append({'label': name, 'frames': count, 'points': points})
    return {'kind': 'family', 'x_label': '报文周期 ms', 'y_label': '总线负载率 %',
            'families': curves,
            'notes': ['负载率超过 80% 以后，低优先级报文的延迟会急剧变大；'
                      '这就是为什么要把"电机状态帧"和"非关键遥测"分开优先级。',
                      f'当前波特率 {can["bitrate"] / 1000:.0f} kbit/s，'
                      f'单帧（含位填充上界）约 {bus.frame_bits(64)} 位。']}


def _encoder_resolution(cfg):
    """编码器分辨率与测速噪声：同样的电机，不同线数下的速度量化台阶。"""
    lines_list = (100, 256, 500, 1000, 2000, 4000)
    period = cfg['control_period']
    curves = []
    for lines in lines_list:
        step_rad = 2.0 * math.pi / (4 * lines)
        # 差分测速的量化台阶（rad/s）
        quant = step_rad / period
        curves.append({'label': f'{lines} 线', 'lines': lines,
                       'quantisation_rad_s': round(quant, 6),
                       'quantisation_rpm': round(quant * RPM_PER_RAD_S, 4),
                       'counts_per_rev': 4 * lines})
    return {'kind': 'bars', 'x_label': '编码器线数', 'y_label': '测速量化台阶 rpm',
            'bars': [{'label': item['label'], 'value': item['quantisation_rpm'],
                      'lines': item['lines']} for item in curves],
            'notes': [f'控制周期 {period * 1e6:.0f} µs 下，'
                      '速度反馈的最小可分辨台阶 = 一个计数 ÷ 控制周期。',
                      '台阶越大，速度环就越容易"抖"；'
                      '要么提高线数，要么用更长的时间窗口测速（代价是相位滞后）。']}


def _four_bar_motion(cfg):
    """四连杆机构的输出角与传动比随曲柄角的变化。"""
    params = cfg['scenario_params']
    crank = params.get('crank_len', 0.04)
    coupler = params.get('coupler_len', 0.12)
    rocker = params.get('rocker_len', 0.10)
    ground = params.get('ground_len', 0.11)
    from . import control as ctl
    points = []
    for index in range(181):
        angle = math.pi * index / 180.0
        solution = ctl.four_bar(angle, crank, coupler, rocker, ground)
        if solution is None:
            continue
        points.append({'crank_deg': round(math.degrees(angle), 3),
                       'rocker_deg': round(math.degrees(solution['rocker_angle']), 3),
                       'ratio': None if solution['ratio'] is None else round(solution['ratio'], 4),
                       'coupler_x': round(solution['coupler'][0], 6),
                       'coupler_y': round(solution['coupler'][1], 6)})
    return {'kind': 'xy', 'x_label': '曲柄角 °', 'y_label': '摇杆角 °',
            'points': points, 'linkage': {'crank': crank, 'coupler': coupler,
                                          'rocker': rocker, 'ground': ground},
            'notes': ['ROBOCON 的取球/击球机构大多是四连杆：'
                      '电机转一整圈，末端只走一个工作行程。',
                      'ratio = dφ/dθ 就是"机构放大倍数"：'
                      '它在中段最大、在死点附近趋近 0——死点附近电机力矩需求会暴涨，'
                      '这也是机械与电控必须一起定的典型位置。']}


def _svpwm_spectrum(cfg):
    """SVPWM 与正弦 PWM 的直流电压利用率对照。"""
    vdc = cfg['vdc']
    limit_svpwm = inverter.linear_limit(vdc)
    limit_sine = inverter.sine_limit(vdc)
    rows = []
    for fraction in (0.2, 0.4, 0.6, 0.8, 1.0, 1.2):
        alpha = fraction * limit_sine
        duties_sine = inverter.carrier_pwm(alpha, 0.0, vdc)
        duties_svpwm = inverter.svpwm_duties(alpha, 0.0, vdc)
        rows.append({'command_v': round(alpha, 4),
                     'ratio_to_sine_limit': round(fraction, 4),
                     'sine_max_duty': round(max(duties_sine), 5),
                     'svpwm_max_duty': round(max(duties_svpwm), 5),
                     'svpwm_saturated': max(duties_svpwm) >= 0.999,
                     'sine_saturated': max(duties_sine) >= 0.999})
    return {'kind': 'table', 'rows': rows,
            'limits': {'sine_limit_v': round(limit_sine, 4),
                       'svpwm_limit_v': round(limit_svpwm, 4),
                       'gain_percent': round(100.0 * (limit_svpwm / limit_sine - 1.0), 3)},
            'notes': ['同样的母线电压，SVPWM 能输出的基波比正弦 PWM 高 15.47%——'
                      '这就是它在电机驱动里取代正弦 PWM 的主要理由。',
                      '代价是算法复杂一点（要判断扇区与作用时间），'
                      '但换来的是母线利用率，对电池供电的机器人非常划算。']}


def _power_budget(cfg):
    """整机电源预算：按工况点算峰值/平均电流与推荐电源容量。"""
    loads = cfg['scenario_params'].get('loads')
    if not loads:
        loads = [
            {'name': '行走电机 ×4', 'current_a': 12.0, 'duty': 0.35},
            {'name': '机构电机 ×2', 'current_a': 8.0, 'duty': 0.25},
            {'name': '控制板与传感', 'current_a': 0.8, 'duty': 1.0},
            {'name': '舵机 ×3', 'current_a': 2.5, 'duty': 0.3},
            {'name': '通信与灯', 'current_a': 0.5, 'duty': 0.8},
        ]
    budget = rb.power_budget(loads, margin=0.25)
    return {'kind': 'table', 'budget': budget, 'rows': budget['detail'],
            'notes': ['峰值电流决定导线与保险丝，平均电流决定电池容量（Ah）与散热。',
                      '推荐容量 = 峰值 × (1 + 裕度)：裕度不是浪费，'
                      '而是留给"卡住时电机堵转"的那几秒——机器人最贵的故障多半发生在这里。']}


# ------------------------------------------------------------------ 工具
def _voltages(cfg, default):
    raw = cfg['scenario_params'].get('ratios')
    if raw:
        return [max(0.05, min(1.5, float(value))) for value in raw]
    return list(default)


def _log_space(start, stop, count):
    if count < 2:
        return [start]
    return [start * (stop / start) ** (index / (count - 1)) for index in range(count)]
