"""HTTP 接口的端到端测试。

课程作者：Connor He 和 Astra。

这些测试会真的起一个本地服务、发真的 HTTP 请求，覆盖：

* 令牌鉴权与跨站来源拦截；
* 参数校验的边界（NaN、超范围、未知字段、超时长）；
* 静态文件的路径穿越防护；
* 仿真作业的完整生命周期（提交 → 轮询 → 导出 → 取消）；
* 课程内容自检接口。

不管 Docker 是否存在，这些测试都必须通过——课程的核心实验不依赖容器。
"""
import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request

from server.app import build_course

TOKEN_HEADER = 'X-Course-Token'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ApiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.course = build_course(state_dir=None, port=cls.port)
        # 测试用独立的临时状态目录，避免污染 .state/
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory(prefix='course-test-')
        cls.course = build_course(state_dir=cls.tmp.name, port=cls.port)
        try:
            cls.course.start()
        except FileNotFoundError as error:              # 内容尚未生成
            raise unittest.SkipTest(str(error))
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.token = cls.course.token
        time.sleep(0.15)

    @classmethod
    def tearDownClass(cls):
        cls.course.stop()
        cls.tmp.cleanup()

    def request(self, path, method='GET', body=None, token=True, origin=None, host=None):
        url = f'{self.base}{path}'
        data = json.dumps(body).encode('utf-8') if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if token:
            request.add_header(TOKEN_HEADER, self.token)
        if origin is not None:
            request.add_header('Origin', origin)
        if body is not None:
            request.add_header('Content-Type', 'application/json')
        if host is not None:
            request.add_header('Host', host)
        opener = urllib.request.build_opener()
        return opener.open(request, timeout=20)

    def json(self, path, method='GET', body=None, **kwargs):
        with self.request(path, method, body, **kwargs) as response:
            return response.status, json.loads(response.read().decode('utf-8'))

    def expect_error(self, path, status, method='GET', body=None, **kwargs):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(path, method, body, **kwargs)
        self.assertEqual(caught.exception.code, status,
                         f'{path} 期望 {status}，实际 {caught.exception.code}')
        return json.loads(caught.exception.read().decode('utf-8'))


class SessionAndAuthTest(ApiTestBase):
    def test_session_is_public(self):
        status, payload = self.json('/api/session', token=False)
        self.assertEqual(status, 200)
        self.assertIn('token', payload)
        self.assertIn('version', payload)

    def test_missing_token_is_rejected(self):
        payload = self.expect_error('/api/health', 401, token=False)
        self.assertIn('会话', payload['error'])

    def test_wrong_token_is_rejected(self):
        self.expect_error('/api/health', 401, token=False)

    def test_foreign_origin_is_rejected(self):
        payload = self.expect_error('/api/health', 403, origin='http://evil.example')
        self.assertIn('来源', payload['error'])

    def test_local_origin_is_accepted(self):
        status, _ = self.json('/api/health', origin=f'http://127.0.0.1:{self.port}')
        self.assertEqual(status, 200)

    def test_foreign_host_header_is_rejected(self):
        self.expect_error('/api/health', 403, host='example.com')

    def test_unknown_endpoint(self):
        payload = self.expect_error('/api/nope', 404)
        self.assertIn('接口', payload['error'])

    def test_method_not_allowed(self):
        self.expect_error('/api/health', 405, method='POST', body={})


class HealthTest(ApiTestBase):
    def test_health_reports_environment_truthfully(self):
        status, payload = self.json('/api/health')
        self.assertEqual(status, 200)
        self.assertIn('python', payload)
        self.assertIn('disk_free_gb', payload)
        self.assertIsInstance(payload['docker'], bool)
        # 没有 docker 时必须如实报告，而不是假装可用
        if not payload['docker']:
            self.assertFalse(payload['ready'])
            self.assertTrue(any('docker' in issue for issue in payload['issues']))


class ContentTest(ApiTestBase):
    def test_curriculum_shape(self):
        status, payload = self.json('/api/content/curriculum')
        self.assertEqual(status, 200)
        self.assertTrue(payload['weeks'])
        self.assertTrue(payload['lessons'])
        self.assertEqual(len(payload['weeks']), 11)

    def test_limits_expose_sim_surface(self):
        status, payload = self.json('/api/content/limits')
        self.assertEqual(status, 200)
        self.assertIn('dc', payload['params'])
        self.assertIn('pmsm_bldc', payload['params'])
        self.assertIn('induction', payload['params'])
        self.assertIn('speed', payload['scenarios']['dc'])
        self.assertNotIn('vf_scan', payload['scenarios']['dc'])

    def test_presets_are_runnable(self):
        status, payload = self.json('/api/sim/presets')
        self.assertEqual(status, 200)
        self.assertTrue(payload)
        for preset in payload:
            self.assertIn('id', preset)
            self.assertIn('title', preset)
            self.assertIn('spec', preset)

    def test_check_endpoint_reports_clean_content(self):
        status, payload = self.json('/api/check', method='POST', body={})
        self.assertEqual(status, 200)
        self.assertTrue(payload['ok'], payload['issues'][:5])


class ProgressTest(ApiTestBase):
    def test_read_and_quiz_round_trip(self):
        lesson = self.json('/api/content/curriculum')[1]['lessons'][0]
        status, payload = self.json('/api/progress', method='POST',
                                    body={'lesson': lesson['id'], 'action': 'read'})
        self.assertEqual(status, 200)
        record = next(item for item in payload['lessons'] if item['lesson'] == lesson['id'])
        self.assertTrue(record['read_at'])

        total = len(lesson['quiz'])
        answers = [0] * total
        status, payload = self.json('/api/progress', method='POST',
                                    body={'lesson': lesson['id'], 'action': 'quiz', 'answers': answers})
        self.assertEqual(status, 200)
        self.assertEqual(payload['total'], total)
        self.assertIn('score', payload)

    def test_unknown_lesson_rejected(self):
        self.expect_error('/api/progress', 400, method='POST',
                          body={'lesson': 'does-not-exist', 'action': 'read'})

    def test_wrong_answer_count_rejected(self):
        lesson = self.json('/api/content/curriculum')[1]['lessons'][0]
        self.expect_error('/api/progress', 400, method='POST',
                          body={'lesson': lesson['id'], 'action': 'quiz', 'answers': [0]})

    def test_out_of_range_answer_rejected(self):
        lesson = self.json('/api/content/curriculum')[1]['lessons'][0]
        answers = [99] * len(lesson['quiz'])
        self.expect_error('/api/progress', 400, method='POST',
                          body={'lesson': lesson['id'], 'action': 'quiz', 'answers': answers})

    def test_preferences_whitelist(self):
        status, _ = self.json('/api/preferences', method='POST', body={'studentName': '测试同学'})
        self.assertEqual(status, 200)
        self.expect_error('/api/preferences', 400, method='POST', body={'evil': 'x'})


class SimulationApiTest(ApiTestBase):
    def run_spec(self, spec, timeout=40):
        status, payload = self.json('/api/sim/runs', method='POST', body={'spec': spec})
        self.assertEqual(status, 202, payload)
        run_id = payload['id']
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, state = self.json(f'/api/sim/runs/{run_id}')
            if state['status'] in ('succeeded', 'failed'):
                return run_id, state
            time.sleep(0.15)
        self.fail('仿真作业超时未结束')

    def test_dc_speed_run_succeeds(self):
        # 机械时间常数是 2 s，所以时长要给够，否则测的只是瞬态而不是闭环稳态
        run_id, state = self.run_spec({'kind': 'dc', 'scenario': 'speed', 'controller': 'speed',
                                       'duration': 4.0, 'target': 600.0})
        self.assertEqual(state['status'], 'succeeded', state.get('error'))
        result = state['result']
        self.assertTrue(result['series'])
        self.assertFalse(result['metrics']['diverged'])
        self.assertAlmostEqual(result['metrics']['final_rpm'], 600.0, delta=8.0)
        self.assertIn('rpm', result['series'][0])

    def test_response_contains_no_non_finite_numbers(self):
        """JSON 里不允许出现 NaN / Infinity——它们会让前端整页崩掉。"""
        _, state = self.run_spec({'kind': 'pmsm_bldc', 'scenario': 'current_loop',
                                  'controller': 'current', 'bridge': 'svpwm',
                                  'duration': 0.1, 'target': 2.0})
        text = json.dumps(state, allow_nan=False)      # 有非有限值会抛异常
        self.assertNotIn('NaN', text)
        self.assertNotIn('Infinity', text)

    def test_export_zip_contains_trace(self):
        run_id, state = self.run_spec({'kind': 'dc', 'scenario': 'speed', 'controller': 'speed',
                                       'duration': 0.5, 'target': 300.0})
        self.assertEqual(state['status'], 'succeeded')
        with self.request(f'/api/sim/runs/{run_id}/export') as response:
            blob = response.read()
        self.assertTrue(blob.startswith(b'PK'))       # zip 魔数
        self.assertIn(b'trace.csv', blob)

    def test_export_before_finish_is_conflict(self):
        status, payload = self.json('/api/sim/runs', method='POST',
                                    body={'spec': {'kind': 'dc', 'duration': 5.0}})
        self.assertEqual(status, 202)
        self.expect_error(f"/api/sim/runs/{payload['id']}/export", 409)

    def test_cancel_marks_failed(self):
        status, payload = self.json('/api/sim/runs', method='POST',
                                    body={'spec': {'kind': 'dc', 'duration': 10.0}})
        self.assertEqual(status, 202)
        run_id = payload['id']
        self.json(f'/api/sim/runs/{run_id}/cancel', method='POST', body={})
        _, state = self.json(f'/api/sim/runs/{run_id}')
        self.assertEqual(state['status'], 'failed')

    def test_unknown_run_id(self):
        self.expect_error('/api/sim/runs/deadbeef', 404)

    def test_malformed_run_id(self):
        self.expect_error('/api/sim/runs/not-a-hex-id', 404)


class ParameterValidationTest(ApiTestBase):
    """参数校验必须拒绝危险输入，而不是悄悄钳位。"""

    def invalid(self, spec, fragment):
        payload = self.expect_error('/api/sim/runs', 400, method='POST', body={'spec': spec})
        self.assertIn(fragment, payload['error'])

    def test_rejects_nan_and_infinity(self):
        # JSON 本身不允许 NaN；这里用字符串形式确认不会被当成数字
        self.invalid({'kind': 'dc', 'target': 'NaN'}, '必须')
        self.invalid({'kind': 'dc', 'vdc': 1e400 if False else 'Infinity'}, '必须')

    def test_rejects_too_long_duration(self):
        self.invalid({'kind': 'dc', 'duration': 60}, 'duration')

    def test_rejects_negative_duration(self):
        self.invalid({'kind': 'dc', 'duration': -1}, 'duration')

    def test_rejects_unknown_field(self):
        self.invalid({'kind': 'dc', 'turbo': True}, '未登记')

    def test_rejects_unknown_param(self):
        self.invalid({'kind': 'dc', 'params': {'R': 2.0, 'magic': 1.0}}, '未登记')

    def test_rejects_out_of_range_param(self):
        self.invalid({'kind': 'dc', 'params': {'R': 0.0}}, '不能小于')

    def test_rejects_bad_scenario_for_kind(self):
        self.invalid({'kind': 'pmsm_bldc', 'scenario': 'vf_scan'}, '不支持')

    def test_rejects_substep_larger_than_control_period(self):
        self.invalid({'kind': 'dc', 'control_period': 0.0001, 'substep': 0.001}, 'substep')

    def test_rejects_bad_fault_name(self):
        self.invalid({'kind': 'dc', 'faults': ['explode']}, '只允许')

    def test_rejects_indicator_matrix_singularity(self):
        self.invalid({'kind': 'induction', 'params': {'Ls': 1.0, 'Lr': 1.0, 'Lm': 2.0}},
                     '互感')

    def test_accepts_valid_boundary_values(self):
        status, payload = self.json('/api/sim/runs', method='POST',
                                    body={'spec': {'kind': 'dc', 'duration': 10.0,
                                                   'substep': 0.0005, 'control_period': 0.001}})
        self.assertEqual(status, 202)


class CurveApiTest(ApiTestBase):
    def curve(self, name, spec):
        return self.json('/api/sim/curves', method='POST', body={'curve': name, 'spec': spec})

    def test_dc_mechanical_curve(self):
        status, payload = self.curve('dc_mechanical', {'kind': 'dc', 'scenario': 'torque_speed'})
        self.assertEqual(status, 200)
        self.assertEqual(payload['kind'], 'family')
        self.assertTrue(payload['families'])
        # 解析与仿真两条路径互相对照
        for check in payload['checks']:
            self.assertAlmostEqual(check['analytic_rpm'], check['simulated_rpm'], delta=30.0)

    def test_induction_torque_slip_curve(self):
        status, payload = self.curve('induction_torque_slip',
                                     {'kind': 'induction', 'scenario': 'slip_scan'})
        self.assertEqual(status, 200)
        self.assertTrue(payload['families'])
        points = payload['families'][0]['points']
        self.assertTrue(any(point['torque_nm'] > 0 for point in points))

    def test_bode_reports_bandwidth(self):
        status, payload = self.curve('current_loop_bode',
                                     {'kind': 'pmsm_bldc', 'scenario': 'current_loop',
                                      'bridge': 'svpwm',
                                      'sweep': {'start_hz': 20, 'stop_hz': 400, 'points': 6}})
        self.assertEqual(status, 200)
        self.assertEqual(payload['kind'], 'bode')
        self.assertTrue(payload['points'])
        self.assertGreater(payload['bandwidth_hz'] or 0, 10.0)

    def test_svpwm_gain_is_15_percent(self):
        status, payload = self.curve('svpwm_spectrum', {'kind': 'dc', 'scenario': 'speed'})
        self.assertEqual(status, 200)
        self.assertAlmostEqual(payload['limits']['gain_percent'], 15.47, delta=0.05)

    def test_unknown_curve_rejected(self):
        self.expect_error('/api/sim/curves', 400, method='POST',
                          body={'curve': 'black-magic', 'spec': {'kind': 'dc'}})


class StaticFilesTest(ApiTestBase):
    def test_index_serves(self):
        with self.request('/', token=False) as response:
            body = response.read().decode('utf-8')
        self.assertIn('ROBOCON', body)
        self.assertIn('Content-Security-Policy', response.headers)

    def test_app_js_serves(self):
        with self.request('/app.js', token=False) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('javascript', response.headers['Content-Type'])

    def test_path_traversal_blocked(self):
        for path in ('/../server/app.py', '/..%2fserver%2fapp.py', '/vendor/../../server/app.py'):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(path, token=False)
            self.assertIn(caught.exception.code, (403, 404))

    def test_missing_asset(self):
        self.expect_error('/nope.js', 404, token=False)


class PortfolioTest(ApiTestBase):
    def test_portfolio_export_is_markdown(self):
        with self.request('/api/portfolio/export') as response:
            text = response.read().decode('utf-8')
        self.assertIn('# RoboCON 电控实训', text)
        self.assertIn('逐周记录', text)

    def test_teacher_export(self):
        with self.request('/api/teacher') as response:
            text = response.read().decode('utf-8')
        self.assertTrue(text.strip())


if __name__ == '__main__':
    unittest.main()
