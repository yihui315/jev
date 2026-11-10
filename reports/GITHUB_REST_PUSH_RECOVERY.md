# GITHUB_REST_PUSH_RECOVERY.md — T01 推送恢复（诚实结论：BLOCKED）

> Generated: 2026-11-10T21:26 (GMT+8)
> Status: **BLOCKED — 无法在本沙箱由 Agent 完成**
> 仓库: yihui315/jev ｜ 分支: feature/v16-market-provenance-hardening ｜ PR #6: Draft

## 1. 目标（重述）
在远端保留本地 7 个提交（`b102ca7`..`8da71f8`）的内容与可审计历史，使用 GitHub REST Git Data API 重建提交链（force=false），记录原始 SHA → 重建 SHA 映射，不得伪造原 SHA 推送。

## 2. 已验证的环境事实（逐条实证）
| 检查项 | 结果 | 证据 |
|--------|------|------|
| 本地领先 | 7 commits ahead（`b102ca7`→`8da71f8`），工作树 clean | `git rev-list --reverse b102ca7..8da71f8`；`git status` clean |
| 远端基线 ref | 仍为 `b102ca7` | `git ls-remote origin refs/heads/feature/v16-market-provenance-hardening` |
| GitHub MCP connector | **connected / bound / enabled** | `list_installed_plugins`: github `status=connected` |
| Connector 写权限 | **无（只读）** | `create_branch` → `403 Resource not accessible by integration` |
| 可直接使用的 GitHub PAT | **无** | env 无 token；`cmdkey` 无 github 条目；GCM 无缓存凭据 |
| `api.github.com` 可达性 | HTTP 200（经代理 10808） | curl 实测；但**无 token 无法鉴权写** |
| `git push` | **receive-pack POST 经代理 stall**（EXIT=124，3 次） | `timeout 200 git push ...` → 仅打印 remote URL 后超时 |
| 底层 Git Data API（blob/tree/commit/ref） | **MCP 未暴露** | ToolSearch：仅 `create_or_update_file`/`push_files`/`get_file_contents`/`create_branch`/`get_commit` |
| 大文件内联注入可行性 | **不可靠** | Read 工具对 >2000 字符行截断 → 无法可靠提取 184KB 内联内容 |

## 3. T01 步骤执行对照
| 步骤 | 设计 | 实际 |
|------|------|------|
| 1. GET 分支 ref，确认远端 SHA | b102ca7 | ✅ 已确认仍为 `b102ca7` |
| 2. 验证 Contents:write | 需 write scope | ❌ Connector 403（只读集成令牌） |
| 3–6. blob/tree/commit 重建 | 需底层 Git Data API + token | ❌ MCP 无底层 API；无可用 token |
| 7–9. 逐提交核对 / 重比 ref / PATCH ref | 需 write + ref 更新 | ❌ 同上 |
| 10. 记录 SHA 映射 | 若 SHA 不能一致则记录 | ⛔ 前置受阻，无法执行 |

## 4. 结论
**T01 无法在本沙箱由 Agent 完成。** 7 个提交仅存于本地；远端 `feature/v16-market-provenance-hardening` 仍停留 `b102ca7`。

## 5. 解除阻塞的精确路径（需主理人动作）
- **方案 A（推荐）**：授予 GitHub connector **Contents:write**（重新授权，scope 含 `repo`/contents），即可用底层 Git Data API 重建提交链，且**可保留原始 SHA**（tree/parent/author/timestamp 完全复刻 → SHA 确定性一致）。
- **方案 B**：提供具备 `Contents:write` 的 GitHub Token（PAT），由 Agent 走直接 REST Git Data API。
- **方案 C**：在正常 GitHub egress 环境执行 `git push`（绕过代理 receive-pack stall）。

## 6. 纪律
- 未 force push、未碰 main / Release / Staging D1、无真实资金、PR #6 维持 Draft。
- 未将 token 写入脚本/提交/日志/输出（本环境亦无可用 token）。
- 若最终走方案 A/B 且底层 API 可用，将严格按 T01 步骤 3–10 重建并**记录原始 SHA → 重建 SHA 映射**（若 SHA 因工具限制无法一致，按约定如实声明，不伪称原 SHA 推送）。
