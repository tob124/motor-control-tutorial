/* ROBOCON 电控实训教室 · 前端应用
 *
 * 原生 ES module，无构建步骤、无 CDN。
 * 状态放在一个对象里，视图函数按 hash 路由切换。
 */

import * as P from '/plot.js';
import { ModelViewer, viewerMarkup } from '/modelview.js';
import { motorSpecFromConfig } from '/view.js';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = P.escapeHtml;

const state = {
  token: '',
  course: null,
  software: [],
  limits: null,
  presets: [],
  progress: { lessons: [], preferences: {}, attempts: 0 },
  health: null,
  view: 'learn',
  lesson: null,
  result: null,
  runId: null,
  running: false,
  pollTimer: null,
  labSpec: null,
  curve: null,
  hintsShown: {},
  // 显示模式：3d（软件渲染）| 2d（正投影降级）。低配电脑可用 2D。
  display: '3d',
  viewers: {},          // canvasId → ModelViewer
  modelHeight: 300,
};

const viewRoot = () => $('#view-root');

/* ------------------------------------------------------------------ 基础设施 */

/* ------------------------------------------------------------------ 模型查看器 */

/** 创建（或复用）一个绑定到 canvas 的查看器。 */
function makeViewer(canvasId, options = {}) {
  disposeViewer(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  const viewer = new ModelViewer(canvas, {
    mode: state.display,
    height: options.height || state.modelHeight,
    onFrame: (info) => updateModelControls(canvasId, info),
    ...options,
  });
  state.viewers[canvasId] = viewer;
  updateModelControls(canvasId, viewer.state());
  return viewer;
}

function disposeViewer(canvasId) {
  const existing = state.viewers[canvasId];
  if (existing) { existing.dispose(); delete state.viewers[canvasId]; }
}

function disposeAllViewers() {
  for (const id of Object.keys(state.viewers)) disposeViewer(id);
}

function updateModelControls(canvasId, info) {
  // 播放控件是否可用，只取决于"有没有数据"。
  // 早先的版本把 disabled 写死在初始 HTML 里（hasData 恒为 false），
  // 结果运行完实验、数据已经灌进模型了，播放按钮却仍然是灰的、点不动。
  const ready = info.total >= 2;
  for (const selector of ['play', 'step']) {
    document.querySelectorAll(`[data-model-${selector}="${canvasId}"]`).forEach((button) => {
      button.disabled = !ready;
    });
  }
  const scrub = document.querySelector(`[data-model-scrub="${canvasId}"]`);
  if (scrub) {
    scrub.disabled = !ready;
    if (info.total) {
      scrub.max = String(Math.max(0, info.total - 1));
      scrub.value = String(info.index);
    }
  }
  const frame = document.querySelector(`[data-model-frame="${canvasId}"]`);
  if (frame) {
    frame.textContent = info.total
      ? `第 ${info.index + 1} / ${info.total} 帧 · t = ${info.time.toFixed(3)} s`
      : '等待实验数据';
  }
  const play = document.querySelector(`[data-model-play="${canvasId}"]`);
  if (play) play.textContent = info.playing ? '⏸ 暂停' : '▶ 播放';
  const stem = document.querySelector(`[data-model-root="${canvasId}"]`);
  if (stem) {
    stem.querySelectorAll('[data-model-mode]').forEach((button) => {
      button.classList.toggle('active', button.dataset.modelMode === info.mode);
    });
  }
}

/** 当前实验配置 → 电机可视化参数（极对数等直接来自仿真配置）。 */
function currentMotorSpec() {
  const spec = state.labSpec || {};
  return motorSpecFromConfig(spec);
}

/** 按课程单元决定在课程页展示哪个模型。 */
function lessonModel(lesson) {
  const week = lesson.week;
  const text = `${lesson.title} ${lesson.summary}`;
  if (week >= 7 || /运动学|底盘|路径|机构|里程计|机器人/.test(text)) {
    return { kind: 'robot', title: '仿真机器人模型', subtitle: '差速底盘 + 四连杆机构；俯视/侧视在 2D 模式' };
  }
  const kind = week <= 1 ? 'dc' : (week === 2 || week === 3 ? 'induction' : 'pmsm_bldc');
  const labels = { dc: '他励直流机', induction: '三相异步机', pmsm_bldc: '永磁同步 / 无刷机' };
  return { kind, title: `仿真电机模型 · ${labels[kind]}`, subtitle: '可拖动旋转、滚轮缩放；部件图注随课程高亮' };
}

/** 课程页里该高亮哪些部件（把讲的内容和模型对上）。 */
function lessonHighlight(lesson) {
  const text = `${lesson.title} ${lesson.summary}`;
  if (/编码器|反馈|测速|量化/.test(text)) return ['encoder'];
  if (/转轴|输出|传动|减速/.test(text)) return ['shaft'];
  if (/磁|极|永磁|磁场/.test(text)) return ['magnet-0', 'magnet-1', 'rotor'];
  if (/绕组|相|定子|电流环|FOC/.test(text)) return ['coil-0', 'coil-1', 'coil-2'];
  return [];
}

/** 课程页顶部的模型面板。 */
function lessonModelPanel(lesson) {
  const info = lessonModel(lesson);
  return viewerMarkup({
    id: 'lesson-model', title: info.title, subtitle: info.subtitle,
    mode: state.display, height: 300, hasData: false,
  });
}

/** 上一次实验的数据是否就是这个单元该看的模型（避免张冠李戴）。 */
function modelMatchesLesson(info, labSpec) {
  if (!labSpec) return false;
  if (info.kind === 'robot') return Boolean(labSpec.robot_params) || labSpec.scenario === 'path_follow';
  return labSpec.kind === info.kind;
}

async function api(path, method = 'GET', body) {
  const options = { method, headers: { 'X-Course-Token': state.token } };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
    options.headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    let payload;
    try { payload = await response.json(); } catch { payload = { error: '本地服务没有正常响应。' }; }
    throw new Error(payload.error || '请求未完成。');
  }
  const type = response.headers.get('Content-Type') || '';
  if (type.includes('application/json')) return response.json();
  return response;
}

function toast(message) {
  const node = $('#toast');
  node.textContent = message;
  node.style.display = 'block';
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.style.display = 'none'; }, 5000);
}

function navigate(view, detail) {
  location.hash = detail ? `${view}/${detail}` : view;
}

function bytes(value) {
  return typeof value === 'number' ? value : '—';
}

/* ------------------------------------------------------------------ 进度 */

function progressOf(id) {
  return state.progress.lessons.find((item) => item.lesson === id) || { score: 0, checked: false };
}

function completed(id) {
  const record = progressOf(id);
  return Boolean(record.read_at) && (record.score || 0) >= 80;
}

function updateNav() {
  const lessons = state.course.lessons;
  const total = lessons.length;
  const done = lessons.filter((item) => completed(item.id)).length;
  $('#progress-label').textContent = `${done} / ${total}`;
  $('#overall-progress').max = total;
  $('#overall-progress').value = done;
  $('#week-count').textContent = `${state.course.weeks.length} 周 / ${total} 单元`;
  $$('.main-nav button').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === state.view);
  });
  const current = currentLesson();
  $('#course-nav').innerHTML = state.course.weeks.map((week) => {
    const active = current && current.week === week.id;
    const items = lessons.filter((item) => item.week === week.id);
    return `<button class="week-link ${active && state.view === 'learn' ? 'active' : ''}"
              data-lesson="${esc(items[0] ? items[0].id : '')}">
              ${String(week.id).padStart(2, '0')}<span>${esc(week.title)}</span></button>
      ${active && state.view === 'learn' ? `<div class="lesson-subnav">${items.map((item) =>
        `<button class="${state.lesson === item.id ? 'active' : ''}" data-lesson="${esc(item.id)}">
          ${completed(item.id) ? '✓ ' : ''}${esc(item.title)}</button>`).join('')}</div>` : ''}`;
  }).join('');
}

function currentLesson() {
  if (!state.course) return null;
  return state.course.lessons.find((item) => item.id === state.lesson)
      || state.course.lessons[0];
}

/* ------------------------------------------------------------------ 渲染入口 */

function render() {
  if (!state.course) return;
  const parts = location.hash.slice(1).split('/');
  const views = ['learn', 'motor', 'power', 'robot', 'software', 'portfolio'];
  state.view = views.includes(parts[0]) ? parts[0] : 'learn';
  if (state.view === 'learn' && parts[1] && state.course.lessons.some((l) => l.id === parts[1])) {
    state.lesson = parts[1];
  }
  const labels = {
    learn: '项目课程', motor: '电机与闭环实验台', power: '逆变器与调制',
    robot: '整机与机器人', software: '工具与环境', portfolio: '学习与作品',
  };
  const current = currentLesson();
  $('#breadcrumb').innerHTML = `${labels[state.view]} <span>/</span> ${
    state.view === 'learn' && current ? `第 ${current.week} 周 · ${esc(current.title)}` : '课程地图'}`;
  updateNav();
  const dispatch = {
    learn: renderLesson, motor: () => renderLab('motor'), power: () => renderLab('power'),
    robot: () => renderLab('robot'), software: renderSoftware, portfolio: renderPortfolio,
  };
  // 每个视图重建前先停掉旧的动画循环：否则切页后 rAF 仍在跑，白烧 CPU
  disposeAllViewers();
  dispatch[state.view]();
  $('#sidebar').classList.remove('open');
  window.scrollTo({ top: 0, behavior: 'instant' });
}

function heading(eyebrow, title, description, meta = '') {
  return `<section class="lesson-heading">
    <div class="eyebrow">${eyebrow}</div>
    <h1>${esc(title)}</h1>
    <p>${esc(description)}</p>${meta}</section>`;
}

/* ------------------------------------------------------------------ 课程视图 */

function renderLesson() {
  const lesson = currentLesson();
  if (!lesson) { viewRoot().innerHTML = '<p>课程内容尚未生成。</p>'; return; }
  const record = progressOf(lesson.id);
  const week = state.course.weeks.find((w) => w.id === lesson.week);
  const weekLessons = state.course.lessons.filter((l) => l.week === lesson.week);
  const position = weekLessons.findIndex((l) => l.id === lesson.id) + 1;
  const objectives = (lesson.objectives || []).map((text) => `<li>${esc(text)}</li>`).join('');
  const sections = (lesson.sections || []).map((section, index) => `
    <section class="teaching-section">
      <h2><span class="section-number">${String(index + 1).padStart(2, '0')}</span>${esc(section.title)}</h2>
      ${String(section.body || '').split(/\n\s*\n/).map((text) => `<p>${inline(text)}</p>`).join('')}
    </section>`).join('');
  const equations = (lesson.equations || []).length ? `
    <section class="equations-card">
      <h2>本课要用的关系式</h2>
      <dl>${lesson.equations.map((item) => `
        <dt>${esc(item.expr)}</dt><dd>${esc(item.note)}</dd>`).join('')}</dl>
    </section>` : '';
  const commands = (lesson.commands || []).map((item, index) => `
    <div class="command-card">
      <div class="command-top"><span>步骤 ${index + 1}</span>
        <button class="copy-button" data-copy="${esc(item.command)}">复制命令</button></div>
      <pre><code>${esc(item.command)}</code></pre>
      <p>${esc(item.explanation)}</p>
      <div class="expected"><b>检查结果</b> ${esc(item.expected)}</div>
    </div>`).join('');
  const labs = (lesson.labs || []).length ? `
    <section class="lab-links">
      <h2>本课实验</h2>
      <div class="preset-row">${lesson.labs.map((lab) => `
        <button class="chip" data-preset="${esc(lab.id)}">▶ ${esc(lab.title)}</button>`).join('')}</div>
      <p class="muted">实验在右侧/本页下方的实验台里运行；数据来自本机实时求解，不是预存曲线。</p>
    </section>` : '';
  const tasks = (lesson.tasks || []).map((task, index) => `
    <div class="task-card">
      <div class="eyebrow">实践任务 ${index + 1}</div>
      <h3>${esc(task.title)}</h3>
      <p>${esc(task.description)}</p>
      <div class="button-row">
        <button class="text-link" data-hint="${esc(lesson.id)}">需要一点提示？</button>
        <button class="text-link" data-solution="${esc(lesson.id)}">查看参考思路</button>
      </div>
      <div id="hint-${esc(lesson.id)}" class="hint-box" hidden>
        <ol>${(task.hints || []).map((hint) => `<li>${esc(hint)}</li>`).join('')}</ol>
      </div>
      <div id="solution-${esc(lesson.id)}" class="solution-box" hidden>
        <p>${esc(task.solution)}</p>
      </div>
    </div>`).join('');
  const quiz = (lesson.quiz || []).map((item, index) => `
    <fieldset>
      <legend>${index + 1}. ${esc(item.question)}</legend>
      ${item.options.map((option, optionIndex) => `
        <label class="quiz-option">
          <input type="radio" name="q${index}" value="${optionIndex}"
            ${(record.quiz && record.quiz.answers && record.quiz.answers[index] === optionIndex) ? 'checked' : ''}>
          <span>${esc(option)}</span></label>`).join('')}
    </fieldset>`).join('');
  const glossary = (lesson.glossary || []).map((item) => `
    <dt>${esc(item.en)}</dt><dd>${esc(item.zh)}</dd>`).join('');

  viewRoot().innerHTML = heading(
    `第 ${String(lesson.week).padStart(2, '0')} 周 · ${esc(week ? week.title : '')}`,
    lesson.title, lesson.summary,
    `<div class="lesson-meta"><span>${position} / ${String(weekLessons.length).padStart(2, '0')} 单元</span>
     <span>约 ${lesson.duration} 分钟</span>
     <span>${record.read_at ? '已记录阅读' : '尚未标记阅读'}</span>
     <span>理解题 ${record.score || 0}%</span>
     <span>${record.checked ? '实践检查通过' : '实践待检查'}</span></div>`)
    + `<div class="learning-grid"><article class="lesson-body">
        ${lessonModelPanel(lesson)}
        <section class="teaching-section"><h2>本课目标</h2><ul class="objectives">${objectives}</ul></section>
        ${sections}
        ${equations}
        ${commands ? `<section class="teaching-section"><h2>动手命令</h2>${commands}</section>` : ''}
        ${labs}
        <section class="teaching-section"><h2>任务与自查</h2>${tasks || '<p class="muted">本单元以理解与实验为主。</p>'}</section>
        <section class="quiz-section"><h2>检查你的理解</h2>
          <p class="muted">可以重试。理解题与实验记录分别保存。</p>
          <form id="quiz-form">${quiz}
            <button class="primary" type="submit">提交理解题</button>
            <div id="quiz-feedback" aria-live="polite"></div></form></section>
        ${glossary ? `<details class="glossary"><summary>中英术语速查</summary><dl>${glossary}</dl></details>` : ''}
        <div class="learning-note"><b>本课交付</b><p>${esc(lesson.deliverable)}</p></div>
        ${(lesson.softwareIds || []).length ? `<div class="software-links">相关工具
          ${lesson.softwareIds.map((id) => {
            const tool = state.software.find((item) => item.id === id);
            return `<button class="text-link" data-software="${esc(id)}">${esc(tool ? tool.name : id)}</button>`;
          }).join('')}</div>` : ''}
        <div class="lesson-bottom">
          <button class="secondary" id="mark-read">${record.read_at ? '✓ 已记录阅读' : '标记已读'}</button>
          <button class="secondary" id="prev-lesson">← 上一单元</button>
          <button class="primary" id="next-lesson">下一单元 →</button>
        </div>
      </article>
      <aside class="lab-panel">
        <div class="panel-title"><span>随课实验</span><span class="tag">实时数值仿真</span></div>
        <div id="lesson-lab" class="lesson-lab-hint">
          <p>选择一个实验预设，右侧会显示参数与曲线。</p>
          <div class="preset-row">${(lesson.labs || []).map((lab) =>
            `<button class="chip" data-preset="${esc(lab.id)}">${esc(lab.title)}</button>`).join('')
            || '<span class="muted">本单元没有配套实验。</span>'}</div>
        </div>
        <div class="lab-help">曲线由本机实时求解微分方程得到；参数与结果都可以导出。</div>
      </aside></div>`;

  // 单元内部按钮
  const markRead = $('#mark-read');
  if (markRead) {
    markRead.onclick = async () => {
      try {
        await api('/progress', 'POST', { lesson: lesson.id, action: 'read' });
        state.progress = await api('/progress');
        await savePreference('lastLesson', lesson.id);
        renderLesson();
      } catch (error) { toast(error.message); }
    };
  }
  const quizForm = $('#quiz-form');
  if (quizForm) {
    quizForm.onsubmit = async (event) => {
      event.preventDefault();
      const answers = (lesson.quiz || []).map((_, index) => {
        const picked = quizForm.querySelector(`input[name="q${index}"]:checked`);
        return picked ? Number(picked.value) : -1;
      });
      try {
        const result = await api('/progress', 'POST', { lesson: lesson.id, action: 'quiz', answers });
        state.progress = await api('/progress');
        const box = $('#quiz-feedback');
        box.innerHTML = `<p class="${result.pass ? 'success-text' : 'warning-text'}">
          得分 ${result.score}%（${result.correct}/${result.total}）${
            result.pass ? '，达到 80% 要求。' : '，尚未达到 80%，建议回看相关段落。'}</p>
          ${(lesson.quiz || []).map((item, index) => {
            const answer = item.answer;
            const mark = answers[index] === answer ? '✓' : '✗';
            return `<p class="muted">${index + 1}. ${mark} 正确答案：<b>${esc(item.options[answer])}</b>。
              ${esc(item.explanation)}</p>`;
            }).join('')}`;
      } catch (error) { toast(error.message); }
    };
  }
  const index = weekLessons.findIndex((item) => item.id === lesson.id);
  const prev = $('#prev-lesson'), next = $('#next-lesson');
  if (prev) {
    prev.disabled = index <= 0;
    prev.onclick = () => navigate('learn', weekLessons[index - 1].id);
  }
  if (next) {
    const globalIndex = state.course.lessons.findIndex((item) => item.id === lesson.id);
    const following = state.course.lessons[globalIndex + 1];
    next.disabled = !following;
    next.onclick = () => following && navigate('learn', following.id);
  }
}

/** 把 **粗体** 与 *斜体* 做最小化处理，其余按纯文本转义。 */
function inline(text) {
  return esc(text)
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

/* ------------------------------------------------------------------ 实验台 */

const MACHINE_NAMES = { dc: '直流机', pmsm_bldc: '永磁同步 / 无刷机', induction: '三相异步机' };
const CONTROL_NAMES = {
  open: '开环（只给电压）', current: '电流环', speed: '速度环',
  position: '位置环（串级）',
};
const SCENARIO_NAMES = {
  speed: '速度阶跃与抗扰', current_loop: '电流环跟踪（堵转）',
  position_step: '位置阶跃', open_loop: '开环启动', steady_state: '稳态点核对',
  path_follow: '差速底盘路径跟踪', bus_can: '母线电压与热工况',
  fault_injection: '故障注入诊断', torque_speed: '机械特性（曲线）',
  slip_scan: '转矩—转差（曲线）', vf_scan: '恒压频比（曲线）', bode: '频率响应（曲线）',
};
const LOAD_NAMES = { none: '无负载', constant: '恒定负载', step: '阶跃负载',
                     ramp: '斜坡负载', fan: '风机型负载' };
const SENSOR_NAMES = { ideal: '理想反馈', quantized: '编码器量化', jitter: '带抖动',
                       biased: '带零偏', reversed: '方向接反' };
const FAULT_NAMES = {
  none: '无故障', encoder_reversed: '编码器方向接反', phase_swap: '相序接反（电机反转）',
  vbus_sag: '母线跌落', can_dropout: 'CAN 丢帧', sensor_bias: '传感器零偏',
  mechanical_jam: '机械卡死', units_confusion: '量纲混淆（度/弧度）',
};

const DEFAULT_SPEC = {
  kind: 'dc', scenario: 'open_loop', controller: 'open', load: 'none',
  duration: 3.0, target: 600, vdc: 24,
};

function renderLab(view) {
  const presets = state.presets.filter((preset) => {
    if (view === 'power') return ['svpwm', 'bode', 'current', 'sag'].includes(preset.id === 'svpwm-limits'
      ? 'svpwm' : preset.id);
    if (view === 'robot') return ['robot-path', 'four-bar', 'power-budget', 'can-load'].includes(preset.id);
    return true;
  });
  const spec = state.labSpec || raceDefault(view);
  state.labSpec = spec;
  const machine = state.limits.params[spec.kind];
  const scenarios = state.limits.scenarios[spec.kind] || [];
  const isCurve = ['torque_speed', 'slip_scan', 'vf_scan', 'bode'].includes(spec.scenario);

  viewRoot().innerHTML = heading(
    'LABORATORY <span>实时数值实验台</span>',
    view === 'power' ? '逆变器与调制实验室' : view === 'robot' ? '整机与机器人实验室' : '电机与闭环实验台',
    '每次运行都在本机实时求解电气—机械耦合微分方程。曲线不是预存动画，参数与数据都可以导出。')
    + `<div class="preset-row wide">${presets.map((preset) =>
        `<button class="chip" data-preset="${esc(preset.id)}">${esc(preset.title)}</button>`).join('')}</div>
    <div class="motor-layout">
      <section class="motor-controls">
        <div class="panel-title">实验设置 <span class="tag blue">${esc(MACHINE_NAMES[spec.kind])}</span></div>
        <form id="lab-form">
          <label class="field">电机类型
            <select name="kind">${Object.entries(MACHINE_NAMES).map(([key, label]) =>
              `<option value="${key}" ${spec.kind === key ? 'selected' : ''}>${label}</option>`).join('')}
            </select></label>
          <label class="field">实验场景
            <select name="scenario">${scenarios.map((key) =>
              `<option value="${key}" ${spec.scenario === key ? 'selected' : ''}>${esc(SCENARIO_NAMES[key] || key)}</option>`).join('')}
            </select></label>
          <label class="field">控制方式
            <select name="controller">${Object.entries(CONTROL_NAMES).map(([key, label]) =>
              `<option value="${key}" ${spec.controller === key ? 'selected' : ''}>${label}</option>`).join('')}
            </select></label>
          ${isCurve ? '' : `
          <div class="field-pair">
            <label class="field">目标 <span>rpm 或 A</span>
              <input name="target" type="number" step="1" value="${spec.target}"></label>
            <label class="field">母线电压 <span>V</span>
              <input name="vdc" type="number" min="5" max="400" step="1" value="${spec.vdc}"></label>
          </div>
          <div class="field-pair">
            <label class="field">仿真时长 <span>s</span>
              <input name="duration" type="number" min="0.01" max="10" step="0.01" value="${spec.duration}"></label>
            <label class="field">负载类型
              <select name="load">${Object.entries(LOAD_NAMES).map(([key, label]) =>
                `<option value="${key}" ${spec.load === key ? 'selected' : ''}>${label}</option>`).join('')}
              </select></label>
          </div>
          <div class="field-pair">
            <label class="field">负载值 <span>N·m</span>
              <input name="load_value" type="number" step="0.01" value="${spec.load_value ?? 0.05}"></label>
            <label class="field">位置反馈
              <select name="sensor">${Object.entries(SENSOR_NAMES).map(([key, label]) =>
                `<option value="${key}" ${(spec.sensor || 'ideal') === key ? 'selected' : ''}>${label}</option>`).join('')}
              </select></label>
          </div>`}
          <details class="advanced"><summary>电机参数与增益（点击展开）</summary>
            <div class="param-grid">${Object.entries(machine).map(([name, info]) => `
              <label class="field small">${esc(info.label)} <span>${esc(info.unit)}</span>
                <input name="param.${esc(name)}" type="number" step="any"
                  min="${info.min}" max="${info.max}"
                  value="${(spec.params && spec.params[name] !== undefined) ? spec.params[name] : info.default}"></label>`).join('')}
            </div>
            <div class="param-grid">${gainFields(spec)}</div>
          </details>
          <details class="advanced"><summary>故障注入（诊断训练）</summary>
            <div class="fault-grid">${state.limits.faults.filter((f) => f !== 'none').map((fault) => `
              <label class="checkbox-field"><input type="checkbox" name="fault.${esc(fault)}"
                ${(spec.faults || []).includes(fault) ? 'checked' : ''}>${esc(FAULT_NAMES[fault] || fault)}</label>`).join('')}
            </div>
            <p class="field-help">故障只作用于反馈与执行通道，物理状态仍然由真实模型推进——所以你会看到"控制变差"，而不是"曲线被伪造"。</p>
          </details>
          <button type="submit" class="primary full-width" ${state.running ? 'disabled' : ''}>
            ${state.running ? '正在计算…' : '运行实验'}</button>
          <button type="button" id="cancel-run" class="secondary full-width" ${state.running ? '' : 'hidden'}>停止计算</button>
        </form>
        <p class="field-help">时长上限 10 s、子步 ≥ 5 µs：这是为了保护本机服务，不是物理限制。</p>
      </section>
      <div class="motor-results">
        <div id="lab-status" class="motor-status" role="status">${
          state.result ? '上一次计算已完成，修改参数后可重新运行。'
                       : '选择预设或调整参数，然后点击"运行实验"。'}</div>
        <div id="lab-model-slot"></div>
        <div id="lab-metrics" class="motor-metrics"></div>
        <div id="lab-charts"></div>
        <div id="lab-notes"></div>
        <div class="button-row">
          <button id="export-run" class="secondary" ${state.runId && state.result ? '' : 'disabled'}>
            导出参数与原始数据</button>
        </div>
      </div>
    </div>`;

  // 实验台的模型面板：放在结果区最上方，让它成为"第一眼看到的东西"
  const slot = $('#lab-model-slot');
  if (slot) {
    const isRobot = Boolean(spec.robot_params) || spec.scenario === 'path_follow';
    slot.innerHTML = viewerMarkup({
      id: 'lab-model',
      title: isRobot ? '仿真机器人（跟随实验数据）' : '仿真电机（跟随实验数据）',
      subtitle: isRobot
        ? '拖动旋转；播放可看到底盘按你的数据行驶、四连杆同步动作'
        : '拖动旋转 · 滚轮缩放 · 播放可看到转子按你的数据转动',
      mode: state.display, height: 320, hasData: false,
      note: '运行实验后，这里的模型会由真实仿真数据驱动——不是预录动画。',
    });
    makeViewer('lab-model', {
      kind: isRobot ? 'robot' : 'motor',
      motorSpec: isRobot ? undefined : motorSpecFromConfig(spec),
      robotSpec: isRobot ? robotSpecFromLab(spec) : undefined,
      height: 320,
    });
    if (state.result && (state.result.series || []).length) {
      const viewer = state.viewers['lab-model'];
      if (viewer) {
        viewer.setRows(state.result.series);
        viewer.setIndex(0);
        document.querySelector('[data-model-note="lab-model"]').textContent =
          '这段数据来自上一次运行。改参数后重新运行，模型会跟着更新。';
      }
    }
  }

  const form = $('#lab-form');
  form.onchange = (event) => {
    // 切换电机类型或场景时重建表单，保证参数集与场景集同步
    if (event.target.name === 'kind' || event.target.name === 'scenario') {
      const next = collectSpec(form);
      state.labSpec = next;
      state.result = null;
      renderLab(view);
    }
  };
  form.onsubmit = (event) => { event.preventDefault(); runLab(collectSpec(form)); };
  const cancel = $('#cancel-run');
  if (cancel) cancel.onclick = cancelRun;
  const exportButton = $('#export-run');
  if (exportButton) exportButton.onclick = exportRun;
  paintResult();
}

/** 从实验配置里取出机器人几何参数，交给模型。 */
function robotSpecFromLab(spec) {
  const params = spec.robot_params || {};
  return {
    track: Math.round((params.track || 0.4) * 1000),
    wheelRadius: Math.round((params.wheel_radius || 0.0625) * 1000),
    wheelbase: 300,
  };
}

function raceDefault(view) {
  const preset = state.presets.find((item) => (
    view === 'power' ? item.id === 'svpwm-limits'
      : view === 'robot' ? item.id === 'robot-path' : item.id === 'first-motor'));
  return preset ? { ...DEFAULT_SPEC, ...preset.spec } : { ...DEFAULT_SPEC };
}

function gainFields(spec) {
  const gains = spec.gains || {};
  const names = spec.kind === 'dc'
    ? [['speed_kp', '速度环 Kp'], ['speed_ki', '速度环 Ki'], ['current_kp', '电流环 Kp'], ['current_ki', '电流环 Ki']]
    : spec.kind === 'pmsm_bldc'
      ? [['vq_kp', 'q 轴 Kp'], ['vq_ki', 'q 轴 Ki'], ['vd_kp', 'd 轴 Kp'], ['vd_ki', 'd 轴 Ki'], ['speed_kp', '速度环 Kp'], ['speed_ki', '速度环 Ki']]
      : [['kspeed_kp', '转差环 Kp'], ['kspeed_ki', '转差环 Ki'], ['speed_kp', '速度环 Kp']];
  return names.map(([name, label]) => `
    <label class="field small">${esc(label)}
      <input name="gain.${esc(name)}" type="number" step="any" value="${gains[name] ?? ''}"></label>`).join('');
}

function collectSpec(form) {
  const data = new FormData(form);
  const spec = {
    kind: data.get('kind'), scenario: data.get('scenario'),
    controller: data.get('controller'), load: data.get('load') || 'none',
    sensor: data.get('sensor') || 'ideal',
  };
  const numeric = ['target', 'vdc', 'duration', 'load_value'];
  for (const key of numeric) {
    const raw = data.get(key);
    if (raw !== null && raw !== '') spec[key] = Number(raw);
  }
  const params = {};
  const gains = {};
  const faults = [];
  for (const [key, value] of data.entries()) {
    if (key.startsWith('param.')) params[key.slice(6)] = Number(value);
    else if (key.startsWith('gain.')) {
      if (value !== '') gains[key.slice(5)] = Number(value);
    } else if (key.startsWith('fault.')) faults.push(key.slice(6));
  }
  if (Object.keys(params).length) spec.params = params;
  if (Object.keys(gains).length) spec.gains = gains;
  if (faults.length) spec.faults = faults;
  return spec;
}

async function runLab(spec) {
  state.labSpec = { ...spec };
  state.running = true;
  state.result = null;
  const status = $('#lab-status');
  if (status) status.textContent = '正在计算……';
  try {
    const started = await api('/sim/runs', 'POST', { spec });
    state.runId = started.id;
    pollRun(started.id);
  } catch (error) {
    state.running = false;
    if (status) status.innerHTML = `<span class="error-text">${esc(error.message)}</span>`;
    renderLab(state.view);
  }
}

function pollRun(id) {
  clearTimeout(state.pollTimer);
  const tick = async () => {
    try {
      const payload = await api(`/sim/runs/${id}`);
      if (payload.status === 'queued' || payload.status === 'running') {
        const status = $('#lab-status');
        if (status) status.textContent = '正在求解微分方程……';
        state.pollTimer = setTimeout(tick, 350);
        return;
      }
      state.running = false;
      state.result = payload.result || {};
      if (payload.status === 'failed') {
        const status = $('#lab-status');
        if (status) status.innerHTML =
          `<span class="error-text">${esc(payload.error || '计算失败。')}</span>`;
      }
      paintResult();
      const button = $('#lab-form button[type=submit]');
      if (button) { button.disabled = false; button.textContent = '运行实验'; }
      const cancel = $('#cancel-run');
      if (cancel) cancel.hidden = true;
      const exportButton = $('#export-run');
      if (exportButton) exportButton.disabled = payload.status !== 'succeeded';
    } catch (error) {
      state.running = false;
      toast(error.message);
    }
  };
  tick();
}

async function cancelRun() {
  if (!state.runId) return;
  try { await api(`/sim/runs/${state.runId}/cancel`, 'POST'); toast('已请求停止计算。'); }
  catch (error) { toast(error.message); }
}

function exportRun() {
  if (!state.runId) return;
  window.location.href = `/api/sim/runs/${state.runId}/export?token=${encodeURIComponent(state.token)}`;
}

function paintResult() {
  const container = $('#lab-charts');
  if (!container) return;
  const result = state.result;
  const metricsBox = $('#lab-metrics');
  const notesBox = $('#lab-notes');
  if (!result) {
    container.innerHTML = '<section class="chart-card"><canvas data-redraw="1" id="chart-empty" height="240"></canvas>'
      + `<div class="chart-caption">${esc(document.querySelector('#chart-empty') ? '' : '')}</div></section>`;
    const canvas = $('#chart-empty');
    if (canvas) {
      const draw = () => P.timeSeries(canvas, [], [], { empty: '运行实验后显示计算曲线' });
      canvas.__redraw = draw; draw();
    }
    if (metricsBox) metricsBox.innerHTML = '';
    if (notesBox) notesBox.innerHTML = '';
    return;
  }
  const rows = result.series || [];
  const keys = result.keys || [];
  const kind = result.kind;
  const context = {
    rpm: '转速 / rpm', current: '电流 / A', voltage: '电压 / V', id: 'd 轴电流 / A',
    iq: 'q 轴电流 / A', ia: 'A 相电流 / A', torque: '转矩 / N·m', slip: '转差 / rad/s',
  };

  const charts = [];
  if (rows.length && rows[0].rpm !== undefined) {
    charts.push({ id: 'chart-speed', title: '转速', lines: [
      { key: 'rpm', color: P.COLORS.blue },
      ...(keys.includes('measured_rpm') ? [{ key: 'measured_rpm', color: P.COLORS.orange }] : []),
      ...(keys.includes('target_rpm') ? [{ key: 'target_rpm', color: P.COLORS.gray, dash: [5, 5] }] : []),
    ], yLabel: 'rpm' });
  }
  if (keys.includes('current')) {
    charts.push({ id: 'chart-current', title: '电流', height: 180,
                  lines: [{ key: 'current', color: P.COLORS.teal }], yLabel: 'A' });
  }
  if (keys.includes('iq')) {
    charts.push({ id: 'chart-idq', title: 'dq 电流', height: 180, lines: [
      { key: 'iq', color: P.COLORS.blue }, { key: 'id', color: P.COLORS.red }], yLabel: 'A' });
  }
  if (keys.includes('ia')) {
    charts.push({ id: 'chart-phase', title: '三相电流', height: 180, lines: [
      { key: 'ia', color: P.COLORS.blue }, { key: 'ib', color: P.COLORS.orange },
      { key: 'ic', color: P.COLORS.teal }], yLabel: 'A' });
  }
  if (keys.includes('slip')) {
    charts.push({ id: 'chart-slip', title: '转差与转子磁链', height: 180, lines: [
      { key: 'slip', color: P.COLORS.purple }, { key: 'psi_r', color: P.COLORS.teal }], yLabel: '' });
  }
  if (keys.includes('voltage') || keys.includes('vbus')) {
    charts.push({ id: 'chart-voltage', title: '电压', height: 180, lines: [
      ...(keys.includes('voltage') ? [{ key: 'voltage', color: P.COLORS.purple }] : []),
      ...(keys.includes('vbus') ? [{ key: 'vbus', color: P.COLORS.orange }] : []),
    ], yLabel: 'V' });
  }
  if (keys.includes('x')) {
    charts.push({ id: 'chart-pose', title: '底盘轨迹（俯视图）', height: 320, special: 'top' });
    charts.push({ id: 'chart-wheels', title: '左右轮转速', height: 200, lines: [
      { key: 'left_rpm', color: P.COLORS.blue }, { key: 'right_rpm', color: P.COLORS.orange },
      { key: 'left_ref_rpm', color: P.COLORS.gray, dash: [5, 5] },
      { key: 'right_ref_rpm', color: P.COLORS.gray, dash: [2, 4] }], yLabel: 'rpm' });
  }
  if (keys.includes('temperature_c')) {
    charts.push({ id: 'chart-temp', title: '温度', height: 160,
                  lines: [{ key: 'temperature_c', color: P.COLORS.red }], yLabel: '°C' });
  }

  container.innerHTML = charts.map((chart) => `
    <section class="chart-card">
      <h3>${esc(chart.title)}</h3>
      <canvas id="${chart.id}" data-redraw="1"></canvas>
      <div class="chart-caption">${esc(chart.yLabel || '')}</div>
    </section>`).join('') || '<p class="muted">这次运行没有可绘制的时间序列。</p>';

  for (const chart of charts) {
    const canvas = $(`#${chart.id}`);
    if (!canvas) continue;
    const draw = () => {
      if (chart.special === 'top') {
        const path = [];
        if (state.labSpec && state.labSpec.scenario === 'path_follow') {
          const radius = 1.5;
          for (let i = 0; i <= 120; i += 1) {
            const theta = (2 * Math.PI * i) / 120;
            path.push({ x: radius * Math.sin(theta), y: radius * (1 - Math.cos(theta)) });
          }
        }
        P.topView(canvas, rows, {
          path,
          // 只有真的采了里程计数据才画里程计轨迹
          showOdom: rows.some((row) => row.odom_x !== undefined),
          height: chart.height,
        });
      } else {
        P.timeSeries(canvas, rows, chart.lines, { height: chart.height, yLabel: chart.yLabel });
      }
    };
    canvas.__redraw = draw;
    draw();
  }

  // 把这次运行的真实数据交给模型：时间轴可拖动，播放按真实时间推进
  const labViewer = state.viewers['lab-model'];
  if (labViewer && rows.length) {
    labViewer.setRows(rows);
    labViewer.setIndex(0);
    const note = document.querySelector('[data-model-note="lab-model"]');
    if (note) {
      note.textContent = `${rows.length} 帧数据已载入模型。`
        + '点“播放”看运动，拖动时间轴可逐帧对照曲线与模型姿态。';
    }
  }

  const metrics = result.metrics || {};
  const labels = {
    final_rpm: ['最终转速', 'rpm'], target_rpm: ['目标转速', 'rpm'],
    overshoot_percent: ['超调', '%'], settling_time_s: ['调节时间', 's'],
    steady_state_error_rpm: ['稳态误差', 'rpm'], peak_current_a: ['峰值电流', 'A'],
    rms_current_a: ['电流有效值', 'A'], saturation_percent: ['饱和占比', '%'],
    final_id: ['稳态 i_d', 'A'], final_iq: ['稳态 i_q', 'A'],
    final_temperature_c: ['终值温度', '°C'], thermal_peak_c: ['峰值温度', '°C'],
    bus_sag_v: ['母线跌落', 'V'], final_current_a: ['稳态电流', 'A'],
    current_error_a: ['电流误差', 'A'], odom_max_error_m: ['里程计最大误差', 'm'],
    path_max_error_m: ['路径最大误差', 'm'], final_position_rad: ['终值位置', 'rad'],
    position_error_rad: ['位置误差', 'rad'], synchronous_rpm: ['同步转速', 'rpm'],
    final_slip_rad_s: ['稳态转差', 'rad/s'], final_psi_r: ['稳态转子磁链', 'Wb'],
  };
  const cards = Object.entries(metrics)
    .filter(([key, value]) => labels[key] && value !== null && value !== undefined)
    .map(([key, value]) => `<div><span>${labels[key][0]}</span><b>${
      typeof value === 'number' ? (Math.abs(value) < 0.001 && value !== 0 ? value.toExponential(2) : value.toFixed(Math.abs(value) >= 100 ? 0 : 3)) : esc(String(value))
      }<small>${labels[key][1]}</small></b></div>`).join('');
  if (metrics.diverged) {
    cards += '<div class="danger-card"><span>结果</span><b>发散</b></div>';
  }
  if (metricsBox) metricsBox.innerHTML = cards;

  const notes = [...(result.warnings || []), ...(result.notes || [])];
  if (notesBox) {
    notesBox.innerHTML = notes.length
      ? notes.map((note) => `<p class="warning-note">${esc(note)}</p>`).join('')
      : '';
  }
}

/* ------------------------------------------------------------------ 曲线实验 */

async function runCurve(preset) {
  viewRoot().innerHTML = heading('CURVE STUDY <span>曲线族实验</span>', preset.title, preset.summary,
    '<div class="lesson-meta"><span>正在计算</span></div>');
  try {
    const payload = await api('/sim/curves', 'POST', { curve: preset.curve, spec: preset.spec });
    state.curve = payload;
    renderCurve(preset, payload);
  } catch (error) {
    viewRoot().innerHTML = heading('CURVE STUDY', preset.title, preset.summary)
      + `<div class="error-box">${esc(error.message)}</div>
         <button class="secondary" data-view="motor">返回实验台</button>`;
  }
}

function renderCurve(preset, payload) {
  const chartId = 'curve-chart';
  let body = '';
  if (payload.kind === 'family') {
    body = `<section class="chart-card"><h3>${esc(payload.y_label || '')} 对 ${esc(payload.x_label || '')}</h3>
      <canvas id="${chartId}" data-redraw="1"></canvas>
      <div class="chart-caption">${esc(payload.x_label || '')} · ${esc(payload.y_label || '')}</div></section>`;
  } else if (payload.kind === 'traces') {
    body = `<section class="chart-card"><h3>阶跃响应族</h3>
      <canvas id="${chartId}" data-redraw="1"></canvas></section>` + P.table(
      ['参数组', '超调 %', '调节时间 s', '峰值电流 A', '是否发散'],
      payload.families.map((f) => [f.label, f.metrics.overshoot_percent,
        f.metrics.settling_time_s, f.metrics.peak_current_a, f.metrics.diverged ? '是' : '否']));
  } else if (payload.kind === 'bode') {
    body = `<section class="chart-card"><h3>电流环频率响应</h3>
      <canvas id="${chartId}" data-redraw="1"></canvas>
      <div class="chart-caption">幅值与相位对频率；横轴对数</div></section>` + P.table(
      ['频率 Hz', '幅值 dB', '相位 °'],
      payload.points.map((p) => [p.frequency_hz, p.gain_db, p.phase_deg]));
  } else if (payload.kind === 'bars') {
    body = `<section class="chart-card"><h3>量化台阶对比</h3>
      <canvas id="${chartId}" data-redraw="1"></canvas></section>`;
  } else if (payload.kind === 'xy') {
    body = `<section class="chart-card"><h3>曲柄角与摇杆角</h3>
      <canvas id="${chartId}" data-redraw="1"></canvas></section>`;
  } else if (payload.kind === 'table') {
    if (payload.rows && payload.rows.length && 'load_percent' in payload.rows[0]) {
      body = `<section class="chart-card"><h3>总线负载率</h3>
        <canvas id="${chartId}" data-redraw="1"></canvas></section>`;
    }
    const keys = payload.rows && payload.rows.length ? Object.keys(payload.rows[0]) : [];
    body += P.table(keys, (payload.rows || []).map((row) => keys.map((key) => row[key])));
    if (payload.budget) {
      body += `<section class="lesson-body"><h2>预算结果</h2>${P.table(
        ['项目', '数值'],
        [['峰值电流 A', payload.budget.peak_current_a],
         ['平均电流 A', payload.budget.average_current_a],
         ['推荐容量 A（含 25% 裕度）', payload.budget.recommended_a]])}</section>`;
    }
  }
  const notes = (payload.notes || []).map((note) => `<p class="warning-note">${esc(note)}</p>`).join('');
  const back = `<button class="secondary" data-view="motor">返回实验台</button>`;
  viewRoot().innerHTML = heading('CURVE STUDY <span>曲线族实验</span>', preset.title, preset.summary,
    `<div class="lesson-meta"><span>用时 ${bytes(payload.elapsed_s)} s</span>
     ${payload.bandwidth_hz ? `<span>−3 dB 带宽 ≈ ${payload.bandwidth_hz} Hz</span>` : ''}</div>`)
    + `<div class="curve-layout">${body}${notes}${back}</div>`;

  const canvas = $(`#${chartId}`);
  if (canvas) {
    const draw = () => {
      if (payload.kind === 'family') {
        P.family(canvas, payload.families, {
          xKey: payload.x_label && payload.x_label.includes('转速') ? 'speed_rpm' : 'speed_rpm',
          yKey: 'torque_nm', xLabel: payload.x_label, yLabel: payload.y_label,
          markers: payload.markers,
        });
      } else if (payload.kind === 'traces') {
        P.traceFamily(canvas, payload.families, { yLabel: 'rpm' });
      } else if (payload.kind === 'bode') {
        P.bode(canvas, payload.points, { bandwidth: payload.bandwidth_hz });
      } else if (payload.kind === 'bars') {
        P.bars(canvas, payload.bars, { yLabel: payload.y_label });
      } else if (payload.kind === 'xy') {
        P.traceFamily(canvas, [{ label: '摇杆角', points: payload.points }],
          { xKey: 'crank_deg', yKey: 'rocker_deg', xLabel: '曲柄角 °', yLabel: '摇杆角 °' });
      } else if (payload.kind === 'table' && payload.rows && payload.rows.length
                 && 'load_percent' in payload.rows[0]) {
        P.traceFamily(canvas, payload.families.map((f) => ({
          label: f.label, points: f.points.map((p) => ({ t: p.period_ms, rpm: p.load_percent })),
        })), { xLabel: '报文周期 ms', yLabel: '负载率 %' });
      }
    };
    canvas.__redraw = draw;
    draw();
  }
}

/* ------------------------------------------------------------------ 工具视图 */

function renderSoftware() {
  const health = state.health || {};
  viewRoot().innerHTML = heading('ENGINEERING TOOLBOX <span>按项目准备</span>',
    '软件与实验环境',
    '每一项都写清用途、执行位置、验证方法与常见问题。本课程的核心实验不需要安装任何东西。')
    + `<section class="environment-card"><div>
        <div class="eyebrow">本机环境</div>
        <h2>${health.docker ? '容器环境可用' : '网页实验台已就绪（无需额外安装）'}</h2>
        <p>${esc((health.issues || []).join(' ') || '课程服务运行正常。')}</p>
        <div class="environment-facts">
          <span>Python ${esc(health.python || '—')}</span>
          <span>磁盘可用 ${esc(String(health.disk_free_gb ?? '—'))} GB</span>
          <span>Docker ${health.docker ? '✓ 已安装' : '未安装'}</span>
        </div></div>
        <button class="secondary" id="refresh-health">重新检查</button></section>
      <div class="software-list">${state.software.map((tool, index) => `
        <details class="software-card" id="software-${esc(tool.id)}" data-stage="${esc(tool.stage)}">
          <summary><span class="software-number">${String(index + 1).padStart(2, '0')}</span>
            <span class="software-card-title"><b>${esc(tool.name)}</b>
              <small>${esc(tool.category)} · ${esc(tool.summary)}</small></span>
            <span class="tag ${tool.stage === '基础必修' ? 'blue' : ''}">${esc(tool.stage)}</span></summary>
          <div class="software-content">
            <div class="software-properties">
              <p><b>执行位置</b>${esc(tool.scope)}</p>
              <p><b>资源要求</b>${esc(tool.resource)}</p>
              <p><b>许可说明</b>${esc(tool.license)}</p></div>
            <a href="${esc(tool.source)}" target="_blank" rel="noopener noreferrer">查看官方文档 ↗</a>
            ${(tool.steps || []).map((step, n) => `
              <section><h3><span class="step-count">${n + 1}</span>${esc(step.title)}</h3>
                <p>${esc(step.explanation)}</p>
                ${step.command ? `<div class="command-card"><div class="command-top">
                  <span>终端命令</span><button class="copy-button" data-copy="${esc(step.command)}">复制命令</button></div>
                  <pre><code>${esc(step.command)}</code></pre></div>` : ''}
                <p class="expected"><b>成功表现</b> ${esc(step.expected)}</p>
                <p class="troubleshooting"><b>遇到问题</b> ${esc(step.troubleshooting)}</p></section>`).join('')}
            ${tool.verify ? `<div class="learning-note"><b>安装后的验证</b><pre>${esc(tool.verify)}</pre></div>` : ''}
          </div></details>`).join('')}</div>`;
  $('#refresh-health').onclick = async () => { await refreshHealth(); renderSoftware(); };
}

function renderPortfolio() {
  const preferences = state.progress.preferences || {};
  const lessons = state.course.lessons;
  const done = lessons.filter((item) => completed(item.id)).length;
  const checked = state.progress.lessons.filter((item) => item.checked).length;
  viewRoot().innerHTML = heading('LEARNING RECORD <span>把过程变成作品</span>',
    '学习与工程作品',
    '自动记录、项目文件与你的分析，共同构成可复现的学习证据。')
    + `<div class="stat-grid">
        <div><span>课程单元完成</span><b>${done}<small> / ${lessons.length}</small></b></div>
        <div><span>实践检查通过</span><b>${checked}<small> / ${lessons.length}</small></b></div>
        <div><span>实验运行次数</span><b>${state.progress.attempts || 0}</b></div>
        <div><span>RoboCON 设计包</span><b class="small-stat">可导出</b></div></div>
      <div class="portfolio-grid">
        <section class="lesson-body">
          <h2>逐周进度</h2>
          ${state.course.weeks.map((week) => `
            <div class="project-progress"><div>
              <b>${String(week.id).padStart(2, '0')} · ${esc(week.project)}</b>
              <button class="text-link" data-lesson="${esc((lessons.find((l) => l.week === week.id) || {}).id || '')}">继续学习 →</button>
            </div>
            <div class="lesson-checks">${lessons.filter((item) => item.week === week.id).map((item) => {
              const record = progressOf(item.id);
              return `<div><span>${esc(item.title)}</span>
                <small>${record.read_at ? '已读' : '未读'} · 理解 ${record.score || 0}% · ${
                  record.checked ? '实践通过' : '实践待检查'}</small></div>`;
            }).join('')}</div></div>`).join('')}
        </section>
        <section class="lesson-body">
          <h2>你的作品说明</h2>
          <form id="portfolio-form">
            <label class="field">姓名<input name="studentName" maxlength="100" value="${esc(preferences.studentName || '')}"></label>
            <label class="field">队伍 / 项目组<input name="teamName" maxlength="100" value="${esc(preferences.teamName || '')}"></label>
            <label class="field">项目名称<input name="projectTitle" maxlength="200" value="${esc(preferences.projectTitle || 'RoboCON 电控系统设计')}"></label>
            <label class="field">项目复盘<textarea name="reflection" rows="10" maxlength="5000"
              placeholder="任务是什么？你选了哪些电机与减速比？控制周期怎么定？遇到什么问题、用什么证据定位？还有什么局限？">${esc(preferences.reflection || '')}</textarea></label>
            <button class="primary" type="submit">保存作品说明</button>
          </form>
          <div class="learning-note"><b>课程作者与学生作者独立记录</b>
            <p>课程作者：Connor He 和 Astra。你的作品应写明自己的姓名、贡献与实验条件。</p></div>
          <div class="button-row">
            <button class="secondary" id="download-portfolio">导出学习与设计包</button>
            <button class="secondary" id="download-teacher">教师/组长使用说明</button>
          </div>
        </section></div>`;

  $('#portfolio-form').onsubmit = async (event) => {
    event.preventDefault();
    try {
      await api('/preferences', 'POST', Object.fromEntries(new FormData(event.target)));
      state.progress = await api('/progress');
      toast('作品说明已保存在本机。');
    } catch (error) { toast(error.message); }
  };
  $('#download-portfolio').onclick = () =>
    download('/portfolio/export', 'robocon-control-portfolio.md');
  $('#download-teacher').onclick = () =>
    download('/teacher', 'teacher-guide.md');
}

async function download(path, filename) {
  try {
    const response = await fetch(`/api${path}`, { headers: { 'X-Course-Token': state.token } });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ error: '导出失败。' }));
      throw new Error(payload.error);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (error) { toast(error.message); }
}

async function savePreference(key, value) {
  try { await api('/preferences', 'POST', { [key]: value }); } catch { /* 非关键 */ }
}

async function refreshHealth() {
  try {
    state.health = await api('/health');
    $('#environment-label').textContent = state.health.docker ? '容器环境已安装' : '网页实验台已就绪';
    $('.status-dot').classList.toggle('ready', true);
  } catch {
    $('#environment-label').textContent = '本地服务未连接';
  }
}

/* ------------------------------------------------------------------ 事件 */

document.addEventListener('click', async (event) => {
  const view = event.target.closest('[data-view]');
  if (view) { navigate(view.dataset.view); return; }
  const lesson = event.target.closest('[data-lesson]');
  if (lesson) { navigate('learn', lesson.dataset.lesson); return; }
  const tool = event.target.closest('[data-software]');
  if (tool) {
    navigate('software');
    setTimeout(() => {
      const card = $(`#software-${CSS.escape(tool.dataset.software)}`);
      if (card) { card.open = true; card.scrollIntoView({ block: 'start' }); }
    }, 60);
    return;
  }
  const copy = event.target.closest('[data-copy]');
  if (copy) {
    try {
      await navigator.clipboard.writeText(copy.dataset.copy);
      const text = copy.textContent;
      copy.textContent = '已复制';
      setTimeout(() => { copy.textContent = text; }, 1500);
    } catch { toast('浏览器未允许剪贴板访问，请手动选中复制。'); }
    return;
  }
  const preset = event.target.closest('[data-preset]');
  if (preset) {
    const found = state.presets.find((item) => item.id === preset.dataset.preset);
    if (!found) return;
    if (found.curve) {
      await runCurve(found);
      return;
    }
    state.labSpec = { ...found.spec };
    state.result = null;
    state.runId = null;
    if (state.view === 'learn') {
      // 从课程单元里点实验：切到实验台并自动运行
      navigate('motor');
      setTimeout(() => renderLab('motor'), 0);
    } else {
      renderLab(state.view);
    }
    return;
  }
  const modeButton = event.target.closest('[data-model-mode]');
  if (modeButton) {
    setDisplayMode(modeButton.dataset.modelMode);
    return;
  }
  const playButton = event.target.closest('[data-model-play]');
  if (playButton) {
    const viewer = state.viewers[playButton.dataset.modelPlay];
    if (viewer) viewer.toggle();
    return;
  }
  const stepButton = event.target.closest('[data-model-step]');
  if (stepButton) {
    const viewer = state.viewers[stepButton.dataset.modelStep];
    if (viewer) {
      viewer.pause();
      viewer.setIndex(viewer.index + Number(stepButton.dataset.delta));
      updateModelControls(stepButton.dataset.modelStep, viewer.state());
    }
    return;
  }
  const hint = event.target.closest('[data-hint]');
  if (hint) {
    const box = $(`#hint-${CSS.escape(hint.dataset.hint)}`);
    if (box) box.hidden = !box.hidden;
    return;
  }
  const solution = event.target.closest('[data-solution]');
  if (solution) {
    const box = $(`#solution-${CSS.escape(solution.dataset.solution)}`);
    if (box) box.hidden = !box.hidden;
  }
});

// 时间轴：拖动时暂停播放，逐帧查看（这是"把曲线和物理对上"的关键交互）
document.addEventListener('input', (event) => {
  const scrub = event.target.closest('[data-model-scrub]');
  if (!scrub) return;
  const viewer = state.viewers[scrub.dataset.modelScrub];
  if (!viewer) return;
  viewer.pause();
  viewer.setIndex(Number(scrub.value));
  updateModelControls(scrub.dataset.modelScrub, viewer.state());
});

$('.menu-toggle').onclick = () => $('#sidebar').classList.toggle('open');
/** 切换 3D / 2D 显示模式，并记住选择。 */
function setDisplayMode(mode) {
  if (mode !== '2d' && mode !== '3d') return;
  state.display = mode;
  savePreference('displayMode', mode);
  $('#mode-button').textContent = mode === '3d' ? '3D 模型' : '2D 模型';
  for (const viewer of Object.values(state.viewers)) viewer.setMode(mode);
  refreshModelButtons();
}

function refreshModelButtons() {
  document.querySelectorAll('.model-mode [data-model-mode]').forEach((button) => {
    button.classList.toggle('active', button.dataset.modelMode === state.display);
  });
}

$('#mode-button').onclick = () => setDisplayMode(state.display === '3d' ? '2d' : '3d');
$('#environment-button').onclick = () => navigate('software');
window.addEventListener('hashchange', () => { state.result = null; state.runId = null; render(); });

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => P.redrawAll(document), 140);
});

/* ------------------------------------------------------------------ 启动 */

async function init() {
  try {
    const session = await fetch('/api/session');
    if (!session.ok) throw new Error('请使用 ./start.sh 启动完整本地服务。');
    state.token = (await session.json()).token;
    const [course, software, progress, limits, presets] = await Promise.all([
      api('/content/curriculum'), api('/content/software'), api('/progress'),
      api('/content/limits'), api('/sim/presets'),
    ]);
    state.course = course;
    state.software = software;
    state.progress = progress;
    state.limits = limits;
    state.presets = presets;
    state.lesson = progress.preferences.lastLesson
      || (course.lessons[0] && course.lessons[0].id);
    // 显示模式：跟随后端保存的偏好；没有则按设备能力给个默认值
    const saved = progress.preferences.displayMode;
    state.display = saved === '2d' || saved === '3d'
      ? saved
      : (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2 ? '2d' : '3d');
    $('#mode-button').textContent = state.display === '3d' ? '3D 模型' : '2D 模型';
    await refreshHealth();
    render();
    registerTools();
  } catch (error) {
    $('#environment-label').textContent = '服务尚未就绪';
    viewRoot().innerHTML = `<div class="error-box">
      <b>课程服务尚未就绪</b><p>${esc(error.message)}</p>
      <p>请在项目目录执行 <code>./start.sh</code>，然后刷新本页。</p></div>`;
  }
}

/** 把课程能力注册成宿主工具（存在时才注册，不影响独立运行）。 */
function registerTools() {
  const context = document.modelContext;
  if (!context || !context.registerTool) return;
  const lifecycle = new AbortController();
  const tools = [
    {
      name: 'read_course_progress',
      description: '读取本机课程学习进度与实验运行次数。',
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      annotations: { readOnlyHint: true },
      execute: () => api('/progress'),
    },
    {
      name: 'open_course_lesson',
      description: '打开指定课程单元。不执行任何仿真、不标记完成。',
      inputSchema: { type: 'object', properties: { lessonId: { type: 'string' } },
                     required: ['lessonId'], additionalProperties: false },
      execute: async (input) => {
        if (!state.course.lessons.some((item) => item.id === input.lessonId)) {
          throw new Error('未知课程单元。');
        }
        navigate('learn', input.lessonId);
        return { lessonId: input.lessonId, title: currentLesson().title };
      },
    },
    {
      name: 'list_lab_presets',
      description: '列出可用的实验预设及其参数，便于引导学员运行指定实验。',
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      annotations: { readOnlyHint: true },
      execute: () => state.presets.map((preset) =>
        ({ id: preset.id, title: preset.title, curve: preset.curve || null })),
    },
  ];
  for (const tool of tools) {
    try {
      Promise.resolve(context.registerTool(tool, { signal: lifecycle.signal })).catch(() => {});
    } catch { /* 宿主不支持时忽略 */ }
  }
  window.addEventListener('pagehide', () => lifecycle.abort(), { once: true });
}

init();
