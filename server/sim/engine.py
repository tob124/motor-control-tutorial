"""单次仿真运行引擎。

课程作者：Connor He 和 Astra。

职责：把「电机模型 + 数字控制器 + 反馈测量 + 负载工况（＋可选底盘运动学）」
装配起来，按固定子步积分、按控制周期更新控制、按采样周期输出一行轨迹。

四条纪律（后面所有实验都依赖它们）：

1. **反馈只有一条路**：控制器只看"测量值"，物理状态永远由真实状态推进。
   编码器反接、零偏、量纲错误表现为控制性能变差，而不是物理被篡改。
2. **积分与控制解耦**：控制周期固定，子步只决定积分精度。改子步不该改变
   控制行为，只该改变数值精度——测试会验证这一点。
3. **坐标要说清楚**：FOC 的 dq 电压先做 Park 反变换到 αβ，再经母线限幅与
   占空比计算。直接把 dq 宽度当 αβ 用是常见错误，这里不接受。
4. **不伪造保护**：真实存在的约束只有母线限幅；电流"超限"只报警告。
"""
from __future__ import annotations

import math

from . import control as ctl
from . import inverter
from . import integrate
from . import metrics as mt
from . import robot as rb
from .machines import make_machine

__all__ = ['simulate', 'Setup', 'RPM_PER_RAD_S']

RPM_PER_RAD_S = 60.0 / (2.0 * math.pi)
SQRT3 = math.sqrt(3.0)
_EPS = 1e-12

# 场景分类：哪些属于"扫一条曲线"而不是"跑一条轨迹"
_SWEEP_SCENARIOS = ('torque_speed', 'slip_scan')
_SPEED_SCENARIOS = ('speed', 'bus_can', 'fault_injection')
_POSITION_SCENARIOS = ('position_step',)

# 前端绘图需要的轨迹列（按电机类型挑选，避免把整张表塞给浏览器）
_DC_KEYS = ('t', 'rpm', 'measured_rpm', 'target_rpm', 'current', 'voltage', 'torque',
            'back_emf', 'temperature_c', 'vbus')
_PMSM_KEYS = ('t', 'rpm', 'target_rpm', 'id', 'iq', 'ia', 'ib', 'ic', 'torque',
              'theta_e', 'temperature_c', 'vbus')
_IM_KEYS = ('t', 'rpm', 'target_rpm', 'ids', 'iqs', 'idr', 'iqr', 'ia', 'ib', 'ic',
            'psi_r', 'slip', 'torque', 'temperature_c', 'vbus')
_ROBOT_KEYS = ('t', 'x', 'y', 'theta', 'odom_x', 'odom_y', 'left_rpm', 'right_rpm',
               'left_ref_rpm', 'right_ref_rpm', 's')

_TRACE_KEYS = {'dc': _DC_KEYS, 'pmsm_bldc': _PMSM_KEYS, 'induction': _IM_KEYS}

# 哪些场景的 target 是"转速（rpm）"。其余场景（位置阶跃、电流、路径速度）
# 的 target 已经是控制器内部使用的单位（rad、A、m/s），不做换算。
_RPM_TARGET_SCENARIOS = ('speed', 'bus_can', 'fault_injection', 'steady_state')


def scenario_needs_rpm(scenario):
    return scenario in _RPM_TARGET_SCENARIOS


class Setup:
    """一次运行的完整装配。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.kind = cfg['kind']
        self.notes = []
        self.warnings = list(cfg.get('warnings', ()))
        self.faults = set(cfg.get('faults') or ())
        self.params = dict(cfg['params'])
        self.vdc = cfg['vdc']
        self.bridge = cfg['bridge']
        self.controller = cfg['controller']
        self.scenario = cfg['scenario']
        self.sensor_mode = cfg['sensor']
        self.gains = cfg['gains']
        self.limits = cfg['limits']
        self.scenario_params = cfg.get('scenario_params', {})
        # 速度给定在控制器内部一律用 rad/s（与状态量同单位）；
        # rpm 只出现在界面与导出的那一列，避免"拿 rpm 当 rad/s 比"的经典事故。
        self.target = cfg['target'] / RPM_PER_RAD_S if scenario_needs_rpm(cfg['scenario']) else cfg['target']
        self.target_display = cfg['target']
        self.load_value = cfg['load_value']
        self.ramp_rate = cfg['ramp_rate']
        self.load_mode = cfg['load']
        self.path = cfg['path']
        self.bus_cfg = cfg['bus']
        self.can_cfg = cfg['can']
        self.duration = cfg['duration']
        self.control_period = cfg['control_period']
        self.substep = cfg['substep']
        self.sample_period = cfg['sample_period']
        self.robot_cfg = cfg.get('robot_params')

        self.omega_s = (2.0 * math.pi * self.params.get('f', 50.0) / self.params.get('p', 1)
                        if self.kind == 'induction' else 0.0)
        self.theta_flux = 0.0
        self.machine = make_machine(self.kind, self.params, omega_s=self.omega_s)
        self._inv_cache_owner = self.machine

        # 电流环实验是堵转实验：转子锁定由机器模型显式实现
        if cfg['scenario'] == 'current_loop':
            self.machine.rotor_locked = True
            self.notes.append('电流环实验按堵转工况进行（转子被锁定）：这样 dq 电流才是直流，'
                              '便于观察跟踪与解耦；转起来以后 dq 电流本就会按电频率振荡。')
        self._build_sensor()
        self._build_controller()
        self._build_drive()
        self._build_robot()
        self._build_faults()

    # -------------------------------------------------------------- 装配
    def _build_sensor(self):
        lines = int(self.limits.get('encoder_lines', 1000))
        mode = self.sensor_mode
        faults = set(self.faults)
        if mode == 'reversed':
            faults.add('encoder_reversed')
        if mode == 'biased':
            faults.add('sensor_bias')
        self.encoder = ctl.Encoder(lines=lines, faults=faults,
                                   bias_rad=0.15 if 'sensor_bias' in faults else 0.0)
        self.sensor_jitter = mode == 'jitter'
        self.use_encoder_speed = mode != 'ideal' or bool(faults & {'encoder_reversed', 'sensor_bias', 'units_confusion'})
        if self.sensor_jitter:
            self.warnings.append('反馈加入 ±1 计数抖动：速度环会出现可见噪声，这是真实驱动器的常态。')
        if 'encoder_reversed' in faults:
            self.warnings.append('编码器计数方向与电机转向相反：速度/位置反馈符号错误，闭环成为正反馈，'
                                 '典型表现是"一使能就飞车或直接报过流"。')
        if 'units_confusion' in faults:
            self.warnings.append('量纲混淆：把弧度当角度处理，反馈被放大约 57.3 倍。')

    def _build_controller(self):
        """装配控制器。

        重要：**每一级的限幅必须落在 PI 内部**，而不是算完之后在外面夹一下。
        外面夹只是"把输出改小"，积分项照样往上爬（积分饱和），
        表现出来就是"到点后冲过头、来回震荡"。这里把限幅交给 PI 的
        条件积分处理，才和真实伺服驱动器的行为一致。
        """
        g = self.gains
        aw = bool(g.get('anti_windup', True))
        self.iq_limit = self.limits.get('iq_limit') or self.limits.get('current_phase') or 8.0
        self.speed_limit = (self.limits.get('target_rpm') or 6000.0) / RPM_PER_RAD_S
        self.voltage_limit = self.limits.get('voltage') or self.vdc

        # 位置环输出 = 速度给定，用速度限幅；速度环输出 = 电流给定，用电流限幅
        self.pi_position = ctl.PI(g.get('position_kp', 0.0), 0.0, self.speed_limit, aw, '位置环')
        self.pi_speed = ctl.PI(g.get('speed_kp', 0.0), g.get('speed_ki', 0.0),
                               self.iq_limit, aw, '速度环')
        self.pi_current = ctl.PI(g.get('current_kp', 0.0), g.get('current_ki', 0.0),
                                 self.voltage_limit, aw, '电流环')
        self.pi_vq = ctl.PI(g.get('vq_kp', 0.0), g.get('vq_ki', 0.0),
                            inverter.linear_limit(self.vdc), aw, 'q 轴电流环')
        self.pi_vd = ctl.PI(g.get('vd_kp', 0.0), g.get('vd_ki', 0.0),
                            inverter.linear_limit(self.vdc), aw, 'd 轴电流环')
        self.pi_slip = ctl.PI(g.get('kspeed_kp', 0.0), g.get('kspeed_ki', 0.0),
                              self.iq_limit, aw, '转差频率环')
        # 异步机额定励磁电流：由额定磁链 ψ = Lm·i_d 反算，取磁链为 Lm·6A 量级
        self.i_ds_rated = 6.0
        self.pi_wheel_l = ctl.PI(g.get('wheel_kp', 0.35), g.get('wheel_ki', 3.0), 0.0, aw, '左轮速度环')
        self.pi_wheel_r = ctl.PI(g.get('wheel_kp', 0.35), g.get('wheel_ki', 3.0), 0.0, aw, '右轮速度环')
        self.pursuit = ctl.PurePursuit(lookahead=self.path['lookahead'])

    def _build_drive(self):
        self.bus = rb.BusModel(nominal_voltage=self.vdc,
                               resistance=self.bus_cfg['resistance'],
                               thermal_r=self.bus_cfg['thermal_r'],
                               thermal_c=self.bus_cfg['thermal_c'],
                               ambient_c=self.bus_cfg['ambient_c'],
                               derate_c=self.bus_cfg['derate_c'])
        self.vbus_now = self.vdc
        self.saturation_events = 0
        self.thermal_peak = self.bus_cfg['ambient_c']

    def _build_robot(self):
        self.drive = None
        self.pose = (0.0, 0.0, 0.0)
        self.odom_pose = (0.0, 0.0, 0.0)
        self.theta_left = 0.0
        self.theta_right = 0.0
        self._odom_prev = (0.0, 0.0)
        self._right_state = None
        self.theta_true = 0.0
        if not self.robot_cfg:
            return
        self.drive = ctl.DifferentialDrive(track=self.robot_cfg['track'],
                                           wheel_radius=self.robot_cfg['wheel_radius'],
                                           gear=self.robot_cfg.get('gear', 1.0))
        self.path_obj = ctl.Path(shape=self.path['shape'], length=self.path['length'],
                                 radius=self.path['radius'])
        self.left = make_machine(self.kind, self.params, omega_s=self.omega_s)
        self.right = make_machine(self.kind, self.params, omega_s=self.omega_s)
        # 里程计使用的编码器：真实机器人上左右轮各一个
        self.enc_l = ctl.Encoder(lines=int(self.limits.get('encoder_lines', 1000)),
                                 faults=self.faults)
        self.enc_r = ctl.Encoder(lines=int(self.limits.get('encoder_lines', 1000)),
                                 faults=self.faults)

    def _build_faults(self):
        # 相序接反是**执行器侧**接线错误：物理上真的反转
        self.actuator_sign = -1.0 if 'phase_swap' in self.faults else 1.0
        if 'phase_swap' in self.faults:
            self.notes.append('相序接反：三相中的两相对调，电机物理上反转。'
                              '电流环本身仍正常，但速度/位置环会试图反向补偿；'
                              '位置环在正反馈下发散，这是现场最容易烧驱动的一类接线事故。')
        if 'vbus_sag' in self.faults:
            self.warnings.append('母线跌落故障：端电压随电流下降，大加速时可用电压不足，表现为"一到加速段就失速"。')
        if 'mechanical_jam' in self.faults:
            self.warnings.append('机械卡死：负载被强制为堵转值，控制器将持续饱和，电流与温升是主要证据。')
        if 'can_dropout' in self.faults:
            self.warnings.append('CAN 丢帧：部分控制周期收不到新指令，表现为指令保持与阶段性跳变。')

    # -------------------------------------------------------------- 工况
    def load_torque(self, t, omega):
        """负载转矩（阻力型正值）。

        两个教学上必需的约定：

        * ``scenario='current_loop'`` 视为**堵转**：电流环只应在转子不动时
          考察跟踪与解耦。否则转子一转，dq 电流按电频率振荡，看起来像
          "电流环坏了"，其实只是坐标系在转。这里用一个足够的保持转矩把
          转速钉在零附近，并在告警里说明。
        * ``mechanical_jam`` 故障额外叠加一个堵转级负载。
        """
        if self.scenario == 'current_loop':
            # 堵转：机械方程被显式锁定，负载无意义
            return 0.0
        if 'mechanical_jam' in self.faults:
            stall = 0.5 * abs(self.params.get('Kt') or self.params.get('lambda_m') or 0.05)
            return self.load_value + max(0.02, stall)
        mode = self.load_mode
        if mode == 'none':
            return 0.0
        if mode == 'constant':
            return self.load_value
        if mode == 'step':
            return self.load_value if t >= self.duration * 0.5 else 0.0
        if mode == 'ramp':
            return self.ramp_rate * t
        if mode == 'fan':
            nom = max(1e-6, self.speed_limit)
            return self.load_value * (omega / nom) * abs(omega / nom)
        raise ValueError(f'未知负载类型：{mode}')

    def reference(self, t):
        """参考值随时间的变化：在 ``reference_step`` 时刻加阶跃。

        默认 20% 处加阶跃，给启动段留观察窗口。异步机加速慢，给得太早
        会在"还没到稳态"时就被再次扰动，曲线看着像模型有问题——所以
        步进时刻是可配的（``scenario_params.reference_step``，取时长的比例）。
        """
        if self.scenario in ('open_loop', 'steady_state', 'torque_speed', 'vf_scan',
                             'slip_scan', 'bode'):
            return 0.0
        if self.scenario == 'position_step':
            return self.target
        if self.scenario == 'path_follow':
            return self.path['speed']
        omega = self.payload('omega', 0.0)
        if omega:
            # 小信号扫频：给定是正弦，用于测量电流环的频率响应。
            # 必须先跑几个周期再取数据（scenario 里只解调后 2/3 段）。
            return self.payload('small_signal', 0.2) * math.sin(omega * t)
        step = self.payload('reference_step', 0.2) * self.duration
        return self.target if t >= step else 0.0

    def payload(self, key, default):
        return self.scenario_params.get(key, default)

    # -------------------------------------------------------------- 测量
    def measure(self, state, t, theta_true, prev_theta):
        """真实状态 → 控制器看到的测量值。

        故障与噪声**只在这一步**引入：物理状态永远由 ``deriv`` 推进，
        控制器看到的只是一个可能被接线错误、量纲错误或噪声污染的信号。
        这是本课程最重要的建模纪律之一——故障应当表现为"控制变差"，
        而不是"物理被篡改"。
        """
        if self.kind == 'dc':
            omega = state[1]
            omega_meas = omega
            if self.use_encoder_speed:
                omega_meas = self._encoder_speed(theta_true, prev_theta)
                if self.sensor_jitter:
                    omega_meas += self._jitter(t)
            return {'position': self.encoder.position_rad(theta_true), 'speed': omega_meas,
                    'current': state[0], 'id': 0.0, 'iq': state[0]}
        if self.kind == 'induction':
            i_ds, i_qs, _i_dr, _i_qr = self.machine.currents(state)
            return {'position': 0.0, 'speed': state[4], 'current': math.hypot(i_ds, i_qs),
                    'id': i_ds, 'iq': i_qs}
        i_d, i_q, omega, theta = state
        omega_meas = omega
        if self.use_encoder_speed:
            omega_meas = self._encoder_speed(theta, prev_theta)
            if self.sensor_jitter:
                omega_meas += self._jitter(t)
        return {'position': self.encoder.position_rad(theta), 'speed': omega_meas,
                'current': math.hypot(i_d, i_q), 'id': i_d, 'iq': i_q}

    def _encoder_speed(self, theta_now, theta_prev):
        delta = self.encoder.counts(theta_now) - self.encoder.counts(theta_prev)
        return delta * 2.0 * math.pi / self.encoder.edges_per_rev / self.control_period

    def _jitter(self, t):
        sign = 1.0 if int(round(t / self.control_period)) % 2 else -1.0
        return sign * self.encoder.quant_step_rad() / self.control_period

    def true_position(self, state):
        if self.kind == 'dc':
            return self.theta_true
        if self.kind == 'induction':
            return 0.0
        return state[3]

    # -------------------------------------------------------------- 控制
    def control_step(self, t, measured, dt, theta_true, prev_theta):
        """一个控制周期：返回施加给模型的输入量（不含负载）。"""
        out = {'u': 0.0, 'ud': 0.0, 'uq': 0.0, 'ud_actual': 0.0, 'uq_actual': 0.0,
               'saturated': False}
        if self.scenario in ('open_loop', 'steady_state', 'torque_speed', 'slip_scan',
                             'vf_scan', 'bus_can'):
            return self._open_loop_step(t, out, dt)
        if self.kind == 'dc':
            return self._dc_step(t, measured, dt, out)
        if self.kind == 'pmsm_bldc':
            if self.payload('six_step', False):
                return self._six_step(t, measured, out)
            return self._foc_step(t, measured, dt, out)
        return self._induction_step(t, measured, dt, out)

    def _open_loop_step(self, t, out, dt):
        """开环：电压/频率按工况设定，控制器不参与。"""
        kind = self.kind
        scenario = self.scenario
        u_open = self.payload('open_loop_voltage', self.vdc * 0.5)
        if scenario in ('open_loop', 'steady_state', 'torque_speed', 'slip_scan'):
            if kind == 'dc':
                out['u'] = u_open
            elif kind == 'pmsm_bldc':
                # 永磁机开环：速度环断开，只给 q 轴电压（d 轴给 0）。
                # 这正是"没有位置反馈的同步机"的实验，会看到失步/振荡。
                uq = min(u_open, inverter.linear_limit(self.vbus_now))
                out['ud'] = out['ud_actual'] = 0.0
                out['uq'] = out['uq_actual'] = uq
            else:
                if scenario == 'open_loop':
                    self.omega_s = 2.0 * math.pi * _frequency_schedule(self)(t) / self.params['p']
                else:
                    self.omega_s = self.machine.synchronous_speed()
                out['ud'] = out['ud_actual'] = min(u_open, inverter.linear_limit(self.vbus_now))
            return out
        if scenario == 'vf_scan':
            f_now = _frequency_schedule(self)(t)
            self.omega_s = 2.0 * math.pi * f_now / self.params['p']
            ratio = self.payload('vf_ratio', 0.9)
            boost = self.payload('vf_boost', 0.08)
            per_hz = ratio * inverter.linear_limit(self.vdc) / max(self.params['f'], 1e-9)
            out['ud'] = out['ud_actual'] = min(per_hz * f_now + boost * self.vdc * 0.1,
                                               inverter.linear_limit(self.vbus_now))
            return out
        if scenario == 'bus_can':
            # 0.5 Hz 方波式速度工况：制造周期性大电流，观察母线压降与温升
            demand = self.vdc * 0.4 * (1.0 if math.sin(2.0 * math.pi * 0.5 * t) >= 0 else -1.0)
            if kind == 'dc':
                out['u'] = demand
            else:
                self.omega_s = 2.0 * math.pi * self.params.get('f', 50.0) / self.params.get('p', 1)
                out['ud'] = out['ud_actual'] = min(abs(demand), inverter.linear_limit(self.vbus_now))
            return out
        raise ValueError(f'该场景不支持开环控制：{scenario}')

    def _dc_step(self, t, measured, dt, out):
        """直流机串级控制：位置（可选）→ 速度 → 电流 → 电压。

        控制方式语义（与 PMSM / 异步机保持一致）：

        * ``speed``：速度环出电流给定，电流环出电压——完整双环；
        * ``position``：位置环出速度给定 → 速度环 → 电流环；
        * ``current``：只跑电流环，参考值就是电流给定（堵转标定用）。

        每一级限幅都由 PI 内部处理（见 ``_build_controller``），
        不在外面再夹一次——外面夹会掩盖积分饱和。
        """
        reference = self.reference(t)
        if self.controller == 'open':
            out['u'] = 0.0
            return out
        speed_ref = reference
        if self.controller == 'speed':
            current_ref = self.pi_speed.step(reference - measured['speed'], dt)
        elif self.controller == 'position':
            speed_ref = self.pi_position.step(reference - measured['position'], dt)
            current_ref = self.pi_speed.step(speed_ref - measured['speed'], dt)
        elif self.controller == 'current':
            current_ref = reference
        else:
            raise ValueError(f'直流机不支持的控制方式：{self.controller}')
        voltage = self.pi_current.step(current_ref - measured['current'], dt)
        out.update({'u': voltage, 'current_ref': current_ref, 'speed_ref': speed_ref,
                    'saturated': self.pi_current.saturated or self.pi_speed.saturated})
        return out

    def _foc_step(self, t, measured, dt, out):
        """PMSM 磁场定向控制：d/q 电流 PI + 前馈解耦 + Park 反变换 + 母线限幅。

        坐标顺序不能省：dq 是转子坐标系，逆变器只在静止 αβ 系里工作，
        所以必须先把电压指令做 Park 反变换，再谈限幅和占空比。
        """
        params = self.params
        p_pole = params['p']
        theta_e = p_pole * measured['position']
        reference = self.reference(t)
        speed_ref = reference
        if self.controller == 'current':
            # 电流环实验：参考值就是 q 轴电流给定，不经过速度环
            iq_ref = reference
        elif self.controller == 'speed':
            iq_ref = self.pi_speed.step(reference - measured['speed'], dt)
        elif self.controller == 'position':
            speed_ref = self.pi_position.step(reference - measured['position'], dt)
            iq_ref = self.pi_speed.step(speed_ref - measured['speed'], dt)
        else:
            raise ValueError(f'永磁同步机不支持的控制方式：{self.controller}')
        i_d_ref = self.payload('id_ref', 0.0)
        we = p_pole * measured['speed']
        ff_d = -we * params['Lq'] * measured['iq']
        ff_q = we * (params['Ld'] * measured['id'] + params['lambda_m'])
        u_d_cmd = self.pi_vd.step(i_d_ref - measured['id'], dt) + ff_d
        u_q_cmd = self.pi_vq.step(iq_ref - measured['iq'], dt) + ff_q

        v_alpha, v_beta = inverter.dq_to_alphabeta(u_d_cmd, u_q_cmd, theta_e)
        alpha_l, beta_l, mod_index, limited = inverter.clamp_vector(
            v_alpha, v_beta, self.vbus_now, 'pwm' if self.bridge == 'pwm' else 'svpwm')
        duties = (inverter.carrier_pwm(alpha_l, beta_l, self.vbus_now) if self.bridge == 'pwm'
                  else inverter.svpwm_duties(alpha_l, beta_l, self.vbus_now))
        sector = inverter.svpwm_sector(alpha_l, beta_l)
        u_d_act, u_q_act = inverter.alphabeta_to_dq(alpha_l, beta_l, theta_e)

        if limited:
            self.saturation_events += 1
        out.update({'ud': u_d_cmd, 'uq': u_q_cmd, 'ud_actual': u_d_act, 'uq_actual': u_q_act,
                    'mod_index': mod_index, 'duties': duties, 'sector': sector,
                    'theta_e': theta_e, 'iq_ref': iq_ref, 'id_ref': i_d_ref,
                    'speed_ref': speed_ref, 'saturated': limited})
        return out

    def _six_step(self, t, measured, out):
        """六步方波（BLDC 常用）：按电角度扇区给两相通电，与 FOC 做对照。"""
        theta_e = (self.params['p'] * measured['position']) % (2.0 * math.pi)
        sector = int(theta_e // (math.pi / 3.0)) + 1
        amp = min(self.vdc * 0.5, self.voltage_limit)
        out.update({'ud_actual': amp if sector % 2 else 0.0,
                    'uq_actual': 0.0 if sector % 2 else amp,
                    'sector': sector, 'duties': (0.5, 0.5, 0.5), 'six_step_sector': sector})
        return out

    def _induction_step(self, t, measured, dt, out):
        """异步机**间接磁场定向控制**（转差频率型）。

        实现的是教科书上的标准间接 FOC，而不是"把频率直接当转速给"：

        1. 励磁电流给定 ``i_ds*`` 由额定磁链反算，保持恒磁通；
        2. 速度环输出转矩电流给定 ``i_qs*``；
        3. **转差频率由转矩电流决定**：``ω_slip = (Rr/Lr)·(i_qs*/i_ds*)``；
        4. 定子电角速度 ``ω_e = p·ω + ω_slip``，积分得到磁链位置角 θ_e；
        5. dq 坐标建立在磁链上，再做 Park 反变换拿到 αβ 电压。

        ``ω_slip`` 用代数式而不是积分器，是因为它本来就由转矩给定唯一确定——
        这样控制器不会和转速测量相互抵消（那正是"看起来在调、其实没调"的陷阱）。
        """
        params = self.params
        reference = self.reference(t)
        nominal_ws = self.machine.synchronous_speed()
        speed_ref = reference
        if self.controller == 'position':
            speed_ref = self.pi_position.step(reference - measured['position'], dt)

        # 转子时间常数与额定磁链
        tau_r = params['Lr'] / params['Rr']
        flux_rated = params['Lm'] * self.i_ds_rated
        self.pi_slip.limit = self.iq_limit
        i_qs_ref = self.pi_slip.step(speed_ref - measured['speed'], dt)
        # 按额定磁链维持励磁分量：低速时不弱磁，高速时才需要单独处理（本课不涉及）
        i_ds_ref = self.i_ds_rated
        omega_slip = (1.0 / tau_r) * (i_qs_ref / max(i_ds_ref, 1e-6))

        if self.omega_s <= 0.0 and t <= 0.0:
            self.omega_s = nominal_ws
        self.omega_s = max(0.0, measured['speed'] + omega_slip / params['p'])
        self.theta_flux += params['p'] * measured['speed'] * dt + omega_slip * dt

        # 恒磁通所需的定子电压（稳态电压方程，含反电势）
        omega_e = params['p'] * self.omega_s
        u_ds = params['Rs'] * i_ds_ref - omega_e * params['Ls'] * i_qs_ref
        u_qs = params['Rs'] * i_qs_ref + omega_e * params['Ls'] * i_ds_ref
        v_alpha, v_beta = inverter.dq_to_alphabeta(u_ds, u_qs, self.theta_flux)
        v_alpha, v_beta, _mod, limited = inverter.clamp_vector(v_alpha, v_beta, self.vbus_now, 'svpwm')
        out.update({'ud': u_ds, 'uq': u_qs, 'ud_actual': v_alpha, 'uq_actual': v_beta,
                    'omega_s': self.omega_s, 'omega_slip': omega_slip, 'theta_flux': self.theta_flux,
                    'speed_ref': speed_ref, 'id_ref': i_ds_ref, 'iq_ref': i_qs_ref,
                    'slip': self.omega_s - measured['speed'],
                    'saturated': limited or self.pi_slip.saturated})
        if limited:
            self.saturation_events += 1
            if not getattr(self, '_flux_weak_noted', False):
                self._flux_weak_noted = True
                self.warnings.append('异步机在额定频率附近进入电压饱和：母线电压不足以同时维持磁通与转矩电流，'
                                     '这就是恒功率区/弱磁问题的起点（本课只做到"看见它"）。')
        _ = flux_rated
        return out

    # -------------------------------------------------------------- 主循环
    def run(self):
        cfg = self.cfg
        machine = self.machine
        state = machine.initial_state()
        n_control, n_sub, dt_ctrl, dt_sub = integrate.substep_plan(
            self.duration, self.control_period, self.substep)
        sample_every = max(1, int(round(self.sample_period / dt_ctrl)))
        use_robot = self.drive is not None

        series = []
        self.theta_true = 0.0
        prev_theta = 0.0
        control = (0.0,) * len(machine.input_keys)
        left_state = right_state = None
        left_cmd = right_cmd = 0.0
        if use_robot:
            left_state = self.left.initial_state()
            right_state = self.right.initial_state()
        t = 0.0
        s_true = 0.0
        total_saturation = 0

        for step_index in range(n_control):
            # 采样时刻就是本控制周期的起点。``prev_theta`` 保存的是**上一个
            # 周期起点**的编码器位置，两者之差除以控制周期就是编码器测速——
            # 与真实驱动器"定时器中断里读编码器寄存器"的时序完全一致。
            t_control = step_index * dt_ctrl
            measured = None
            out = None
            if use_robot:
                measured = self._robot_measure(left_state, right_state)
                left_cmd, right_cmd = self._robot_control(t_control, s_true, measured, dt_ctrl)
            else:
                measured = self.measure(state, t_control, self.theta_true, prev_theta)
                out = self.control_step(t_control, measured, dt_ctrl, self.theta_true, prev_theta)
                if out.get('saturated'):
                    total_saturation += 1
                sign = self.actuator_sign
                if self.kind == 'dc':
                    control = (out.get('u', 0.0) * sign,)
                elif self.kind == 'pmsm_bldc':
                    control = (out.get('ud_actual', 0.0) * sign, out.get('uq_actual', 0.0) * sign)
                else:
                    control = (out.get('ud_actual', 0.0) * sign, out.get('uq_actual', 0.0) * sign)
                # 位置反馈必须与速度一致地推进。锁转子时加速度为零，
                # 但"已经转起来再用固定频率驱动"这种实验里位置仍在变，
                # 不推进就会让位置环收到一个永远不动的假反馈。
                if self.kind == 'pmsm_bldc':
                    self.theta_true = state[3]
                prev_theta = self.theta_true

            # ---- 物理积分（负载在每个子步按精确时刻重算）
            for _sub in range(n_sub):
                if use_robot:
                    left_state = self._motor_substep(self.left, left_state, left_cmd, t, dt_sub)
                    right_state = self._motor_substep(self.right, right_state, right_cmd, t, dt_sub)
                    self._right_state = right_state
                    self.pose = self.drive.step_pose(self.pose, self.left.omega(left_state),
                                                     self.right.omega(right_state), dt_sub)
                    s_true += self.drive.forward(self.left.omega(left_state),
                                                 self.right.omega(right_state))[0] * dt_sub
                else:
                    state = integrate.rk4_step(
                        lambda tt, ss: machine.deriv(
                            ss, control + (self.load_torque(tt, machine.omega(ss)),)),
                        t, state, dt_sub)
                    if self.kind == 'dc':
                        self.theta_true += state[1] * dt_sub
                t += dt_sub

            # ---- 里程计推进（只使用编码器计数，模拟真实感知）
            if use_robot:
                self._odometry(left_state, right_state, dt_ctrl)

            # ---- 母线电压与热模型
            bus_state = left_state if use_robot else state
            current_for_bus = self._bus_current(bus_state)
            droop = self.bus_cfg['voltage_droop_per_a']
            if 'vbus_sag' in self.faults or droop > 0.0:
                self.vbus_now = max(0.4 * self.vdc, self.bus.terminal_voltage(current_for_bus))
            else:
                self.vbus_now = self.vdc
            self.bus.step_temperature(current_for_bus, dt_ctrl)
            self.thermal_peak = max(self.thermal_peak, self.bus.temperature_c)

            if step_index % sample_every == 0 or step_index == n_control - 1:
                row = self._row(t, state, measured, control, s_true, left_state, left_cmd, right_cmd)
                series.append(_prune(row, _ROBOT_KEYS if use_robot else _TRACE_KEYS[self.kind]))

            # ---- 数值发散检查
            # 闭环正反馈（例如编码器接反）、参数极端或积分饱和都可能让状态发散。
            # 这里如实停止并报告，绝不把 NaN 或 1e300 当成曲线交给前端。
            probe = left_state if use_robot else state
            if not _all_finite(probe):
                self.diverged = True
                self.diverged_at = t
                self.warnings.append(
                    f'仿真在 t = {t:.3f} s 数值发散并已停止：状态出现非有限值。'
                    '常见原因：反馈极性接反（编码器方向）、增益远超对象带宽、'
                    '或积分饱和后长时间满压。请先检查反馈符号，再降低增益。')
                break

        metrics = self._metrics(series)
        metrics['saturation_percent'] = 100.0 * total_saturation / max(1, n_control)
        metrics['thermal_peak_c'] = self.thermal_peak
        metrics['bus_sag_v'] = self.vdc - self.vbus_now
        metrics['diverged'] = bool(getattr(self, 'diverged', False))
        if getattr(self, 'diverged', False):
            metrics['diverged_at_s'] = self.diverged_at
            metrics['completed'] = False
        else:
            metrics['completed'] = True
        # ``warnings`` 是校验阶段的产物，不属于仿真配置本身；
        # 再喂回 validate() 会被判成"未登记字段"。
        config = {key: value for key, value in cfg.items() if key != 'warnings'}
        return {'series': series, 'metrics': metrics, 'notes': list(self.notes),
                'warnings': self.warnings, 'config': config, 'machine': machine.describe(),
                'keys': list(_ROBOT_KEYS if use_robot else _TRACE_KEYS[self.kind]),
                'kind': self.kind, 'scenario': self.scenario}

    # -------------------------------------------------------------- 运行辅助
    def _motor_substep(self, machine, state, cmd, t, dt):
        """单台电机的子步积分：``cmd`` 是本周期保持的控制量。"""
        return integrate.rk4_step(
            lambda tt, ss: machine.deriv(ss, (cmd, self.load_torque(tt, machine.omega(ss)))),
            t, state, dt)

    def _robot_measure(self, left_state, right_state):
        return {'left_speed': self.left.omega(left_state),
                'right_speed': self.right.omega(right_state),
                'speed': (self.left.omega(left_state) + self.right.omega(right_state)) / 2.0,
                'position': 0.0, 'current': 0.0, 'id': 0.0, 'iq': 0.0}

    def _robot_control(self, t, s_true, measured, dt):
        """底盘控制：纯追踪给左右轮速度指令，再由轮速环跟到位。"""
        if self.scenario != 'path_follow':
            return 0.0, 0.0
        v, omega, _err = self.pursuit.step(self.odom_pose, self.path_obj, s_true,
                                           speed=self.path['speed'])
        w_l, w_r = self.drive.inverse(v, omega)
        w_l, _ = ctl.saturate(w_l, self.speed_limit)
        w_r, _ = ctl.saturate(w_r, self.speed_limit)
        # 轮速环：把轮速指令折算成电机侧转矩/电压指令
        cmd_l = self._wheel_command(self.pi_wheel_l, w_l, measured['left_speed'], dt)
        cmd_r = self._wheel_command(self.pi_wheel_r, w_r, measured['right_speed'], dt)
        return cmd_l, cmd_r

    def _wheel_command(self, pi, reference, actual, dt):
        """轮速环输出：直流机是电压，交流机是 q 轴电流给定。"""
        pi.limit = self.voltage_limit if self.kind == 'dc' else self.iq_limit
        return pi.step(reference - actual, dt)

    def _odometry(self, left_state, right_state, dt):
        """里程计：只用编码器计数得到左右轮位移，再推算位姿。

        这是在"感知受限"的前提下做的：真实机器人拿不到轮子真实角速度，
        只有计数值。量化误差与方向错误都会在这里体现。
        """
        theta_l = self.theta_left + self.left.omega(left_state) * dt
        theta_r = self.theta_right + self.right.omega(right_state) * dt
        count_l = self.enc_l.counts(theta_l)
        count_r = self.enc_r.counts(theta_r)
        self.theta_left, self.theta_right = theta_l, theta_r
        ang_l = count_l * 2.0 * math.pi / self.enc_l.edges_per_rev
        ang_r = count_r * 2.0 * math.pi / self.enc_r.edges_per_rev
        prev = self._odom_prev
        ds_l = (ang_l - prev[0]) * self.drive.wheel_radius / self.drive.gear
        ds_r = (ang_r - prev[1]) * self.drive.wheel_radius / self.drive.gear
        self._odom_prev = (ang_l, ang_r)
        ds = (ds_l + ds_r) / 2.0
        dtheta = (ds_r - ds_l) / self.drive.track
        x, y, theta = self.odom_pose
        theta_new = theta + dtheta
        self.odom_pose = (x + ds * math.cos((theta + theta_new) / 2.0),
                          y + ds * math.sin((theta + theta_new) / 2.0),
                          theta_new)

    def _bus_current(self, state):
        """母线电流估计：直流机用电枢电流，交流机用定子电流矢量幅值。"""
        if state is None:
            return 0.0
        if self.kind == 'dc':
            return abs(state[0])
        if self.kind == 'induction':
            i_ds, i_qs, _i_dr, _i_qr = self.machine.currents(state)
            return math.hypot(i_ds, i_qs)
        return math.hypot(state[0], state[1])

    def _row(self, t, state, measured, control, s_true, left_state, left_cmd, right_cmd):
        row = {'t': round(t, 6)}
        if left_state is not None:
            row.update({
                'x': self.pose[0], 'y': self.pose[1], 'theta': self.pose[2],
                'odom_x': self.odom_pose[0], 'odom_y': self.odom_pose[1],
                'odom_theta': self.odom_pose[2],
                'left_rpm': self.left.omega(left_state) * RPM_PER_RAD_S,
                'right_rpm': self.right.omega(self._right_state) * RPM_PER_RAD_S,
                'left_ref_rpm': left_cmd * RPM_PER_RAD_S,
                'right_ref_rpm': right_cmd * RPM_PER_RAD_S,
                's': s_true})
            return row
        machine = self.machine
        out = machine.outputs(state, (tuple(control) + (0.0,) * 3)[:3])
        if self.kind == 'dc':
            current, omega = state
            row.update({'rpm': omega * RPM_PER_RAD_S, 'measured_rpm': measured['speed'] * RPM_PER_RAD_S,
                        'target_rpm': self.reference(t) * RPM_PER_RAD_S,
                        'current': current, 'voltage': control[0], 'torque': out.get('torque', 0.0),
                        'back_emf': out.get('back_emf', 0.0), 'id': 0.0, 'iq': current})
        elif self.kind == 'induction':
            omega = state[4]
            i_ds, i_qs, i_dr, i_qr = machine.currents(state)
            row.update({'rpm': omega * RPM_PER_RAD_S, 'measured_rpm': measured['speed'] * RPM_PER_RAD_S,
                        'target_rpm': self.reference(t) * RPM_PER_RAD_S,
                        'current': math.hypot(i_ds, i_qs), 'torque': out.get('torque', 0.0),
                        'ids': i_ds, 'iqs': i_qs, 'idr': i_dr, 'iqr': i_qr,
                        'ia': i_ds, 'ib': -0.5 * i_ds + SQRT3 / 2.0 * i_qs,
                        'ic': -0.5 * i_ds - SQRT3 / 2.0 * i_qs,
                        'psi_r': out.get('psi_r', 0.0), 'slip': out.get('slip', 0.0),
                        'voltage': math.hypot(control[0], 0.0)})
        else:
            i_d, i_q, omega, theta = state
            row.update({'rpm': omega * RPM_PER_RAD_S, 'measured_rpm': measured['speed'] * RPM_PER_RAD_S,
                        'target_rpm': self.reference(t) * RPM_PER_RAD_S,
                        'id': i_d, 'iq': i_q, 'current': math.hypot(i_d, i_q),
                        'torque': out.get('torque', 0.0), 'theta_e': self.params['p'] * theta,
                        'voltage': math.hypot(control[0], control[1]),
                        'ia': _phase_a(i_d, i_q, self.params['p'] * theta),
                        'ib': _phase_b(i_d, i_q, self.params['p'] * theta),
                        'ic': _phase_c(i_d, i_q, self.params['p'] * theta)})
        row['temperature_c'] = self.bus.temperature_c
        row['vbus'] = self.vbus_now
        return row

    # -------------------------------------------------------------- 指标
    def _metrics(self, series):
        if not series:
            return {}
        times = [r['t'] for r in series]
        m = {}
        position_like = self.controller in ('position', 'current')
        if 'rpm' in series[0] and not position_like:
            speed = [r.get('rpm', 0.0) for r in series]
            target = [r.get('target_rpm', 0.0) for r in series]
            m['final_rpm'] = speed[-1]
            m['measured_final_rpm'] = series[-1].get('measured_rpm', speed[-1])
            m['target_rpm'] = target[-1]
            m['overshoot_percent'] = mt.percent_overshoot(speed, target[-1])
            m['settling_time_s'] = mt.settling_time(times, speed, target[-1])
            m['steady_state_error_rpm'] = mt.steady_state_error(target[-1], speed)
            err = mt.tracking_error(times, target, speed)
            m['tracking_rms_rpm'] = err['rms']
            m['peak_current_a'] = mt.peak([r.get('current', 0.0) for r in series])
            m['rms_current_a'] = mt.rms([r.get('current', 0.0) for r in series])
            m['peak_torque_nm'] = mt.peak([r.get('torque', 0.0) for r in series])
            m['peak_voltage_v'] = mt.peak([r.get('voltage', 0.0) for r in series])
            m['final_temperature_c'] = series[-1].get('temperature_c')
        if 'id' in series[0]:
            m['final_id'] = series[-1].get('id')
            m['final_iq'] = series[-1].get('iq')
        if self.controller == 'position':
            m['final_position_rad'] = series[-1].get('theta_m', 0.0)
            m['target_position_rad'] = self.target
            m['position_error_rad'] = self.target - series[-1].get('theta_m', 0.0)
        if self.controller == 'current':
            key = 'current'
            m['final_current_a'] = series[-1].get(key)
            m['target_current_a'] = self.target
            m['current_error_a'] = self.target - (series[-1].get(key) or 0.0)
        if self.kind == 'induction':
            m['final_slip_rad_s'] = series[-1].get('slip')
            m['final_psi_r'] = series[-1].get('psi_r')
            m['synchronous_rpm'] = self.machine.synchronous_speed() * RPM_PER_RAD_S
        if 'x' in series[0]:
            m['path_max_error_m'] = max(math.hypot(r['x'] - _path_point(self, r['s'])[0],
                                                  r['y'] - _path_point(self, r['s'])[1])
                                        for r in series if 's' in r) if 's' in series[0] else None
            m['odom_max_error_m'] = max(math.hypot(r['x'] - r['odom_x'], r['y'] - r['odom_y'])
                                        for r in series)
            m['odom_final_error_m'] = math.hypot(series[-1]['x'] - series[-1]['odom_x'],
                                                 series[-1]['y'] - series[-1]['odom_y'])
            m['final_x'] = series[-1]['x']
            m['final_y'] = series[-1]['y']
        return m


def _path_point(setup, s):
    try:
        return setup.path_obj.point(s)[:2]
    except Exception:
        return (0.0, 0.0)


def _all_finite(values):
    for value in values:
        if not math.isfinite(value):
            return False
    return True


def _safe_round(value, digits=6):
    """把无穷/NaN 挡在返回值之外：JSON 里不允许出现它们。"""
    if not math.isfinite(value):
        return None
    return round(value, digits)


def _phase_a(i_d, i_q, theta):
    v_alpha, v_beta = inverter.dq_to_alphabeta(i_d, i_q, theta)
    return inverter.alphabeta_to_abc(v_alpha, v_beta)[0]


def _phase_b(i_d, i_q, theta):
    v_alpha, v_beta = inverter.dq_to_alphabeta(i_d, i_q, theta)
    return inverter.alphabeta_to_abc(v_alpha, v_beta)[1]


def _phase_c(i_d, i_q, theta):
    v_alpha, v_beta = inverter.dq_to_alphabeta(i_d, i_q, theta)
    return inverter.alphabeta_to_abc(v_alpha, v_beta)[2]


def _prune(row, keys):
    return {key: row[key] for key in keys if key in row}


def _frequency_schedule(setup):
    """V/f 与开环场景的频率时间表：从低频爬升到额定频率，避免直接全频冲击。"""
    f_nom = setup.params.get('f', 50.0)
    duration = setup.duration
    scenario = setup.scenario

    def at(t):
        frac = min(1.0, max(0.0, t / max(duration, 1e-9)))
        if scenario == 'vf_scan':
            start = setup.payload('f_start_hz', 2.0)
            stop = setup.payload('f_stop_hz', f_nom)
            return start + (stop - start) * frac
        return f_nom * (0.05 + 0.95 * min(1.0, frac / 0.15))

    return at


def simulate(cfg):
    """校验后的配置 → 一次完整仿真。"""
    return Setup(cfg).run()
