# 学习助手前端框架

纯 HTML / CSS / JavaScript，无框架、无构建依赖。打开 `index.html` 即可查看页面和切换导航；连接后台时请通过 HTTP 服务访问。

## 文件职责

- `index.html`：顶部导航、五个页面的结构及空状态。
- `css/reset.css`：颜色变量、基础重置。
- `css/layout.css`：整体布局及响应式规则。
- `css/components.css`：导航、按钮、面板、连接指示等公共组件。
- `css/pages.css`：各页面专属样式。
- `js/app.js`：hash 路由、导航激活状态、后台状态更新。
- `js/api.js`：请求封装、超时处理、响应校验和连接检查。
- `js/library.js`：资料上传、同名选择、列表、解析状态刷新、重试和删除。
- `js/assessment.js`：测评设置、出题等待、三种题型作答、计时、提交、结果动画和历史成绩。
- `js/progress.js`：进度统计、知识点雷达图与列表、薄弱项、资料详情和笔记自动保存。
- `js/reports.js`：报告日期范围、生成等待、结构化排版、Markdown 下载和历史报告。

## 页面与后台

路由为 `#learn`、`#library`、`#assessment`、`#progress`、`#reports`。无锚点或无效路由回到学习页，支持浏览器前进与后退。

默认 API 地址为同源 `/api/v1`。需要分开部署时，在 `index.html` 的 `meta[name="api-base"]` 中配置后台实际地址，后台需允许相应跨域来源。

连接检查调用需求文档已有的 `GET /api/v1/materials?page=1&page_size=1`。成功返回 `{ "data": { "items": [], "total": 0 } }` 形式的数据才亮绿灯；网络异常、超时、HTTP 错误或响应不符合约定时亮红灯。初次检测完成前为红点及“连接检测中”。页面可见时每 15 秒检测一次；恢复可见或网络连接时立即检测。不新增后台接口。

资料库、测评、进度和报告已实现前端交互，通过真实 API 管理资料、测评、进度、笔记和报告，不使用模拟数据或浏览器本地缓存冒充后台。学习页面仍为框架。

## 资料库接口约定

支持 `.pdf`、`.ppt`、`.pptx`、`.doc`、`.docx`、`.md`、`.markdown`、`.txt`，支持点击及拖拽、多文件顺序上传。上传前检查同名；取消不上传，覆盖附带版本条件，保留两份由后台命名。保存时出现同名或版本冲突会重新询问，不自动覆盖。

- `GET /api/v1/materials?page=1&page_size=100`：按页加载全部资料，响应 `data.items`、`data.total`。每项含 `id, filename, file_type, file_size`（字节）、`status`（processing / ready / failed）、`error_message`。覆盖期间可包含 `pending_version: {status, file_size, error_message}` 和 `current_version_id`，显示新版本状态并提示旧版本可用。
- `POST /api/v1/materials/check-name`：JSON `{filename}`，返回 `data: {duplicate, existing_material: {id, current_version_id}}`。
- `POST /api/v1/materials`：multipart 文件及同名处理参数，返回 `data: {material_id, filename, status}`。覆盖时原资料没有可用版本则 `expected_version_id` 传空字符串，后台需按“无当前可用版本”校验。
- `POST /api/v1/materials/{id}/retry`：重新解析最近失败的版本，返回 `data: {material_id, status: "processing"}`。不可重试时返回 409。保留仍可用的旧版本。
- `DELETE /api/v1/materials/{id}`：成功返回 204 或 `{data: {deleted: true}}`。后台从当前资料库移除资料，但保留既有测评记录及其来源快照。

解析中每 4 秒刷新资料列表，其他情况下每 15 秒刷新，离开页面或页面不可见时暂停。上传请求最长等待 120 秒；超时或断网不自动重复上传，提示用户刷新核对。失败的操作不会显示成功。后台未实现时展示连接或加载失败提示；文件保存、解析、重试及删除效果仍需后台按以上契约实现。

## 测评页面

选择可用资料和 5／8／10／15 题；选择、判断、简答题分别分配为 2/2/1、3/3/2、4/3/3、5/5/5。计时从题目出现开始，到提交停止。三种题型均使用原生表单控件，选项支持键盘操作，加载和圆环动画兼容减少动态效果设置。

请求集中在 `api.assessments`：创建 `POST /assessments`、详情 `GET /assessments/{id}`、提交 `POST /assessments/{id}/submissions`、结果 `GET /assessments/{id}/result`、重新评分 `POST /assessments/{id}/retry-grading`、历史 `GET /assessments?status=graded&page=1&page_size=10`（均带 `/api/v1` 前缀）。完整字段见项目功能需求文档 V1.2 的测评接口补充。

创建与提交均使用固定 `request_id` 支持失败重试，后台必须实施幂等约束。提交增加 `duration_seconds`，题目增加 `difficulty`；结果返回 `questions` 数组中的逐题答案、评分和点评。生成与评分每 1.5 秒读取状态，连续等待约 2 分钟或读取失败后提供手动继续获取，不自动重新建卷。明确评分失败时可重新评分原提交。前端校验试卷数量、类型、选择项及结果分数一致性。

历史成绩由后台保存，刷新页面后可重新查询；当前未提交答案仅保存在页面内存中，切换导航保留，关闭或刷新页面时浏览器提示避免意外丢失。放弃仅清空本次未提交答案，不删除后台历史。未接入后台时不能实际出题和评分，也不会显示模拟成绩。

## 进度页面

顶部五项统计、知识点雷达图、完整知识点列表、薄弱知识点和逐资料详情均读取后台计算结果。`unassessed / needs_review / partial / mastered` 分别显示未测评、待巩固、部分掌握、已掌握；未测评不显示为 0% 成绩。掌握资料要求当前版本全部知识点均已测评且全部掌握，详细口径见功能需求文档 V1.3。

使用 `GET /api/v1/progress/summary`、`GET /api/v1/progress/knowledge-points?page=...`、`GET /api/v1/progress/materials?page=...` 和 `PUT /api/v1/materials/{id}/note`。资料笔记停止输入 900 毫秒后自动保存，同一资料的保存请求串行处理；失败后保留编辑内容并自动重试，切换页面时立即尝试保存。长笔记的滑块只改变编辑区显示高度，不改变内容。接口字段与数据库补充见功能需求文档。

## 报告页面

支持按本周或自定义起止日期生成报告。本周按浏览器本地时区的周一至周日计算；自定义范围包含起止两天。生成期间锁定日期和按钮，创建或轮询中断后沿用原 `request_id`／`report_id` 继续，避免重复报告。

使用 `POST /api/v1/weekly-reports`、`GET /api/v1/weekly-reports/{id}` 和 `GET /api/v1/weekly-reports?status=ready&page=...`。报告详情为结构化纯文本，页面显示统计、总结、学到的内容、薄弱点和建议。下载按钮在浏览器本地将当前报告重新排版成 UTF-8 Markdown，并对 HTML 和 Markdown 控制字符转义；不需要单独下载接口。历史报告按页查询并从详情接口打开。完整字段、状态和统计口径见功能需求文档 V1.4。
