# DeepTutor 架构图

DeepTutor 采用前后端分离的模块化单体架构，以 Agent 工作流为核心。
Web、CLI 和 Python SDK 的对话请求共用应用服务、执行引擎及事件协议；
知识库、文件、设置等管理操作通过独立的 REST 接口调用业务服务。
下图对应源码版本 **1.6.15**。

![DeepTutor 对话执行架构](../assets/figs/system/architecture.svg)

[直接打开 SVG 总览图](../assets/figs/system/architecture.svg)。以下 Mermaid 图可在支持 Mermaid
的 Markdown 查看器中渲染，也可直接编辑图中的节点和连线。

总体结构包含入口、应用服务、执行机制、能力与工具，以及底层业务服务。
图中的连线表示主要调用关系，虚线表示事件流。

```mermaid
flowchart TB
    subgraph Entry["入口与适配器"]
        Web["Web<br/>Next.js 16 / React 19 / TypeScript"]
        CLI["CLI<br/>Typer / Rich / prompt_toolkit"]
        SDK["Python SDK"]
        WS["FastAPI<br/>WebSocket /ws"]
        REST["FastAPI<br/>REST 管理接口"]
        Facade["DeepTutorApp<br/>deeptutor/app/facade.py"]
        Web --> WS
        Web --> REST
        CLI --> Facade
        SDK --> Facade
    end

    subgraph Execution["共享对话运行时"]
        App["TurnApplicationService<br/>启动、取消、重试、订阅"]
        Runtime["TurnRuntimeManager<br/>组装 UnifiedContext、管理执行与事件"]
        Engine["TurnEngine → ChatOrchestrator<br/>选择当前 Capability"]
        Catalog["CapabilityCatalog / Registry<br/>工厂注册、按回合创建实例"]
        Capability["Capability<br/>AgentLoop 或多阶段工作流"]
        Bus["StreamBus<br/>内容、工具、进度、结果事件"]
        App --> Runtime
        Runtime --> Engine
        Engine --> Catalog
        Engine --> Capability
        Capability -.-> Bus
        Bus -.-> Runtime
    end

    subgraph Services["工具与业务服务"]
        Tools["ToolRegistry<br/>内置工具 / MCP / CLI 扩展"]
        LLM["LLM Provider 适配层<br/>模型调用、流式输出、用量统计"]
        RAG["知识库与 RAG<br/>LlamaIndex / 可选检索引擎"]
        Parsing["ParseService<br/>文档解析、统一结构、内容缓存"]
        Learning["学习业务<br/>阅读、课程、书籍、笔记、记忆"]
        Sandbox["执行后端<br/>Runner 容器 / bwrap / 受限子进程"]
        Tools --> RAG
        Tools --> Learning
        Tools --> Sandbox
        RAG --> Parsing
    end

    subgraph State["持久化与协调"]
        Store["SessionStore<br/>默认 SQLite / 可选 PocketBase"]
        Coordinator["RuntimeCoordinator<br/>默认内存 / 多 worker 使用 Redis"]
        Files["工作区文件系统<br/>文档、附件、索引、产物"]
    end

    WS --> App
    Facade --> App
    REST --> RAG
    REST --> Learning
    Runtime <--> Store
    Runtime <--> Coordinator
    Store -. "历史事件回放" .-> App
    Coordinator -. "实时事件与续传" .-> App
    Capability --> Tools
    Capability --> LLM
    RAG --> Files
    Parsing --> Files
    Learning --> Files

    classDef entry fill:#eff6ff,stroke:#60a5fa,color:#172554
    classDef execution fill:#eef2ff,stroke:#818cf8,color:#312e81
    classDef service fill:#f0fdf4,stroke:#4ade80,color:#14532d
    classDef state fill:#f8fafc,stroke:#94a3b8,color:#0f172a
    class Web,CLI,SDK,WS,REST,Facade entry
    class App,Runtime,Engine,Catalog,Capability,Bus execution
    class Tools,LLM,RAG,Parsing,Learning,Sandbox service
    class Store,Coordinator,Files state
```

Tool 完成单次操作，Capability 负责整个回合或工作流。当前注册了 11 个内置回合
能力，包括 `chat`、`deep_solve`、`deep_research`、`mastery_path`、
`immersive_reading` 和 `audio_overview`；用户可切换工具共 8 个，包含
`zotero_search`。RAG、记忆、执行等其他工具按上下文及权限挂载。
`CapabilityCatalog` 同时管理 `turn` 和 `loop_extension`，后者为循环增加
提示词、专属工具与生命周期钩子。

普通聊天使用一个持续增长的消息上下文：模型调用工具后，工具结果回填到下一轮；
通常在模型不再调用工具时结束。`ask_user` 可以暂停等待回答，循环预算约束执行规模。
下面的事件链路支撑实时展示和断线回放。

```mermaid
flowchart LR
    Capability["Capability / AgentLoop"] --> Bus["StreamBus"]
    Bus --> Runtime["TurnRuntime<br/>绑定 session_id / turn_id"]
    Runtime --> Coordinator["协调器事件日志<br/>分配 seq、发布实时事件"]
    Runtime -->|"周期、批量及终态刷写"| Store["SessionStore<br/>持久化 turn_events"]
    Coordinator -->|"读取实时事件"| App["TurnApplicationService<br/>subscribe_turn"]
    Store -->|"按 after_seq 回放"| App
    App --> Adapter["WebSocket / CLI / SDK"]
    Adapter --> Consumer["展示内容、工具执行和进度"]
    Consumer -->|"重连并携带事件游标"| Adapter
    Adapter -->|"订阅或续传"| App
```

事件采用批量及周期持久化，终态触发刷写；实时传输与落盘由不同机制承接。
Web 客户端通过序号去重、补齐缺口和续传。
多 worker 配置要求 Redis 协调，租约和写入凭证约束执行归属。
执行 worker 丢失后，恢复服务会将回合标记为可重试失败；事件回放不代表从任意
模型调用或工具步骤自动继续执行。

默认知识库采用 LlamaIndex，本地解析、索引和检索分别承担独立职责。
下面展示该默认引擎的文档接入与查询路径。

```mermaid
flowchart TB
    Documents["文档来源<br/>PDF / Office / 文本等"] --> Parse["ParseService<br/>选择解析引擎与内容缓存"]
    Parse --> IR["ParsedDocument<br/>统一文档结构"]
    IR --> Indexing["LlamaIndex 索引流水线<br/>分块与 Embedding"]
    Indexing --> Vector["向量索引<br/>优先 FAISS"]
    Indexing --> Nodes["文档节点与元数据"]
    Nodes --> BM25["BM25 关键词检索"]

    Query["Agent 的知识库查询"] --> RAG["RAGService<br/>解析知识库绑定的引擎"]
    RAG --> Retrieval["LlamaIndex 检索"]
    Retrieval --> Vector
    Retrieval --> BM25
    Vector --> Fusion["结果融合<br/>可选重排序"]
    BM25 --> Fusion
    Fusion --> Sources["上下文片段与来源引用"]
    Sources --> Agent["Agent 组织回答"]
```

FAISS 或 BM25 的相应集成不可用时，检索实现提供降级路径。GraphRAG、LightRAG
及外部知识库连接是其他可选引擎；PageIndex 使用 Agent 工具进行推理式检索，
不沿用上图的普通向量检索路径。

技术栈与部署组件如下；版本为项目依赖声明，具体可选功能取决于安装及配置。

| 层面 | 技术与职责 |
| --- | --- |
| 后端 | Python 3.11–3.14、asyncio；FastAPI、Uvicorn、Pydantic 2 |
| Web | Next.js 16.2、React 19、TypeScript 5、Tailwind CSS 3.4 |
| 前端状态 | React Context + useReducer；独立的 WebSocket 传输与续传客户端 |
| 接口契约 | 后端导出 OpenAPI / JSON Schema，生成前端 TypeScript 类型 |
| 模型调用 | OpenAI / Anthropic 等适配器，HTTPX、aiohttp，统一流式与用量处理 |
| RAG | 默认 LlamaIndex、FAISS、BM25；可选图检索、推理式检索和外部知识库 |
| 文档解析 | 默认文本解析；可选 MinerU、Docling、MarkItDown、PyMuPDF4LLM 等 |
| 内容展示 | Markdown、KaTeX、Mermaid、Chart.js、PDF.js、EPUB.js；可选 Manim 动画 |
| 存储 | 默认 SQLite 与工作区文件；可选 PocketBase 会话存储 |
| 协调 | 默认进程内存；多后端 worker 要求 Redis |
| 执行隔离 | 独立 runner 容器或 Linux bwrap；受限子进程仅为管理员显式启用的降级模式 |
| 部署 | Python 包、CLI 包、Next.js standalone、Docker / Compose |
| 质量检查 | pytest、Vitest、Playwright、Ruff、ESLint、import-linter、dependency-cruiser |

运行设置集中在 `data/user/settings/*.json`，进程环境变量可覆盖设置；项目根目录
`.env` 不会被运行时自动读取。用户与工作区上下文决定数据访问范围。
完整 Web 应用需要 Node.js，CI 与 Docker 使用 Node.js 22。

维护这些图时，可从下列源码入口核对职责和组件变化。

| 架构部分 | 源码入口 |
| --- | --- |
| 应用服务与依赖装配 | [service.py](../deeptutor/app/service.py)、[container.py](../deeptutor/app/container.py) |
| SDK 与 CLI 门面 | [facade.py](../deeptutor/app/facade.py) |
| 执行引擎与编排 | [turn_engine.py](../deeptutor/runtime/turn_engine.py)、[orchestrator.py](../deeptutor/runtime/orchestrator.py) |
| 核心上下文与事件协议 | [context.py](../deeptutor/core/context.py)、[stream.py](../deeptutor/core/stream.py) |
| 能力目录与内置能力 | [capability_catalog.py](../deeptutor/runtime/capability_catalog.py)、[builtin_capabilities.py](../deeptutor/runtime/bootstrap/builtin_capabilities.py) |
| Agent 循环与工具挂载 | [agent_loop.py](../deeptutor/agents/loop/agent_loop.py)、[tool_composition.py](../deeptutor/agents/_shared/tool_composition.py) |
| 会话存储与事件生命周期 | [session/__init__.py](../deeptutor/services/session/__init__.py)、[lifecycle.py](../deeptutor/services/session/turns/lifecycle.py) |
| 协调与故障恢复 | [settings.py](../deeptutor/runtime/coordination/settings.py)、[recovery.py](../deeptutor/runtime/coordination/recovery.py) |
| RAG 与解析 | [rag/factory.py](../deeptutor/services/rag/factory.py)、[parsing/service.py](../deeptutor/services/parsing/service.py) |
| 前端事件消费 | [TurnRuntimeClient.ts](../web/features/chat/transport/TurnRuntimeClient.ts)、[ChatStateAdapter.tsx](../web/features/chat/ChatStateAdapter.tsx) |
| 前端契约维护 | [contracts/README.md](../web/contracts/README.md) |
| 依赖与部署 | [pyproject.toml](../pyproject.toml)、[package.json](../web/package.json)、[Dockerfile](../Dockerfile)、[docker-compose.yml](../docker-compose.yml) |
