"""电机模型与控制器件的单元测试。

课程作者：Connor He 和 Astra。

每个模型都要和"纸笔能算出来的答案"对上，或者和一条独立的解析曲线对上。
这些断言是课程信誉的底线：曲线不是画出来好看的，是算出来的。
"""
import math
import unittest

from server.sim import control as ctl
from server.sim import inverter
from server.sim import metrics
from server.sim import integrate
from server.sim.machines import DCMachine, InductionMachine, PMSMMachine, make_machine, CONTROL_WIDTH

RPM_PER_RAD_S = 60.0 / (2.0 * math.pi)

DC_PARAMS = {'R': 2.0, 'L': 0.002, 'J': 2e-4, 'b': 1e-4, 'Kt': 0.05, 'Ke': 0.05}
# 面向 24 V 母线的小功率异步机
IM_PARAMS = {'Rs': 0.5, 'Rr': 0.4, 'Ls': 0.012, 'Lr': 0.012, 'Lm': 0.0112,
             'J': 5e-3, 'b': 1e-3, 'f': 50.0, 'p': 2}
PMSM_PARAMS = {'Rs': 0.4, 'Ld': 0.001, 'Lq': 0.001, 'Lm': 0.001, 'lambda_m': 0.012,
               'J': 2e-4, 'b': 1e-4, 'p': 4, 'Kt': 0.0717, 'Ke': 0.0717}


class DCMachineTest(unittest.TestCase):
    def setUp(self):
        self.machine = DCMachine(DC_PARAMS)

    def test_analytic_steady_state_satisfies_both_equations(self):
        """解析稳态必须同时满足电枢方程与转矩平衡。"""
        p = DC_PARAMS
        for voltage, load in ((12.0, 0.0), (24.0, 0.05), (6.0, 0.01)):
            current, omega = self.machine.steady_state(voltage, load)
            self.assertAlmostEqual(p['R'] * current + p['Ke'] * omega, voltage, places=9)
            self.assertAlmostEqual(p['Kt'] * current, p['b'] * omega + load, places=9)

    def test_steady_state_is_an_equilibrium_point(self):
        """把解析稳态当初始状态，导数应当为零。"""
        current, omega = self.machine.steady_state(12.0, 0.03)
        d_i, d_w = self.machine.deriv((current, omega), (12.0, 0.03))
        self.assertAlmostEqual(d_i, 0.0, places=6)
        self.assertAlmostEqual(d_w, 0.0, places=6)

    def test_open_loop_integration_converges_to_analytic_solution(self):
        """开环积分 2 秒后收敛到解析稳态（这是"仿真不是动画"的核心证据）。"""
        state, t, step = (0.0, 0.0), 0.0, 1e-4
        for _ in range(20_000):
            state = integrate.rk4_step(lambda tt, ss: self.machine.deriv(ss, (12.0, 0.0)), t, state, step)
            t += step
        current, omega = self.machine.steady_state(12.0, 0.0)
        self.assertAlmostEqual(state[0], current, places=4)
        self.assertAlmostEqual(state[1], omega, places=2)

    def test_step_size_convergence(self):
        """子步缩小 4 倍，结果变化应当很小（说明 0.1 ms 已进入收敛区）。"""
        def final(step):
            state, t = (0.0, 0.0), 0.0
            n = int(round(0.5 / step))
            for _ in range(n):
                state = integrate.rk4_step(lambda tt, ss: self.machine.deriv(ss, (12.0, 0.02)), t, state, step)
                t += step
            return state
        coarse = final(1e-4)
        fine = final(2.5e-5)
        self.assertLess(abs(coarse[1] - fine[1]) / abs(fine[1]), 0.005)
        self.assertLess(abs(coarse[0] - fine[0]) / abs(fine[0]), 0.005)

    def test_time_constants(self):
        self.assertAlmostEqual(self.machine.electrical_tau(), 1e-3, places=12)
        self.assertAlmostEqual(self.machine.mechanical_tau(), 2.0, places=9)

    def test_resistive_load_always_opposes_rotation(self):
        """阻力型负载：正转时为正、反转时为负，永不变成驱动源。"""
        forward = self.machine.deriv((0.0, 10.0), (0.0, 0.05))
        reverse = self.machine.deriv((0.0, -10.0), (0.0, 0.05))
        self.assertLess(forward[1], 0.0)
        self.assertGreater(reverse[1], 0.0)

    def test_zero_speed_load_direction_is_deterministic(self):
        self.assertLess(self.machine.deriv((0.0, 0.0), (0.0, 0.05))[1], 0.0)


class InductionMachineTest(unittest.TestCase):
    def setUp(self):
        self.machine = InductionMachine(IM_PARAMS)

    def test_synchronous_speed_includes_pole_pairs(self):
        """n_s = 60f/p。漏掉极对数是异步机建模最常见的错误。"""
        self.assertAlmostEqual(self.machine.synchronous_speed(),
                               2 * math.pi * 50.0 / 2, places=9)
        self.assertAlmostEqual(self.machine.synchronous_speed() * RPM_PER_RAD_S, 1500.0, places=6)

    def test_torque_slip_curve_is_zero_at_synchronous_speed(self):
        curve = self.machine.torque_slip_curve(13.0, points=40)
        smallest = min(curve, key=lambda point: point[0])
        self.assertLess(abs(smallest[1]), 1e-3)
        self.assertAlmostEqual(smallest[2], self.machine.synchronous_speed(), delta=1e-2)

    def test_torque_slip_curve_matches_air_gap_formula(self):
        """独立复算：T = 3·U²·(Rr/s) / [ω_s·((Rs+Rr/s)² + (Xls+Xlr)²)]。"""
        voltage_rms = 13.0
        ws = self.machine.synchronous_speed()
        x_sum = (IM_PARAMS['Ls'] - IM_PARAMS['Lm']) * ws + (IM_PARAMS['Lr'] - IM_PARAMS['Lm']) * ws
        s = 0.2
        expected = 3.0 * voltage_rms ** 2 * (IM_PARAMS['Rr'] / s) / (
            ws * ((IM_PARAMS['Rs'] + IM_PARAMS['Rr'] / s) ** 2 + x_sum ** 2))
        curve = self.machine.torque_slip_curve(voltage_rms, points=4000)
        closest = min(curve, key=lambda point: abs(point[0] - s))
        self.assertLess(abs(closest[0] - s), 1e-3)
        self.assertAlmostEqual(closest[1], expected, delta=abs(expected) * 0.02)

    def test_max_torque_slip_formula(self):
        ws = self.machine.synchronous_speed()
        x_sum = (IM_PARAMS['Ls'] - IM_PARAMS['Lm']) * ws + (IM_PARAMS['Lr'] - IM_PARAMS['Lm']) * ws
        expected = IM_PARAMS['Rr'] / math.sqrt(IM_PARAMS['Rs'] ** 2 + x_sum ** 2)
        self.assertAlmostEqual(self.machine.max_torque_slip(13.0), expected, places=12)

    def test_dynamic_model_settles_below_synchronous_speed(self):
        """开环给定额定频率与电压：稳态机械角速度必须**低于**同步转速。"""
        self.machine.set_omega_s(self.machine.synchronous_speed())
        state, t, step = self.machine.initial_state(), 0.0, 1e-4
        voltage = 13.0
        for _ in range(60_000):
            state = integrate.rk4_step(lambda tt, ss: self.machine.deriv(ss, (voltage, 0.0, 0.0)), t, state, step)
            t += step
        omega = state[4]
        self.assertLess(omega, self.machine.synchronous_speed())
        self.assertGreater(omega, 0.85 * self.machine.synchronous_speed())

    def test_steady_torque_balances_damping(self):
        self.machine.set_omega_s(self.machine.synchronous_speed())
        state, t, step = self.machine.initial_state(), 0.0, 1e-4
        for _ in range(60_000):
            state = integrate.rk4_step(lambda tt, ss: self.machine.deriv(ss, (13.0, 0.0, 0.0)), t, state, step)
            t += step
        torque = self.machine.outputs(state, (13.0, 0.0, 0.0))['torque']
        self.assertAlmostEqual(torque, IM_PARAMS['b'] * state[4], delta=abs(torque) * 0.05)

    def test_currents_are_consistent_with_flux_state(self):
        state = (0.01, -0.005, 0.004, 0.002, 10.0)
        i_ds, i_qs, i_dr, i_qr = self.machine.currents(state)
        p = IM_PARAMS
        self.assertAlmostEqual(p['Ls'] * i_ds + p['Lm'] * i_dr, state[0], places=12)
        self.assertAlmostEqual(p['Lr'] * i_qr + p['Lm'] * i_qs, state[3], places=12)

    def test_rotor_lock_freezes_speed(self):
        self.machine.rotor_locked = True
        derivative = self.machine.deriv((0.01, 0.0, 0.0, 0.0, 0.0), (13.0, 0.0, 0.0))
        self.assertEqual(derivative[4], 0.0)

    def test_rejects_singular_inductance_matrix(self):
        with self.assertRaises(ValueError):
            InductionMachine({'Rs': 1.0, 'Rr': 1.0, 'Ls': 1.0, 'Lr': 1.0, 'Lm': 1.0,
                              'J': 1e-3, 'b': 0.0, 'f': 50.0, 'p': 2})


class PMSMMachineTest(unittest.TestCase):
    def setUp(self):
        self.machine = PMSMMachine(PMSM_PARAMS)

    def test_torque_constant_relation(self):
        """表贴式永磁机：Te = 1.5·p·λm·iq。"""
        p = PMSM_PARAMS
        for i_q in (0.0, 1.0, 2.5, -3.0):
            state = (0.0, i_q, 0.0, 0.0)
            torque = self.machine.outputs(state, (0.0, 0.0, 0.0))['torque']
            self.assertAlmostEqual(torque, 1.5 * p['p'] * p['lambda_m'] * i_q, places=12)

    def test_salient_machine_adds_reluctance_torque(self):
        params = dict(PMSM_PARAMS, Ld=0.0008, Lq=0.0016)
        machine = PMSMMachine(params)
        torque = machine.outputs((2.0, 1.0, 0.0, 0.0), (0.0, 0.0, 0.0))['torque']
        self.assertAlmostEqual(torque, 1.5 * params['p'] * (params['lambda_m'] * 1.0
                                                            + (params['Ld'] - params['Lq']) * 2.0 * 1.0),
                               places=12)

    def test_rotor_lock_zeroes_mechanical_rate(self):
        self.machine.rotor_locked = True
        derivative = self.machine.deriv((0.0, 2.0, 0.0, 0.0), (0.0, 0.8, 0.0))
        self.assertEqual(derivative[2], 0.0)
        self.assertEqual(derivative[3], 0.0)

    def test_position_state_integrates_speed(self):
        """堵转 + 零转矩时速度恒定，位置必须严格是 ω·t。"""
        self.machine.rotor_locked = True
        self.machine.params['lambda_m'] = 0.0        # 零转矩，速度保持不变
        self.machine.params['Ld'] = self.machine.params['Lq']
        state, t, step = (0.0, 0.0, 5.0, 0.0), 0.0, 1e-4
        for _ in range(1000):
            state = integrate.rk4_step(lambda tt, ss: self.machine.deriv(ss, (0.0, 0.0, 0.0)), t, state, step)
            t += step
        self.assertAlmostEqual(state[3], 5.0 * t, places=6)
        self.assertAlmostEqual(state[2], 5.0, places=9)


class FactoryTest(unittest.TestCase):
    def test_make_machine_dispatch(self):
        self.assertIsInstance(make_machine('dc', DC_PARAMS), DCMachine)
        self.assertIsInstance(make_machine('induction', IM_PARAMS), InductionMachine)
        self.assertIsInstance(make_machine('pmsm_bldc', PMSM_PARAMS), PMSMMachine)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            make_machine('stepper', {})

    def test_control_width_matches_declared_input_keys(self):
        for kind, params in (('dc', DC_PARAMS), ('induction', IM_PARAMS), ('pmsm_bldc', PMSM_PARAMS)):
            machine = make_machine(kind, params)
            self.assertEqual(len(machine.input_keys), {'dc': 1, 'induction': 2, 'pmsm_bldc': 2}[kind])
            self.assertEqual(len(machine.input_keys), CONTROL_WIDTH[kind])
            # deriv 必须接受 control + (load,) 的长度
            state = machine.initial_state()
            control = (0.0,) * len(machine.input_keys) + (0.0,)
            derivative = machine.deriv(state, control)
            self.assertEqual(len(derivative), len(machine.state_keys))


class PITest(unittest.TestCase):
    def _closed_loop(self, kp, ki, aw=True, limit=5.0, tau=0.05, steps=4000, setpoint=1.0):
        y, controller, dt = 0.0, ctl.PI(kp, ki, limit, aw), 1e-3
        for _ in range(steps):
            u = controller.step(setpoint - y, dt)
            y += (u - y) * dt / tau
        return y, controller

    def test_tracks_setpoint_with_integral_action(self):
        for kp, ki in ((40.0, 800.0), (10.0, 200.0), (1.0, 20.0)):
            y, _ = self._closed_loop(kp, ki)
            self.assertAlmostEqual(y, 1.0, places=6)

    def test_output_respects_limit(self):
        controller = ctl.PI(1000.0, 0.0, limit=2.5)
        self.assertEqual(controller.step(10.0, 1e-3), 2.5)
        self.assertTrue(controller.saturated)

    def test_anti_windup_prevents_integral_runaway(self):
        """饱和期间错误继续同向：抗饱和开启时积分不应无限增长。"""
        on = ctl.PI(1.0, 100.0, limit=1.0, anti_windup=True)
        off = ctl.PI(1.0, 100.0, limit=1.0, anti_windup=False)
        for _ in range(2000):
            on.step(1.0, 1e-3)
            off.step(1.0, 1e-3)
        self.assertLess(abs(on.integral), 2.0)
        self.assertGreater(abs(off.integral), 100.0)

    def test_recovers_quickly_after_saturation(self):
        """抗饱和控制器退出饱和所需时间应显著短于不抗饱和的情形。"""
        def recovery(aw):
            controller = ctl.PI(1.0, 100.0, limit=1.0, anti_windup=aw)
            for _ in range(3000):
                controller.step(1.0, 1e-3)
            for step in range(1, 6000):
                if controller.step(-1.0, 1e-3) < 0.5:
                    return step
            return 10_000
        self.assertLess(recovery(True), recovery(False))

    def test_rejects_non_finite_error(self):
        controller = ctl.PI(1.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            controller.step(float('nan'), 1e-3)
        with self.assertRaises(ValueError):
            controller.step(1.0, 0.0)

    def test_reset_clears_state(self):
        controller = ctl.PI(1.0, 100.0, 5.0)
        controller.step(1.0, 1e-3)
        controller.reset()
        self.assertEqual(controller.integral, 0.0)
        self.assertFalse(controller.saturated)


class EncoderTest(unittest.TestCase):
    def test_counts_are_quantised(self):
        encoder = ctl.Encoder(lines=1000)
        step = encoder.quant_step_rad()
        self.assertAlmostEqual(encoder.position_rad(step * 0.4), 0.0)
        self.assertAlmostEqual(encoder.position_rad(step * 0.6), step)

    def test_velocity_from_count_difference_is_exact_at_slow_speed(self):
        encoder = ctl.Encoder(lines=1000)
        step = encoder.quant_step_rad()
        period = 1e-3
        # 每周期恰好跨过一个计数
        velocity = encoder.velocity(step, 0.0, period)
        self.assertAlmostEqual(velocity, step / period, places=6)

    def test_reversed_encoder_inverts_sign(self):
        normal = ctl.Encoder(lines=1000)
        reversed_ = ctl.Encoder(lines=1000, faults=('encoder_reversed',))
        angle = 1.234
        self.assertAlmostEqual(reversed_.position_rad(angle), -normal.position_rad(angle), places=12)

    def test_units_confusion_scales_by_degrees(self):
        """量纲混淆：把弧度当角度，90° 会被读成 1.57° 左右。"""
        normal = ctl.Encoder(lines=1000)
        confused = ctl.Encoder(lines=1000, faults=('units_confusion',))
        angle = math.pi / 2.0
        scaled = confused.position_rad(angle)
        self.assertAlmostEqual(scaled, math.radians(math.pi / 2.0),
                               delta=confused.quant_step_rad())
        self.assertLess(abs(scaled), abs(normal.position_rad(angle)))

    def test_resolution_scales_with_lines(self):
        self.assertLess(ctl.Encoder(lines=4000).quant_step_rad(),
                        ctl.Encoder(lines=1000).quant_step_rad())

    def test_rejects_invalid_lines(self):
        with self.assertRaises(ValueError):
            ctl.Encoder(lines=0)


class DifferentialDriveTest(unittest.TestCase):
    def setUp(self):
        self.drive = ctl.DifferentialDrive(track=0.4, wheel_radius=0.0625, gear=1.0)

    def test_inverse_then_forward_round_trip(self):
        for v, omega in ((1.0, 0.0), (0.0, 2.0), (1.5, -0.8), (-0.4, 0.3)):
            w_l, w_r = self.drive.inverse(v, omega)
            back_v, back_omega = self.drive.forward(w_l, w_r)
            self.assertAlmostEqual(back_v, v, places=12)
            self.assertAlmostEqual(back_omega, omega, places=12)

    def test_straight_line_pose(self):
        pose = (0.0, 0.0, 0.0)
        w = 1.0 * self.drive.gear / self.drive.wheel_radius
        for _ in range(1000):
            pose = self.drive.step_pose(pose, w, w, 1e-3)
        self.assertAlmostEqual(pose[0], 1.0, places=6)
        self.assertAlmostEqual(pose[1], 0.0, places=9)
        self.assertAlmostEqual(pose[2], 0.0, places=9)

    def test_in_place_rotation_keeps_position(self):
        """左右轮反向同速：位置不动，航向按 v/track 变化。"""
        w_l, w_r = 1.0, -1.0
        expected = (w_r - w_l) * self.drive.wheel_radius / self.drive.gear / self.drive.track
        pose = (0.0, 0.0, 0.0)
        for _ in range(1000):
            pose = self.drive.step_pose(pose, w_l, w_r, 1e-3)
        self.assertAlmostEqual(pose[0], 0.0, places=9)
        self.assertAlmostEqual(pose[1], 0.0, places=9)
        self.assertAlmostEqual(pose[2], expected, places=6)

    def test_circle_radius_matches_command(self):
        v, radius = 1.0, 2.0
        omega = v / radius
        pose = (0.0, 0.0, 0.0)
        w_l, w_r = self.drive.inverse(v, omega)
        for _ in range(int(2 * math.pi * radius / v / 1e-3)):
            pose = self.drive.step_pose(pose, w_l, w_r, 1e-3)
        self.assertAlmostEqual(pose[0], 0.0, places=2)
        self.assertAlmostEqual(pose[2], 2 * math.pi, places=2)

    def test_rejects_invalid_geometry(self):
        for kwargs in ({'track': 0.0}, {'wheel_radius': -1.0}, {'gear': 0.0}):
            with self.assertRaises(ValueError):
                ctl.DifferentialDrive(**kwargs)


class PathAndPursuitTest(unittest.TestCase):
    def test_circle_path_is_closed(self):
        path = ctl.Path(shape='circle', radius=1.5)
        start = path.point(0.0)
        finish = path.point(path.closed_loop_length())
        self.assertAlmostEqual(start[0], finish[0], places=9)
        self.assertAlmostEqual(start[1], finish[1], places=9)

    def test_pursuit_keeps_robot_on_circle(self):
        path = ctl.Path(shape='circle', radius=1.5)
        pursuit = ctl.PurePursuit(lookahead=0.4, max_omega=6.0, max_v=3.0)
        drive = ctl.DifferentialDrive(track=0.4, wheel_radius=0.0625, gear=1.0)
        pose = (0.0, 0.0, 0.0)
        s, worst = 0.0, 0.0
        for _ in range(12_000):
            v, omega, _ = pursuit.step(pose, path, s, speed=1.0)
            w_l, w_r = drive.inverse(v, omega)
            pose = drive.step_pose(pose, w_l, w_r, 1e-3)
            s += v * 1e-3
            target = path.point(s)
            if s > 3.0:
                worst = max(worst, math.hypot(pose[0] - target[0], pose[1] - target[1]))
        self.assertLess(worst, 0.35)

    def test_pursuit_rejects_zero_lookahead(self):
        with self.assertRaises(ValueError):
            ctl.PurePursuit(lookahead=0.0)


class FourBarTest(unittest.TestCase):
    def test_returns_solution_for_reachable_angles(self):
        found = False
        for index in range(72):
            angle = 2 * math.pi * index / 72
            result = ctl.four_bar(angle)
            if result is not None:
                found = True
                # 连杆长度约束必须成立
                c = result['coupler']
                b = result['crank']
                d = result['ground']
                self.assertAlmostEqual(math.hypot(c[0] - b[0], c[1] - b[1]), 0.12, places=9)
                self.assertAlmostEqual(math.hypot(c[0] - d[0], c[1] - d[1]), 0.10, places=9)
                self.assertAlmostEqual(math.hypot(b[0], b[1]), 0.04, places=9)
                # 分支固定：连杆铰点必须在机架线之上
                self.assertGreater(c[1], 0.0)
        self.assertTrue(found)

    def test_reports_impossible_assembly(self):
        self.assertIsNone(ctl.four_bar(0.0, crank_len=0.04, coupler_len=0.01,
                                       rocker_len=0.01, ground=0.5))

    def test_limit_switch_states(self):
        self.assertEqual(ctl.limit_switch_state(0.5, 0.0, 1.0)[0], 'ok')
        self.assertEqual(ctl.limit_switch_state(0.0, 0.0, 1.0), ('at_lower', 1.0))
        self.assertEqual(ctl.limit_switch_state(1.0, 0.0, 1.0), ('at_upper', -1.0))
        with self.assertRaises(ValueError):
            ctl.limit_switch_state(0.5, 1.0, 0.0)


class InverterTest(unittest.TestCase):
    def test_linear_limits(self):
        self.assertAlmostEqual(inverter.linear_limit(24.0), 24.0 / math.sqrt(3.0), places=12)
        self.assertAlmostEqual(inverter.sine_limit(24.0), 12.0, places=12)

    def test_clamp_respects_svpwm_limit(self):
        vdc = 24.0
        limit = inverter.linear_limit(vdc)
        alpha, beta, mod_index, limited = inverter.clamp_vector(limit * 3.0, 0.0, vdc, 'svpwm')
        self.assertTrue(limited)
        self.assertAlmostEqual(math.hypot(alpha, beta), limit, places=9)
        self.assertAlmostEqual(mod_index, 1.0, places=9)

    def test_sine_pwm_limit_is_narrower(self):
        vdc = 24.0
        alpha, beta, _, limited = inverter.clamp_vector(0.0, 13.0, vdc, 'pwm')
        self.assertTrue(limited)
        self.assertAlmostEqual(beta, 12.0, places=9)

    def test_svpwm_beats_sine_pwm_by_15_percent(self):
        self.assertAlmostEqual(inverter.linear_limit(24.0) / inverter.sine_limit(24.0),
                               2.0 / math.sqrt(3.0), places=9)

    def test_ideal_mode_does_not_clamp(self):
        alpha, beta, _, limited = inverter.clamp_vector(100.0, -50.0, 24.0, 'ideal')
        self.assertFalse(limited)
        self.assertEqual((alpha, beta), (100.0, -50.0))

    def test_clarke_and_park_round_trip(self):
        for alpha, beta in ((1.0, 0.0), (0.3, -0.7), (-2.0, 1.5)):
            a, b, c = inverter.alphabeta_to_abc(alpha, beta)
            back = inverter.abc_to_alphabeta(a, b, c)
            self.assertAlmostEqual(back[0], alpha, places=12)
            self.assertAlmostEqual(back[1], beta, places=12)

    def test_dq_park_round_trip(self):
        for d, q, theta in ((1.0, 2.0, 0.0), (0.5, -0.5, 1.1), (-3.0, 1.0, -2.4)):
            alpha, beta = inverter.dq_to_alphabeta(d, q, theta)
            back_d, back_q = inverter.alphabeta_to_dq(alpha, beta, theta)
            self.assertAlmostEqual(back_d, d, places=12)
            self.assertAlmostEqual(back_q, q, places=12)

    def test_svpwm_duties_are_zero_sum_offset(self):
        """七段式 SVPWM 等价于零序注入：三相占空比的极差等于线电压需求。"""
        vdc = 24.0
        alpha, beta = 5.0, 3.0
        d_a, d_b, d_c = inverter.svpwm_duties(alpha, beta, vdc)
        for duty in (d_a, d_b, d_c):
            self.assertGreaterEqual(duty, 0.0)
            self.assertLessEqual(duty, 1.0)
        # 零序注入后中心应被拉回 0.5 附近
        self.assertAlmostEqual((max(d_a, d_b, d_c) + min(d_a, d_b, d_c)) / 2.0, 0.5, places=6)

    def test_svpwm_sector_assignment(self):
        """扇区按 60° 划分：扇区 k 覆盖 (k−1)·60° 到 k·60°。"""
        sqrt3_2 = math.sqrt(3.0) / 2.0
        self.assertEqual(inverter.svpwm_sector(1.0, 0.0), 1)          # 0°
        self.assertEqual(inverter.svpwm_sector(0.5, sqrt3_2), 2)      # 60°（含边界）
        self.assertEqual(inverter.svpwm_sector(0.0, 1.0), 2)          # 90°
        self.assertEqual(inverter.svpwm_sector(-1.0, 0.0), 4)         # 180°
        self.assertEqual(inverter.svpwm_sector(0.0, -1.0), 5)         # 270°
        self.assertEqual(inverter.svpwm_sector(1.0, -0.0001), 6)      # 接近 360°
        self.assertEqual(inverter.svpwm_sector(0.0, 0.0), 0)          # 零矢量

    def test_svpwm_sequence_times_sum_to_period(self):
        vdc, period = 24.0, 1e-4
        for alpha, beta in ((6.0, 0.0), (3.0, 4.0), (0.0, -7.0)):
            sequence = inverter.svpwm_vector_times(alpha, beta, vdc, period)
            self.assertAlmostEqual(sum(item[1] for item in sequence), period, places=12)
            for name, duration in sequence:
                self.assertGreaterEqual(duration, 0.0)
                self.assertTrue(name.startswith('V'))

    def test_ab_vectors_equal_phase_voltage_amplitude(self):
        """核心约定：αβ 矢量长度 = 相电压幅值，且占空比能精确还原指令。

        这条测试走的是"指令 → 占空比 → 相电压 → Clarke"的完整回路，
        不去依赖某个公式的记忆。它是后面所有 FOC 结论的地基。
        """
        vdc = 24.0
        # 只在**线性区之内**才谈"精确还原"：超出后占空比被母线削顶，
        # 而削顶本身就是第 3 周要观察的现象。
        for alpha, beta in ((4.0, 2.0), (0.0, 7.5), (-9.0, -4.0), (11.0, 6.0)):
            duties = inverter.svpwm_duties(alpha, beta, vdc)
            phase = [duty * vdc for duty in duties]
            common = sum(phase) / 3.0
            back_a, back_b = inverter.abc_to_alphabeta(*(value - common for value in phase))
            self.assertAlmostEqual(back_a, alpha, places=9)
            self.assertAlmostEqual(back_b, beta, places=9)

    def test_linear_region_boundary(self):
        """线性区上界 Vdc/√3：占空比正好用满正负半周（但不到母线轨）。"""
        vdc = 24.0
        limit = inverter.linear_limit(vdc)
        duties = inverter.svpwm_duties(limit, 0.0, vdc)
        # 参考相电压是 (limit, −limit/2, −limit/2)，零序注入后映射到
        # (0.9330, 0.0670, 0.0670)：正负各用满 0.4330。
        self.assertAlmostEqual(max(duties), 0.9330127018922193, places=9)
        self.assertAlmostEqual(min(duties), 0.06698729810778065, places=9)
        self.assertAlmostEqual(max(duties) + min(duties), 1.0, places=9)
        # 超出线性区后被削顶，输出不再增长
        beyond = inverter.svpwm_duties(1.2 * limit, 0.0, vdc)
        self.assertAlmostEqual(max(beyond), max(duties), places=9)
        self.assertAlmostEqual(min(beyond), min(duties), places=9)

    def test_hexagon_vertex_needs_two_vectors(self):
        """六边形顶点 2·Vdc/3 处需要两个有效矢量共同作用。

        这条测试解释了为什么"线性区上界"（内切圆，Vdc/√3）比
        "六边形顶点"（2·Vdc/3）小 √3/2 倍：顶点方向要两个矢量一起凑。
        """
        vdc = 24.0
        vertex = 2.0 * vdc / 3.0
        # 顶点方向（0°）只用到 V1，但占比不满周期——剩下的时间给了零矢量
        sector, r1, r2 = inverter.svpwm_dwell(vertex, 0.0, vdc)
        self.assertEqual(sector, 1)
        self.assertAlmostEqual(r2, 0.0, places=9)
        self.assertLess(r1, 1.0)

    def test_switching_state_magnitudes(self):
        """六个有效开关矢量在 αβ 平面上的长度都是 2·Vdc/3。"""
        vdc = 24.0
        states = {1: (1, 0, 0), 2: (1, 1, 0), 3: (0, 1, 0),
                  4: (0, 1, 1), 5: (0, 0, 1), 6: (1, 0, 1)}
        for key, levels in states.items():
            phase = [level * vdc for level in levels]
            common = sum(phase) / 3.0
            alpha, beta = inverter.abc_to_alphabeta(*(value - common for value in phase))
            self.assertAlmostEqual(math.hypot(alpha, beta), 2.0 * vdc / 3.0, places=9)
            # 相邻矢量相差 60°，V1 沿 α 轴
            angle = math.degrees(math.atan2(beta, alpha)) % 360.0
            self.assertAlmostEqual(angle, (key - 1) * 60.0, places=6)

    def test_svpwm_sequence_times_sum_to_period(self):
        vdc, period = 24.0, 1e-4
        for alpha, beta in ((6.0, 0.0), (3.0, 4.0), (0.0, -7.0)):
            sequence = inverter.svpwm_vector_times(alpha, beta, vdc, period)
            self.assertAlmostEqual(sum(item[1] for item in sequence), period, places=12)
            for name, duration in sequence:
                self.assertGreaterEqual(duration, 0.0)
                self.assertTrue(name.startswith('V'))

    def test_ab_vectors_equal_phase_voltage_amplitude(self):
        """核心约定：αβ 矢量长度 = 相电压幅值，且占空比能精确还原指令。

        这条测试走的是"指令 → 占空比 → 相电压 → Clarke"的完整回路，
        不去依赖某个公式的记忆。它是后面所有 FOC 结论的地基。
        """
        vdc = 24.0
        # 只在**线性区之内**才谈"精确还原"：超出后占空比被母线削顶，
        # 而削顶本身就是第 3 周要观察的现象。
        for alpha, beta in ((4.0, 2.0), (0.0, 7.5), (-9.0, -4.0), (11.0, 6.0)):
            duties = inverter.svpwm_duties(alpha, beta, vdc)
            phase = [duty * vdc for duty in duties]
            common = sum(phase) / 3.0
            back_a, back_b = inverter.abc_to_alphabeta(*(value - common for value in phase))
            self.assertAlmostEqual(back_a, alpha, places=9)
            self.assertAlmostEqual(back_b, beta, places=9)

    def test_linear_region_boundary(self):
        """线性区上界 Vdc/√3：占空比正好用满正负半周（但不到母线轨）。"""
        vdc = 24.0
        limit = inverter.linear_limit(vdc)
        duties = inverter.svpwm_duties(limit, 0.0, vdc)
        # 参考相电压是 (limit, −limit/2, −limit/2)，零序注入后映射到
        # (0.9330, 0.0670, 0.0670)：正负各用满 0.4330。
        self.assertAlmostEqual(max(duties), 0.9330127018922193, places=9)
        self.assertAlmostEqual(min(duties), 0.06698729810778065, places=9)
        self.assertAlmostEqual(max(duties) + min(duties), 1.0, places=9)
        # 超出线性区后被削顶，输出不再增长
        beyond = inverter.svpwm_duties(1.2 * limit, 0.0, vdc)
        self.assertAlmostEqual(max(beyond), max(duties), places=9)
        self.assertAlmostEqual(min(beyond), min(duties), places=9)

    def test_hexagon_vertex_needs_two_vectors(self):
        """六边形顶点 2·Vdc/3 处需要两个有效矢量共同作用。

        这条测试解释了为什么"线性区上界"（内切圆，Vdc/√3）比
        "六边形顶点"（2·Vdc/3）小 √3/2 倍：顶点方向要两个矢量一起凑。
        """
        vdc = 24.0
        vertex = 2.0 * vdc / 3.0
        sector, r1, r2 = inverter.svpwm_dwell(vertex, 0.0, vdc)
        self.assertEqual(sector, 1)
        # 顶点方向只用到 V1 与零矢量，占比不到满周期——这正是
        # "顶点可达但内切圆才是线性区"的几何来源。
        self.assertAlmostEqual(r2, 0.0, places=9)
        self.assertGreater(r1, 0.0)
        self.assertLessEqual(r1, 1.0)
        # 15.5% 的过调制区会被母线削顶：顶点指令得到的是内切圆上的输出
        self.assertAlmostEqual(max(inverter.svpwm_duties(vertex, 0.0, vdc)), 0.9330127018922193, places=9)

    def test_dwell_times_satisfy_volt_second_balance(self):
        """用真实的开关矢量（含零序修正）验证伏秒平衡，误差应为机器精度。

        开关状态 V_k = (1,0,0) 等直接取三相电平会带共模分量；
        电机看到的是扣除共模后的电压，其 αβ 表示长度恰好是 2·Vdc/3。
        用这组矢量去乘解出来的占比，必须精确还原参考矢量。
        """
        vdc = 24.0
        states = {1: (1, 0, 0), 2: (1, 1, 0), 3: (0, 1, 0),
                  4: (0, 1, 1), 5: (0, 0, 1), 6: (1, 0, 1)}
        vectors = {}
        for key, levels in states.items():
            phase = [level * vdc for level in levels]
            common = sum(phase) / 3.0
            vectors[key] = inverter.abc_to_alphabeta(*(value - common for value in phase))
            self.assertAlmostEqual(math.hypot(*vectors[key]), 2.0 * vdc / 3.0, places=9)

        limit = inverter.linear_limit(vdc)
        for alpha, beta in ((4.0, 2.0), (13.0, -5.0), (0.0, 7.5), (-9.0, -4.0), (1.0, -12.0)):
            sector, r1, r2 = inverter.svpwm_dwell(alpha, beta, vdc)
            self.assertLess(r1 + r2, 1.0 + 1e-12)
            first, second = vectors[sector], vectors[sector % 6 + 1]
            back_a = r1 * first[0] + r2 * second[0]
            back_b = r1 * first[1] + r2 * second[1]
            self.assertAlmostEqual(back_a, alpha, delta=max(1e-9, abs(alpha) * 1e-9))
            self.assertAlmostEqual(back_b, beta, delta=max(1e-9, abs(beta) * 1e-9))
        self.assertGreater(limit, 0.0)

    def test_dwell_times_at_linear_limit(self):
        """全周扫一圈：线性区上界的矢量在任意方向都只用一个有效矢量。"""
        vdc = 24.0
        limit = inverter.linear_limit(vdc)
        # 避开扇区边界：正好落在边界上的浮点结果不该被用来断言扇区编号
        for step in range(6):
            theta = (step + 0.5) * math.pi / 3.0
            sector, r1, r2 = inverter.svpwm_dwell(limit * math.cos(theta), limit * math.sin(theta), vdc)
            self.assertEqual(sector, step + 1)
            self.assertAlmostEqual(r1 + r2, 1.0, places=9)
            self.assertAlmostEqual(r1, r2, places=9)
        # 方向在扇区内部时两个矢量都参与
        _sector, r1, r2 = inverter.svpwm_dwell(limit * math.cos(0.5), limit * math.sin(0.5), vdc)
        self.assertGreater(r1, 0.0)
        self.assertGreater(r2, 0.0)
        self.assertLess(r1 + r2, 1.0)

    def test_svpwm_sequence_times_sum_to_period(self):
        vdc, period = 24.0, 1e-4
        for alpha, beta in ((6.0, 0.0), (3.0, 4.0), (0.0, -7.0), (12.0, 5.0)):
            sequence = inverter.svpwm_vector_times(alpha, beta, vdc, period)
            self.assertAlmostEqual(sum(item[1] for item in sequence), period, places=12)
            for name, duration in sequence:
                self.assertGreaterEqual(duration, -1e-15)
                self.assertTrue(name.startswith('V'))

    def test_pwm_duties_bounded(self):
        duties = inverter.carrier_pwm(50.0, 0.0, 24.0)
        for duty in duties:
            self.assertGreaterEqual(duty, 0.0)
            self.assertLessEqual(duty, 1.0)

    def test_carrier_waveform_shape(self):
        t, carrier, ref = inverter.carrier_pwm_waveform(3.0, 0.0, 24.0, cycles=2, points=200)
        self.assertEqual(len(t), len(carrier), len(ref))
        # 三角载波：峰谷接近 ±1（采样点不一定正好落在端点）
        self.assertGreater(max(carrier), 0.98)
        self.assertLess(min(carrier), -0.98)

    def test_rejects_non_positive_bus(self):
        with self.assertRaises(ValueError):
            inverter.svpwm_duties(1.0, 1.0, 0.0)


class MetricsAgainstModelsTest(unittest.TestCase):
    def test_mechanical_time_constant_shows_in_step_response(self):
        """一阶惯性环节 63.2% 上升时间应约等于时间常数。"""
        tau, gain = 2.0, 10.0
        value_at_tau = metrics.first_order_response(gain, tau, tau)
        self.assertAlmostEqual(value_at_tau / gain, 0.6321205588, places=6)


if __name__ == '__main__':
    unittest.main()
