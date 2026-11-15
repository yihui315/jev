// kill-switch.mjs — 人工熔断；绝不自动清除、绝不超时恢复
import { FILES, resolvePath, atomicWriteJson, readJson } from './core.mjs';

export function createKillSwitch(deps) {
  const { fsImpl, baseDir, nowMs } = deps;
  const switchPath = resolvePath(baseDir, FILES.killSwitch);

  // 只看文件是否存在：无超时、无自动恢复、无自动清除。
  function isHalted() {
    return fsImpl.existsSync(switchPath);
  }

  function read() { return readJson(fsImpl, switchPath, null); }

  function arm(token) {
    if (!token || typeof token !== 'string') throw new Error('KILL_SWITCH_TOKEN_REQUIRED');
    atomicWriteJson(fsImpl, switchPath, {
      armedBy: token,
      armedAtMs: nowMs(),
      autoClear: false,        // 明确登记：绝不自动清除
      expiryMs: null,          // 无超时字段可用（null，绝不写 0）
      note: 'manual clear requires explicit matching token; no timeout recovery',
    });
    return read();
  }

  function clear(token) {
    if (!token || typeof token !== 'string') throw new Error('KILL_SWITCH_TOKEN_REQUIRED');
    const cur = read();
    if (!cur) return { cleared: false, reason: 'NOT_ARMED' };
    if (cur.armedBy !== token) return { cleared: false, reason: 'TOKEN_MISMATCH' };
    fsImpl.unlinkSync(switchPath);   // 仅显式匹配 token 才清除
    return { cleared: true };
  }

  return { isHalted, arm, clear, read, switchPath };
}
