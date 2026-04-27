# TOP5 Hot AI Project

[English](#english) | [中文](#中文)

## English

TOP5 Hot AI Project tracks and summarizes the weekly TOP10 trending open-source AI projects on GitHub. It includes GitHub data collection, objective scoring, a bilingual web UI, JSON APIs, static-site export, and GitHub Pages deployment.

Live site:

- English: `https://seanliuxq.github.io/TOP5_hot_AI/`
- 中文: `https://seanliuxq.github.io/TOP5_hot_AI/zh/`

### Features

- Weekly TOP10 ranking for open-source AI repositories.
- Real GitHub API collection with built-in mock data fallback.
- Repository snapshots, weekly rankings, and collection run logs.
- Pages for latest ranking, history, repository details, comparison, methodology, and runs.
- English and Chinese UI, with English as the default.
- `/api/v1` JSON API.
- Markdown, JSON, and CSV weekly exports.
- Static build for GitHub Pages.

### Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
python scripts/collect_weekly.py --mock
uvicorn app.main:app --reload
```

Open:

- Web UI: `http://127.0.0.1:8000`
- Chinese UI: `http://127.0.0.1:8000/zh/`
- API docs: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/healthz`

### Use Real GitHub Data

Set these values in `.env`:

```env
GITHUB_TOKEN=your_github_token
USE_MOCK_WHEN_NO_TOKEN=false
```

Then run:

```bash
python scripts/collect_weekly.py --limit 120
```

The token only needs access to public repository data. The first real collection has limited trend baseline; after two or more weekly runs, growth metrics become more representative.

### Static Build And GitHub Pages

Build the static site locally:

```bash
python scripts/build_static_site.py --collect --mock --output site --archive-dir public-data
python -m http.server 8080 -d site
```

The repository includes a GitHub Actions workflow for GitHub Pages. It can be run manually and also refreshes the site weekly.

### API Examples

```bash
curl http://127.0.0.1:8000/api/v1/rankings/latest
curl http://127.0.0.1:8000/api/v1/rankings/weeks
curl http://127.0.0.1:8000/api/v1/repos/vllm-project/vllm
```

### Scoring

`hot_score` is a 100-point score:

```text
0.35 attention_score
+ 0.25 activity_score
+ 0.15 community_score
+ 0.10 freshness_score
+ 0.10 health_score
+ 0.05 maturity_confidence
```

### Project Structure

```text
app/
  api/        JSON API
  core/       configuration, dates, and JSON helpers
  db/         SQLAlchemy models and sessions
  github/     GitHub client, discovery, filtering, and collection
  ranking/    scoring, ranking, and report generation
  web/        Jinja2 pages and static assets
scripts/      collection, backfill, and static build scripts
tests/        unit tests
```

## 中文

TOP5 Hot AI Project 每周追踪并汇总 GitHub 热门 AI 开源项目 TOP10。项目包含 GitHub 数据采集、客观评分模型、双语 Web 页面、JSON API、静态站点导出和 GitHub Pages 部署。

在线地址：

- English: `https://seanliuxq.github.io/TOP5_hot_AI/`
- 中文: `https://seanliuxq.github.io/TOP5_hot_AI/zh/`

### 功能

- 每周生成 AI 开源项目 TOP10。
- 支持真实 GitHub API 采集，也支持内置示例数据。
- 保存仓库快照、周榜和采集运行日志。
- 提供最新榜单、历史周榜、项目详情、项目对比、评分方法和采集运行页面。
- 支持英文和中文界面，默认英文。
- 提供 `/api/v1` JSON API。
- 导出 Markdown、JSON、CSV 周报。
- 支持导出静态站点并部署到 GitHub Pages。

### 本地启动

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
python scripts/collect_weekly.py --mock
uvicorn app.main:app --reload
```

打开：

- Web UI: `http://127.0.0.1:8000`
- 中文界面: `http://127.0.0.1:8000/zh/`
- API docs: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/healthz`

### 使用真实 GitHub 数据

在 `.env` 中设置：

```env
GITHUB_TOKEN=your_github_token
USE_MOCK_WHEN_NO_TOKEN=false
```

然后运行：

```bash
python scripts/collect_weekly.py --limit 120
```

GitHub token 只需要读取公开仓库数据的权限。首次真实采集没有完整趋势基线，连续运行两周或更久后，增长指标会更稳定。

### 静态构建和 GitHub Pages

本地生成静态站点：

```bash
python scripts/build_static_site.py --collect --mock --output site --archive-dir public-data
python -m http.server 8080 -d site
```

仓库已包含 GitHub Pages 的 GitHub Actions 工作流，可以手动运行，也会每周自动刷新。

### API 示例

```bash
curl http://127.0.0.1:8000/api/v1/rankings/latest
curl http://127.0.0.1:8000/api/v1/rankings/weeks
curl http://127.0.0.1:8000/api/v1/repos/vllm-project/vllm
```

### 评分口径

`hot_score` 满分 100：

```text
0.35 attention_score
+ 0.25 activity_score
+ 0.15 community_score
+ 0.10 freshness_score
+ 0.10 health_score
+ 0.05 maturity_confidence
```

### 项目结构

```text
app/
  api/        JSON API
  core/       配置、日期和 JSON 工具
  db/         SQLAlchemy 模型和 session
  github/     GitHub client、候选发现、过滤和采集
  ranking/    评分、榜单和报告
  web/        Jinja2 页面和静态资源
scripts/      采集、回填和静态构建脚本
tests/        单元测试
```
