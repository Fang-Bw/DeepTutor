# 前端运行时性能基准

本工具用 Playwright 驱动真实聊天页面，在本机生成确定性的会话、流式响应、工具事件及工作簿，测量长内容渲染和交互表现。它用于建立后续性能优化的基线，不能用来评估模型速度、后端吞吐量或网络性能。

## 1. 与现有体积预算的区别

| 命令 | 测量对象 | 不能说明什么 |
| --- | --- | --- |
| `npm run perf:check` | 已构建路由的 JavaScript 体积预算 | 不能直接得出浏览器卡顿程度 |
| `npm run perf:runtime` | 真实页面在合成负载下的帧间隔、长任务、内容就绪时间、输入响应和 DOM 数量 | 不等于生产环境的用户体验指标或模型端到端延迟 |

两类检查互相补充。基准没有添加流式缓存、虚拟化或改变生产渲染路径，也没有设置未经测量的毫秒级性能门槛。只有内容正确显示和分页 DOM 数量等确定性条件被用作断言。

## 2. 场景

每个聊天场景先加载相同的 **20 组历史问答**，通过真实输入框提交问题，再由模拟 WebSocket 发送协议事件。真实传输客户端、状态更新、Markdown、KaTeX、Mermaid 和工具轨迹组件继续参与执行。

| 场景 | 合成负载 | 完成检查 |
| --- | --- | --- |
| `long-markdown` | 80 节标题、长段落和列表 | 最终标记出现在助手回答中 |
| `math` | 60 节公式，每节 2 个行内公式和 1 个块公式 | 最终标记及至少 180 个 KaTeX 元素 |
| `mermaid` | 8 个独立流程图 | 最终标记及至少 8 个 Mermaid SVG |
| `tool-events` | 80 组工具调用和工具结果，然后输出回答 | 最终回答显示；测量结束后验证真实页面出现 `80 tool calls`，避免仅终答被消费而假通过 |
| `spreadsheet paging` | 1200 行 × 80 列的真实 XLSX，强制进入浏览器回退预览 | 首屏与下一页各 50 行、3050 个单元格（含行号列），上一页数据被移除 |

流式文本按 **200 字符**切片，每条事件发送后至少等待 **16 ms**。因此总时长包含刻意加入的输入节奏，不能将总时长直接称为“渲染耗时”。同一次流式输出期间，还会向输入框键入一个未提交的草稿，观察输入事件到下一帧的时间。

工作簿在测试进程中生成，浏览器仍运行实际 ExcelJS 解码和分页组件。工作簿解析、首次加载组件和抽屉动画均包含在打开预览的样本中；翻页另外采样。翻页采用正常键盘操作：先聚焦“Next page”，再开始计时并按 Enter，避免开发模式指示器遮挡右下角按钮；不使用强制点击、不移除界面元素，报告记录此激活方式。该基准证明的是可见 DOM 的上限，不能证明 ExcelJS 只解析了当前页或完整工作簿的内存占用已经受到限制。

## 3. 隔离方式

- 每次样本使用新的浏览器上下文，不加载个人登录状态、现有会话或本地存储。
- 所有 `/api/` 请求和应用 WebSocket 都在 Playwright 中截获，不连接后端、不调用 LLM、不产生模型费用。开发模式下，仅放行同一前端的 `/_next/webpack-hmr` 热更新握手，确保 Next 正常完成页面初始化。
- API 返回的内容仅来自代码内的固定夹具。固定任务板没有变化，`/api/task-board/events` 返回 HTTP 204，明确终止 EventSource 重连。未知 API 路径返回空对象并记入报告；这些通常是课程、推荐等辅助接口的合成空响应。建立正式基线前，应检查报告的 `unexpectedApiPaths`，确认没有因缺少必要数据而跳过待测 UI 或产生额外错误；未知路径不能自动视为正确夹具。
- 第三方请求和未提供夹具的 `/files/` 请求被阻止；仅从指定前端获取页面和静态资源。
- 前端进程本身依旧按其启动配置运行。若需要完全隔离的部署环境，应启动独立的前端实例；基准的网络隔离覆盖浏览器请求，不替代服务端运行环境隔离。

不要将基准连接到需要真实登录的生产站点。它的用途是本地源码版本对比。

## 4. 运行

先按[本地运行指南](./RUNNING_LOCALLY.md)启动前端。后端不参与夹具执行。

如未安装 Playwright Chromium，在前端目录显式安装一次：

```powershell
Set-Location D:\codex_base\DeepTutor\web
npx playwright install chromium
```

也可使用电脑上已经安装的 Chrome 或 Edge，无需下载 Playwright 的 Chromium。先选择一个已安装的浏览器通道，再运行后文的命令：

```powershell
$env:BENCHMARK_BROWSER_CHANNEL = 'chrome'
# 使用 Edge 时改为：$env:BENCHMARK_BROWSER_CHANNEL = 'msedge'
```

只接受 `chrome`、`msedge`；未设置时使用 Playwright 自带的 Chromium。报告记录实际浏览器版本与通道，对比前后版本应保持一致。该选项仍使用隔离的临时浏览器上下文，不复用系统浏览器的个人登录资料。

使用当前本地开发服务器快速检查：

```powershell
Set-Location D:\codex_base\DeepTutor\web
$env:WEB_BASE_URL = 'http://127.0.0.1:3782'
$env:BENCHMARK_SERVER_MODE = 'development'
$env:BENCHMARK_SAMPLES = '5'
npm run perf:runtime
```

仅运行一种负载：

```powershell
npm run perf:runtime -- --grep 'runtime math'
npm run perf:runtime -- --grep 'runtime spreadsheet'
```

默认关闭 Playwright trace，避免截图与 DOM 快照采集干扰性能数字。诊断场景失败时可临时启用：

```powershell
$env:BENCHMARK_TRACE = '1'
npm run perf:runtime -- --grep 'runtime tool-events'
Remove-Item Env:BENCHMARK_TRACE
```

启用后仅保留失败场景的 trace，但成功场景运行期间仍会采集；启用与关闭 trace 的数据不能直接比较。报告记录实际 trace 设置。

正式版本比较应使用同一硬件、浏览器版本、窗口大小、服务器模式和采样数量。推荐对两份待比较代码分别执行 `npm run build`，再使用独立端口运行 `npm run start -- --hostname 127.0.0.1 --port 3301`，并设置：

```powershell
$env:WEB_BASE_URL = 'http://127.0.0.1:3301'
$env:BENCHMARK_SERVER_MODE = 'production'
$env:BENCHMARK_SAMPLES = '10'
npm run perf:runtime
```

`BENCHMARK_SERVER_MODE` 是人工记录的环境标签，工具不会自动证明服务器处于生产模式。不要将开发模式与生产模式样本直接对比。开发模式的编译、开发工具和严格模式额外工作可能改变结果。

默认每个场景执行 **1 个排除统计的预热样本 + 5 个有效样本**。样本数支持 1–30。预热样本用于暖前端编译/资源服务；每个样本仍使用新的浏览器上下文，浏览器端不是跨样本热缓存。Playwright 请求拦截也会影响缓存行为，所有比较必须使用同一夹具。

## 5. 输出与解读

每个场景输出到 `web/test-results/runtime-benchmark/<场景目录>/runtime-benchmark.json`，同时作为 Playwright 附件保存。测试输出目录通常被 Git 忽略；需要长期对比时，应在下一次运行前将选定报告保存在专门的基线归档中。

报告包含 Git 提交、时间、Node/浏览器版本、操作系统、CPU、内存、viewport、服务器模式标签、场景规模，以及全部原始样本和汇总：

| 指标 | 含义及边界 |
| --- | --- |
| `elapsedMs` | 浏览器时钟上的提交/点击/键盘激活前到完成 DOM 条件后两次动画帧；包含夹具节奏、懒加载、执行和布局等待 |
| `deliveryMs` | 测量开始到事件发送结束或点击/键盘激活返回；不能解释为网络延迟 |
| `afterDeliveryMs` | 发送结束或点击/键盘激活返回到完成 DOM 条件后两次动画帧；不是精确的屏幕像素呈现时间 |
| `frameGapsMs` | 实际 `requestAnimationFrame` 间隔；长间隔可提示卡顿，但不直接等于丢帧数 |
| `longTasksMs` | 浏览器 Long Tasks API 报告的超过 50 ms 的主线程任务；报告同时注明 API 是否可用 |
| `inputToFrameMs` | 合成输入事件时间戳到下一帧回调，不等同于真实用户 INP 或硬件输入延迟 |
| `mutationBatches` | DOM MutationObserver 回调数，不是 React 组件 render 次数 |
| `domElements` | 测量结束时整页 DOM 元素数，包括外壳和历史消息 |
| `tableRows/tableCells` | 表格 DOM 规模，验证分页没有把完整工作簿挂到页面上 |
| `renderedMath/renderedDiagrams` | 完成时实际公式和图形元素数量，避免把占位符误认为渲染完成 |

汇总包含样本数、中位数、p95 和最大值。中位数取中间值（偶数样本取中间两值平均）；p95 使用 nearest-rank。5 个样本的 p95 就是最大值，只能作为初步观察，不能当成稳定分位数。帧间隔、输入延迟和长任务汇总会合并所有有效样本中的原始观测，报告同时保留逐次数据。

测量探针本身也有开销，包括每帧回调、DOM 变化监听和最终条件检查。比较前后版本时保持探针版本一致。工具事件场景测量的是默认轨迹折叠策略，不等同于将所有工具详情展开时的成本。

## 6. 当前验证记录与后续步骤

2026-11-04 已在允许启动浏览器的环境中使用系统 Chrome 执行功能 smoke。环境为 Windows x64、Node 24.11.1、Chrome 155.0.8059.84、1440 × 900、前端开发模式。每场景仅 **1 个预热样本 + 1 个有效样本**；首批运行启用了 trace，之后工具事件场景在关闭 trace、任务板合成 SSE 返回 204 的最终配置下也通过。结果只证明场景可执行，**不作为正式性能基线，也不证明任何性能提升**。

| 场景 | 当前功能验证 |
| --- | --- |
| 长 Markdown | 通过；真实流式回答与草稿输入均被消费 |
| 数学公式 | 通过；180 个实际 KaTeX 元素完成 |
| Mermaid | 通过；8 个 SVG 完成 |
| XLSX | 通过；真实文件解析、首屏及键盘翻页的 DOM 上限断言通过 |
| 工具事件 | 修正夹具遗漏的 `call_id` 后通过；终答及真实 UI 中严格的 `80 tool calls` 计数断言均通过。此前仅终答通过的旧工具数据无效，不纳入结果 |

五个场景已分别完成 smoke，报告保存在 `web/test-results/runtime-benchmark`、`web/test-results/runtime-benchmark-followup`、`web/test-results/runtime-benchmark-tools-final` 与 `web/test-results/runtime-benchmark-tools-clean` 四个输出目录。它们来自逐项排查后的不同运行，不是一次统一的正式性能比较；工具场景应使用最后一个目录中的报告。trace 配置以各报告及对应运行记录为准，旧报告可能没有显式 trace 字段；当前版本已将该字段加入报告，并默认关闭采集。

执行中修正了三个基准问题：开发模式 HMR 握手必须放行，否则页面无法初始化；XLSX 右下角翻页按钮可能被开发工具遮挡，因此使用正常键盘激活；工具轨迹除了关联结果的 `tool_call_id`，还需要用于 UI 分组的 `call_id`。这些都是夹具/测试问题，没有改动生产渲染组件。

正式比较时应关闭 trace、使用生产构建并增加有效样本数，保留原始报告，再决定是否优化流式更新频率、Markdown 完整块复用、工具轨迹或长会话虚拟化。若失败，可临时启用 trace 并检查页面断言；夹具缺失不能当作性能下降。

## 7. 源码入口

- [独立 Playwright 配置](../web/playwright.benchmark.config.ts)
- [工作负载与协议事件](../web/tests/performance/scenarios.ts)
- [隔离 API/WebSocket 夹具](../web/tests/performance/fixture.ts)
- [浏览器测量探针和统计](../web/tests/performance/measure.ts)
- [执行器、XLSX 生成与 JSON 报告](../web/tests/performance/runtime.bench.ts)
- [既有路由体积预算](../web/scripts/route_budgets.mjs)
