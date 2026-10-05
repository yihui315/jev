# P3 / P4 规格（CR-H5 / CR-H6）— 落盘版

> **本文件是规格的权威副本。** 编号→内容映射、实现位置、判据都在这里。
> 执行者**不应依赖任何对话记录**。若本文件与对话冲突，以本文件为准。
>
> 落盘原因：本轮已发生一次规格只存在于对话、执行者拒绝猜测的返工，
> 以及一次编号别名导致的歧义。规格必须落在可复用的位置。
>
> 状态：**等待 risk1-scope commit `server.py`**（见 §5开工条件）

---

## 1. 编号映射（唯一权威）

| 本文件编号 | 旧别名 | 含义 | 文件 |
|---|---|---|---|
| **P3** | CR-H5 | `/api/health` 未暴露令牌配置状态 | `packages/jcc_core/server.py` |
| **P4** | CR-H6 | 非 ASCII 令牌抛未捕获 `TypeError` | `packages/jcc_core/server.py` **两处** |

**别名说明**：`CR-H5` 与 `control-token` 是同一件事；
`CR-H6` 与 `p2` 文档中「路径留存」**不是**同一件事——不要混用。

---

## 2. P3 · `/api/health` 暴露 `control_token_configured`

### 2.1 缺陷
`/api/health` 的返回体**没有**该字段。实测现有字段：
```
status  jev_provider  provider_mode  provider_verified  provider_activation_state
provider_requested_mode  provider_is_real  actuation_enabled  policy_version
localhost_only  wal  open_get_endpoints
```

### 2.2 为什么这是缺陷而非 feature request
本机 `.env` 三个键（`JCC_CONTROL_TOKEN` / `JEV_API_KEY` / `JCC_DIRECTIVE_SIGNING_KEY`）
**全为空值**，宿主进程**零 `JCC_*` 导出**。
安全成员实测：**27 条 mutating 路由在无token 时全部 401/403**。

**控制面是锁死的（fail-closed，正确行为）。残余风险纯在运维面**：
操作者可能以为令牌已配好而实际没有，因为**没有任何地方告诉他**。

### 2.3 实现位置与方式
- 文件：`packages/jcc_core/server.py` 的 `def health()`（约 327 行起）
- 计算式：**`bool(os.environ.get(config.control_token_env))`**

> ⚠️ **属性名是 `config.control_token_env`，不是 `config.control.token_env`。**
> `Config` 是**扁平 dataclass**，`config/jcc-v1.3.yaml` 里的
> `control: token_env: ...` 在加载时被摊平成 `control_token_env` 属性。
> 已实测：
> ```
> hasattr(Config, 'control')           = False
> hasattr(Config, 'control_token_env')  = True
> Config().control.token_env-> AttributeError
> ```
> 写成 `config.control.token_env` 会在**开放 GET 的 health 上抛 500**——
> **一个想暴露「服务健康」的字段，会让 health 自己挂掉。**

`Config` 现有字段（读 dataclass 定义确认，勿凭记忆）：
```
server, control_token_env, jev, state_store_db_path, actuation, policy,
launcher, agents, raw, config_path, config_is_explicit
```

### 2.4 验收
- `.env` 为空 → 该字段为 `false`
- 设置非空 token → `true`（用 `subprocess` 起子进程注入环境变量验证，
  **不要在当前进程里改 `os.environ`**）
- **health 响应体任何位置都不得出现 token 值** —��� 加断言

### 2.5 commit message 必须写入的安全性推理
> health 本身是**开放 GET**（既有设计，安全成员已实测 27 条 mutating 路由的隔离方式）。
> 暴露「是否配置」不新增攻击面：攻击者从 `false` 无法推出「再试别的 env 名」，
> 因为他必须先知道 `token_env` 叫什么才能问这个问题，
> **而那个名字在配置文件里本来就是公开的**。
> 换言之该信息对任何能访问 health 的人**本来就是可推导的**。

---

## 3. P4 · 非 ASCII 令牌不得抛未捕获异常

### 3.1 缺陷
`hmac.compare_digest(a, b)` 对**含非 ASCII 码点的 `str` 抛 `TypeError`**。
实测：
```
compare_digest('中文', '中文')            -> TypeError
compare_digest('中文', 'ascii-token')     -> TypeError
compare_digest('ascii-token','ascii-token') -> True     ← ASCII 对照正常
```

### 3.2 性质（必须写进 commit message）
- **机密性与完整性零影响** —— 请求**仍被拒绝**
- 这是**可用性/健壮性**缺陷：不是 403，而是未捕获异常（可能返回 500）

### 3.3 实现位置：**两处，同一 commit**

| 行 | 函数 | 场景 | 崩在|
|---|---|---|---|
| `346` | `require_control_token()` | HTTP mutating 路由 | 返回 500 |
| `1493` | `ws_endpoint()` | WebSocket `/ws`（token 取自 subprotocol） | **握手中断**，非 HTTP 500 |

**只修 346 的话 `/ws` 仍会崩**，且崩在 `ws.close(code=4401)` **之前** ——
socket 既没被 accept 也没被干净关闭。

**修一处留一处 = 不一致的加固，比全不改更难推理。** 故两处同一 commit 修。

### 3.4 修法：两侧 `.encode()` 成 bytes 后再比较
```python
# :346
if not presented or not hmac.compare_digest(presented.encode(), expected.encode()):

# :1493  —— 必须保留 not expected / not token 的短路顺序
if not expected or not token or not hmac.compare_digest(token.encode(), expected.encode()):
```

**只改比较处**。`expected`/`presented`/`token` 的空值判断与真值逻辑**一字不动**。

选 (a) `.encode()` 而非 (b) `try/except TypeError` 的理由：
它**消除异常路径**而非捕获它，且 `compare_digest` 在 bytes 上恒定时间。

### 3.5 验收（三条都要有测试）
- 非 ASCII token → **403**（不是 500、不是异常、不是断连）
- 正确 ASCII token → 仍然通过
- 错误 ASCII token → 仍然 403

**「拒绝」这个结果在任何输入下都不变** —— 不能因类型异常放行。

---

## 4. 硬性要求

1. **zero new dependencies**：仅 stdlib
2. **禁止硬编码盘符**（本项目已因盘符出 4 次事故）
3. **禁止 `importlib` / `exec_module` 加载本仓库脚本做验证** ——
   本仓库大量脚本缺 `if __name__ == "__main__"`，import 等于执行。
   已有成员这么干，**改写 16 个 tracked 文件并拉起 2 个进程**。要验证就**运行**它。
4. **禁止 spawn 真实 agent / 真实服务进程**
5. **禁止 `git add -A` / `git add .` / `git checkout` / `git restore` / `git stash`**
   —— 只 `git add -- <具体文件>`。提交前用 `git diff --cached --name-only` 核对。
   **本仓已发生 3 次互相覆盖未提交改动的事故。**
6. **不要改 A8 pin 值**（`packages/jcc_core/tests/test_p3_acceptance_gate.py` 的
   `_BASELINE_HASHES`）—— 那是主理人的重基线职责。
   **但你改了 `server.py`（在 pin 内）后，必须在回传里明确告知**，主理人会重基线。
7. 不要 push / force / tag

---

## 5. 开工条件

| # | 条件 | 如何确认 |
|---|---|---|
| 1 | 本规格已具备 | ✅ |
| 2 | `packages/jcc_core/server.py` 工作区干净 | `git status --short packages/jcc_core/server.py` **输出为空** |

**条件 2 不满足时不开工。** 即使只差几分钟、即使改动很小。

**理由**：risk1-scope 正在编辑 `server.py`（其改动面已从 3 个文件扩大到 6 个，
方向是「还在写」不是「快收尾」）。并行修改会导致 diff 交错，
且**他们的 `git add` 很可能连带把你的改动一起提交**——
这与上一轮 `1d7d55a` 混入他人 `run.py` 是同一种事故，只是方向相反。

**不要用「我改得很快」说服自己先动手。**
本轮每一次互相覆盖事故，都是这样开始的。

---

## 6. 相关背景（供理解，不必深究）

### 6.1 `Config.launcher` 的来源
`Config.launcher`（`LaunchScopeConfig`）由 **CR-003** 引入，
`DEFAULT_PROJECT_SCOPE_BASE = str(REPO_ROOT)` 由 `__file__` 派生（无盘符字面量）。
它**存活在 risk1-scope 正在编辑的 `config.py` 里**，
**所以他们 commit 时会把它一起带进去** → `config.py` 在 A8 pin 内 → **需要重基线**。

### 6.2 P4 的相邻历史
P2（`05b4f9a`）刚把 `jcc_settings.py` 的 P2 门从 `assert` 改成 `raise`，
理由是 `python -O` 会剥掉 assert。**P4 是同一类问题的另一个面**：
不是 assert 被剥，而是 `compare_digest` 对非 ASCII 抛异常。
两者都属「门在特殊输入下失效」。

---

## 7. 提交要求

- commit message 引用本文件路径（`docs/P3_P4_SPEC.md`）作为规格出处
- 包含 §2.5 的安全性推理（P3）
- 包含 §3.2 的性质说明（P4）
- 说明 `/ws` 与 HTTP 两处同批修的理由（§3.3）
- **明确列出你改动的、属于 A8 pin 的文件**（目前已知 `server.py`）
