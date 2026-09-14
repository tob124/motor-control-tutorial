/* Node 导入钩子：把浏览器风格的绝对路径（'/model.js'）映射到仓库内相对路径。
 *
 * 浏览器里 `import { x } from '/model.js'` 是标准做法（向站点根请求）。
 * Node 会把它当成文件系统绝对路径，找不到。为了让同一份几何代码
 * 在 Node 里可测（不复制、不构建），这里加一个最小解析钩子，只做路径重写。
 *
 * 用法（Node 22）：
 *   node --experimental-loader ./tests/browser-resolve.mjs tests/model.test.mjs
 */

import { register } from 'node:module';
import { pathToFileURL } from 'node:url';

const ROOT = new URL('../dist/', import.meta.url);

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('/') && /\.(m?js)$/.test(specifier)) {
    return { url: new URL(specifier.slice(1), ROOT).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

// 同时支持 `node --import` 的用法
register(import.meta.url, pathToFileURL('./'));
