"""电机模型：直流机、三相异步机、永磁同步机 / 无刷直流机。

课程作者：Connor He 和 Astra。

坐标与单位约定（全课程一致，测试会强制检查）：

* 全部使用 SI 单位：V、A、N·m、rad/s（电角速度也是 rad/s）、H、Wb、kg·m²。
* 直流机状态 ``(i, ω)``，控制输入 ``(u, τL)``。
* 异步机在**定子静止 dq 坐标系**下建模，状态
  ``(i_ds, i_qs, i_dr, i_qr, ω)``，输入 ``(u_ds, u_qs, τL)``。
  转子方程中的 ``ω_s`` 项来自坐标系相对转子的转速（定子坐标系→转子坐标系）。
* 永磁机在**转子 dq 坐标系**下建模，状态 ``(i_d, i_q, ω, θ_m)``，
  输入 ``(u_d, u_q, τL)``；``θ_m`` 是机械角，用于编码器模型。
* 转矩常数使用公制一致定义：``Te = (3/2)·p·λm·i_q``（表贴式），
  异步机 ``Te = (3/2)·p·Lm·(i_qs·i_dr − i_ds·i_qr)``。
* 负载转矩沿用姊妹项目语义：**正值表示与当前转向相反的外转矩**（阻力型），
  在积分中逐子步按下式计算，而不是"无论转向都阻碍旋转"的摩擦制动器。

所有模型都是无副作用的纯函数对象：``deriv`` 只返回导数，不修改状态。
"""
from __future__ import annotations

import math

__all__ = ['DCMachine', 'InductionMachine', 'PMSMMachine', 'make_machine', 'Machine',
           'STATE_KEYS', 'INPUT_KEYS']

STATE_KEYS = {
    'dc': ('current', 'omega'),
    'induction': ('ids', 'iqs', 'idr', 'iqr', 'omega'),
    'pmsm_bldc': ('id', 'iq', 'omega', 'theta_m'),
}

#: 输入布局。**只列出控制器产生的控制量**，负载转矩是工况给出的扰动，
#: 由仿真引擎在每子步按 ``control + (load,)`` 拼成完整的输入向量。
INPUT_KEYS = {
    'dc': ('u',),
    'induction': ('uds', 'uqs'),
    'pmsm_bldc': ('ud', 'uq'),
}

#: 控制量个数（便于引擎校验拼接长度）。
CONTROL_WIDTH = {'dc': 1, 'induction': 2, 'pmsm_bldc': 2}

_EPS = 1e-12


def _load_torque(load, omega):
    """阻力型负载：正值总与当前转向相反；零速时按负载符号取其自身方向。

    这样"加 0.05 N·m 负载"在正转时是阻力、反转时也是阻力，
    不会出现负载把电机越推越快的情况。
    """
    if omega > 1e-9:
        return load
    if omega < -1e-9:
        return -load
    return load


class Machine:
    """电机基类。子类实现 ``deriv``，并声明输入布局。"""

    kind = 'none'
    state_keys = ()
    input_keys = ()
    #: 与状态一一对应的名称，前端画图时用
    display = ()
    units = ()

    def __init__(self, params):
        self.params = dict(params)
        #: 机械转子锁定。``True`` 时机械方程退化为 ``dω/dt = 0``，
        #: 用于"堵转"实验：电流环只应在转子不动时考察，否则转子一转，
        #: dq 电流就按电频率振荡，看起来像电流环坏了。这是显式声明的
        #: 实验条件，不是偷偷改物理。
        self.rotor_locked = False

    def initial_state(self):
        return tuple(0.0 for _ in self.state_keys)

    def deriv(self, state, control):
        raise NotImplementedError

    def outputs(self, state, control):
        """派生量（转矩、功率、反电势…），用于指标与绘图。"""
        return {}

    def omega(self, state):
        return state[self.state_keys.index('omega')]

    def describe(self):
        return {'kind': self.kind, 'state_keys': list(self.state_keys),
                'input_keys': list(self.input_keys), 'display': list(self.display),
                'units': list(self.units), 'params': dict(self.params)}


class DCMachine(Machine):
    """他励直流机：``L·di/dt = u − R·i − Ke·ω``，``J·dω/dt = Kt·i − b·ω − τL``。"""

    kind = 'dc'
    state_keys = ('current', 'omega')
    input_keys = ('u',)
    display = ('电枢电流', '机械角速度')
    units = ('A', 'rad/s')

    def deriv(self, state, control):
        i, omega = state
        u, load = control
        p = self.params
        di = (u - p['R'] * i - p['Ke'] * omega) / p['L']
        if self.rotor_locked:
            return (di, 0.0)
        dw = (p['Kt'] * i - p['b'] * omega - _load_torque(load, omega)) / p['J']
        return (di, dw)

    def outputs(self, state, control):
        i, omega = state
        p = self.params
        return {'torque': p['Kt'] * i, 'back_emf': p['Ke'] * omega,
                'power_in': control[0] * i, 'power_mech': p['Kt'] * i * omega}

    def steady_state(self, u, load=0.0):
        """解析稳态：给定电压与阻力负载，求 ``(i, ω)``。

        由 ``Kt·i = b·ω + τL`` 与 ``u = R·i + Ke·ω`` 联立求解。
        """
        p = self.params
        denom = p['R'] * p['b'] + p['Kt'] * p['Ke']
        omega = (p['Kt'] * u - p['R'] * load) / denom
        i = (p['b'] * u + p['Ke'] * load) / denom
        return i, omega

    def electrical_tau(self):
        return self.params['L'] / self.params['R']

    def mechanical_tau(self):
        return self.params['J'] / self.params['b'] if self.params['b'] > 0 else math.inf


class InductionMachine(Machine):
    """三相异步机 dq 动态模型（定子静止坐标系，转子量已折算）。

    **状态取磁链而不是电流**。这是标准做法，也是数值上唯一稳妥的做法：
    电压方程本来就写成磁链导数，积分前不必先反解电流；积分后再由代数
    关系 ``i = L⁻¹·ψ`` 得到电流，只是"读出来"，不会把代数误差重新喂回积分。

    ``ω_s`` 是**同步机械角速度** ``ω_s = 2πf/p``（不是电角速度！）。
    定子方程里的旋转项用电角速度 ``p·ω_s``；转子方程的旋转项是转差对应的
    电角速度 ``p·(ω_s − ω)``：

        dψ_ds/dt = u_ds − Rs·i_ds + p·ω_s·ψ_qs
        dψ_qs/dt = u_qs − Rs·i_qs − p·ω_s·ψ_ds
        dψ_dr/dt = −Rr·i_dr + p·(ω_s − ω)·ψ_qr
        dψ_qr/dt = −Rr·i_qr − p·(ω_s − ω)·ψ_dr

    把极对数漏掉是异步机建模最常见的一个错误，后果是"转速能跑到同步转速
    以上还不发电"。测试会用 ``synchronous_speed()`` 与转矩—转差曲线卡住它。
    """

    kind = 'induction'
    state_keys = ('psi_ds', 'psi_qs', 'psi_dr', 'psi_qr', 'omega')
    input_keys = ('uds', 'uqs')
    display = ('定子 d 轴磁链', '定子 q 轴磁链', '转子 d 轴磁链', '转子 q 轴磁链', '机械角速度')
    units = ('Wb', 'Wb', 'Wb', 'Wb', 'rad/s')

    def __init__(self, params, omega_s=0.0):
        super().__init__(params)
        self.omega_s = float(omega_s)
        p = self.params
        det = p['Ls'] * p['Lr'] - p['Lm'] ** 2
        if abs(det) < _EPS:
            raise ValueError('互感参数导致电感矩阵奇异，请检查 Ls、Lr、Lm。')
        # i = L⁻¹·ψ，把 4×4 分块对角矩阵的逆先算好
        self._inv = (p['Lr'] / det, -p['Lm'] / det, -p['Lm'] / det, p['Ls'] / det)
        # 允许出现的最大电流（用于如实警告，不作为"保护"参与计算）
        self.current_limit = float(params.get('i_max', 6.0 * math.sqrt(2.0)))

    def set_omega_s(self, omega_s):
        self.omega_s = float(omega_s)

    def currents(self, state):
        """由磁链状态读出四个电流：``i = L⁻¹·ψ``。"""
        psi_ds, psi_qs, psi_dr, psi_qr, _ = state
        a, b, c, d = self._inv
        return (a * psi_ds + b * psi_dr, a * psi_qs + b * psi_qr,
                c * psi_ds + d * psi_dr, c * psi_qs + d * psi_qr)

    def flux(self, state):
        return state[:4]

    def deriv(self, state, control):
        psi_ds, psi_qs, psi_dr, psi_qr, omega = state
        u_ds, u_qs, load = control
        p = self.params
        i_ds, i_qs, i_dr, i_qr = self.currents(state)
        p_pole = p['p']
        we = p_pole * self.omega_s          # 电角速度（定子频率）
        we_slip = p_pole * (self.omega_s - omega)   # 转差电角速度
        dpsi_ds = u_ds - p['Rs'] * i_ds + we * psi_qs
        dpsi_qs = u_qs - p['Rs'] * i_qs - we * psi_ds
        dpsi_dr = -p['Rr'] * i_dr + we_slip * psi_qr
        dpsi_qr = -p['Rr'] * i_qr - we_slip * psi_dr
        te = 1.5 * p_pole * p['Lm'] * (i_qs * i_dr - i_ds * i_qr)
        if self.rotor_locked:
            return (dpsi_ds, dpsi_qs, dpsi_dr, dpsi_qr, 0.0)
        dw = (te - p['b'] * omega - _load_torque(load, omega)) / p['J']
        return (dpsi_ds, dpsi_qs, dpsi_dr, dpsi_qr, dw)

    def outputs(self, state, control):
        i_ds, i_qs, i_dr, i_qr = self.currents(state)
        psi_ds, psi_qs, psi_dr, psi_qr = state[:4]
        p = self.params
        te = 1.5 * p['p'] * p['Lm'] * (i_qs * i_dr - i_ds * i_qr)
        omega = state[4]
        return {'torque': te, 'psi_r': math.hypot(psi_dr, psi_qr),
                'psi_s': math.hypot(psi_ds, psi_qs), 'slip': self.omega_s - omega,
                'slip_ratio': (self.omega_s - omega) / self.omega_s if abs(self.omega_s) > 1e-9 else 0.0,
                'current_mag': max(abs(i_ds), abs(i_qs), abs(i_dr), abs(i_qr))}

    def synchronous_speed(self):
        """同步机械角速度 ``ω_s = 2πf / p``（rad/s），即理论空载转速。"""
        return 2.0 * math.pi * self.params['f'] / self.params['p']

    def torque_slip_curve(self, voltage_rms, points=240, slip_max=1.0):
        """稳态转矩—转差曲线（每相有效值电压，忽略铁损）。

        三相总电磁功率除以同步机械角速度：

            T = 3·U_ph²·(Rr/s) / [ω_s·((Rs + Rr/s)² + (Xls + Xlr)²)]

        这是《电机拖动》里那张经典曲线，用来给动态仿真做独立对照。
        """
        p = self.params
        ws_sync = self.synchronous_speed()
        if ws_sync <= 0:
            raise ValueError('同步转速必须为正。')
        Xls = (p['Ls'] - p['Lm']) * ws_sync
        Xlr = (p['Lr'] - p['Lm']) * ws_sync
        curve = []
        for index in range(points + 1):
            t = index / points
            # 用对数分布让转差接近 0 的区域也有足够分辨率
            if index == 0:
                s = 1e-6
            else:
                s = slip_max * (10.0 ** (-6.0 * (1.0 - t)))
            if s <= 0:
                continue
            r = p['Rr'] / s
            denom = (p['Rs'] + r) ** 2 + (Xls + Xlr) ** 2
            torque = 3.0 * voltage_rms ** 2 * r / (ws_sync * denom)
            curve.append((s, torque, ws_sync * (1.0 - s)))
        return curve

    def max_torque_slip(self, voltage_rms):
        """产生最大转矩的转差（解析式 ``s_m = Rr/√(Rs²+(Xls+Xlr)²)``）。"""
        p = self.params
        ws_sync = self.synchronous_speed()
        X = (p['Ls'] - p['Lm']) * ws_sync + (p['Lr'] - p['Lm']) * ws_sync
        return p['Rr'] / math.sqrt(p['Rs'] ** 2 + X ** 2)


class PMSMMachine(Machine):
    """永磁同步机（可退化为表贴式）与无刷直流机的 dq 模型。

    ``Ld = Lq`` 时为表贴式；``bridge='pwm'`` 且 ``scenario`` 为六步模式时
    由 ``SixStepController`` 驱动，用于和 FOC 做对照。
    """

    kind = 'pmsm_bldc'
    state_keys = ('id', 'iq', 'omega', 'theta_m')
    input_keys = ('ud', 'uq')
    display = ('d 轴电流', 'q 轴电流', '机械角速度', '机械角位置')
    units = ('A', 'A', 'rad/s', 'rad')

    def deriv(self, state, control):
        i_d, i_q, omega, _theta = state
        u_d, u_q, load = control
        p = self.params
        p_pole = p['p']
        we = p_pole * omega
        did = (u_d - p['Rs'] * i_d + we * p['Lq'] * i_q) / p['Ld']
        diq = (u_q - p['Rs'] * i_q - we * p['Ld'] * i_d - we * p['lambda_m']) / p['Lq']
        te = 1.5 * p_pole * (p['lambda_m'] * i_q + (p['Ld'] - p['Lq']) * i_d * i_q)
        if self.rotor_locked:
            # 只冻结加速度：位置仍由积分器按当前 ω 推进
            return (did, diq, 0.0, omega)
        dw = (te - p['b'] * omega - _load_torque(load, omega)) / p['J']
        return (did, diq, dw, omega)

    def outputs(self, state, control):
        i_d, i_q, omega, _theta = state
        p = self.params
        p_pole = p['p']
        te = 1.5 * p_pole * (p['lambda_m'] * i_q + (p['Ld'] - p['Lq']) * i_d * i_q)
        return {'torque': te, 'back_emf_q': p_pole * omega * p['lambda_m'],
                'current_mag': math.hypot(i_d, i_q),
                'electrical_freq_hz': abs(p_pole * omega) / (2.0 * math.pi)}

    def torque_from_iq(self, i_q):
        """表贴式近似下的转矩，用于 FOC 理论对照。"""
        return 1.5 * self.params['p'] * self.params['lambda_m'] * i_q


def make_machine(kind, params, omega_s=0.0):
    if kind == 'dc':
        return DCMachine(params)
    if kind == 'induction':
        return InductionMachine(params, omega_s=omega_s)
    if kind == 'pmsm_bldc':
        return PMSMMachine(params)
    raise ValueError(f'未知电机类型：{kind}')
