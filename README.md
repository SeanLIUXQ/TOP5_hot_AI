# TOP5 Hot AI Project

[English](#english) | [中文](#中文)

## English

TOP5 Hot AI Project tracks and summarizes the weekly TOP10 trending open-source AI projects on GitHub. Each day it publishes a dated TOP10 snapshot and a dedicated AI Infra digest. It includes GitHub data collection, objective scoring, a bilingual web UI, JSON APIs, static-site export, and GitHub Pages deployment.

Live site:

- English: `https://seanliuxq.github.io/TOP5_hot_AI/`
- 中文: `https://seanliuxq.github.io/TOP5_hot_AI/zh/`

### Features

- Daily editions of the weekly TOP10 and [AI Infra](https://seanliuxq.github.io/TOP5_hot_AI/infra/).
- AI Infra searches GitHub for inference infrastructure created in the past 30 days or pushed in the past 14 days. It prioritizes edge runtimes, SSD/MoE offload, quantization and accelerators, and includes inference serving. Strata, inferssd and colibri are watched by stable GitHub ID. All qualifying results from the bounded searches appear in the digest. The release shortlist includes the watchlist and checks up to 15 repositories.
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
python scripts/build_static_site.py --output site
python -m http.server 8080 -d site
```

GitHub Actions collects and deploys at **01:17 UTC daily** and after changes to site code reach `main`. Queues can delay the start. The manual `mock` option creates a downloadable preview. For local development, `python scripts/build_static_site.py --mock` uses a disposable database and archive. Set `GITHUB_TOKEN` and use `--collect` to publish a live local edition.

Each new runner restores weekly checkpoints in `collection-state/` and legacy weekly exports in `public-data/` before scoring. Checkpoints retain candidate snapshots, ranks, run history and daily counters for seven-day growth. Dated TOP10s and Infra digests keep their observed metadata. A rerun refreshes only that day's edition and the rolling weekly export. Legacy weeks use their Monday labels and retain their original generation timestamps. The original TOP10 exports contain only published ranks and available metrics.


### API Examples

```bash
curl http://127.0.0.1:8000/api/v1/infra/latest
curl http://127.0.0.1:8000/api/v1/editions
curl http://127.0.0.1:8000/api/v1/rankings/latest
curl http://127.0.0.1:8000/api/v1/rankings/weeks
curl http://127.0.0.1:8000/api/v1/repos/vllm-project/vllm
```

| Data | FastAPI | GitHub Pages (relative to the site root) |
| --- | --- | --- |
| Latest TOP10 | `/api/v1/rankings/latest` | `data/rankings-latest.json` |
| Available ranking editions | `/api/v1/editions` | `data/editions.json` |
| Exact dated TOP10 | `/api/v1/editions/YYYY-MM-DD` | `data/editions/YYYY-MM-DD.json` |
| Infra dates | `/api/v1/infra` | `data/infra-dates.json` |
| Latest or dated Infra | `/api/v1/infra/latest` or `/api/v1/infra/YYYY-MM-DD` | `data/infra/latest.json` or `data/infra/YYYY-MM-DD.json` |
| Weekly dates | `/api/v1/rankings/weeks` | `data/ranking-weeks.json` |

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
  infra/      daily inference discovery and release coverage
  github/     GitHub client, discovery, filtering, and collection
  ranking/    scoring, ranking, and report generation
  web/        Jinja2 pages and static assets
scripts/      collection, backfill, and static build scripts
tests/        unit tests
```

## 中文

TOP5 Hot AI Project 每周追踪并汇总 GitHub 热门 AI 开源项目 TOP10。每天发布当日 TOP10 快照和独立的 AI Infra 推理简报。项目包含 GitHub 数据采集、客观评分模型、双语 Web 页面、JSON API、静态站点导出和 GitHub Pages 部署。

在线地址：

- English: `https://seanliuxq.github.io/TOP5_hot_AI/`
- 中文: `https://seanliuxq.github.io/TOP5_hot_AI/zh/`

### 功能

- 每天发布每周 TOP10 的当日快照和 [AI Infra 端侧推理简报](https://seanliuxq.github.io/TOP5_hot_AI/zh/infra/)。
- AI Infra 搜索 GitHub 近 30 天创建或近 14 天有推送的推理项目，覆盖端侧运行时、SSD / MoE 卸载、量化、硬件加速和推理服务。通过 GitHub 仓库 ID 长期关注 Strata、inferssd 和 colibri。将命中的合格项目全部收录，并为含关注名单在内的最多 15 个项目查询 Release。
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
python scripts/build_static_site.py --output site
python -m http.server 8080 -d site
```

GitHub Actions 每天 **北京时间 09:17**（UTC 01:17）触发采集和部署，实际开始时间取决于 GitHub 队列。站点代码推送到 `main` 也会触发更新。手动选择 `mock` 可下载预览附件。本地运行 `python scripts/build_static_site.py --mock` 使用临时数据库和归档。配置 `GITHUB_TOKEN` 后加 `--collect` 会采集真实数据。

每个新 runner 会先从 `collection-state/` 的周检查点和 `public-data/` 的历史导出恢复 SQLite，再计算排名。检查点保存候选仓库快照、排名、采集记录和计算七天增长的每日计数。榜单与 AI Infra 按 UTC 采集日分别留档，同日重跑刷新该期和该周汇总。旧周榜沿用周一起始日及原始生成时间，可恢复当时已导出的 TOP10 和指标。


### API 示例

```bash
curl http://127.0.0.1:8000/api/v1/infra/latest
curl http://127.0.0.1:8000/api/v1/editions
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
  infra/      每日推理项目发现与版本跟踪
  github/     GitHub client、候选发现、过滤和采集
  ranking/    评分、榜单和报告
  web/        Jinja2 页面和静态资源
scripts/      采集、回填和静态构建脚本
tests/        单元测试
```
