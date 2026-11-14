// leader-election.mjs — 基于文件锁的租约选举（语义移植自 JCC supervisor，不 import JCC 代码）
import { FILES, resolvePath, readJson, canonicalize } from './core.mjs';

export function createLeaderElection(deps) {
  const { nowMs, fsImpl, baseDir } = deps;
  const lockPath = resolvePath(baseDir, FILES.lock);

  function inspectLease() {
    return readJson(fsImpl, lockPath, null);
  }

  function isHeldBy(ownerToken) {
    const cur = inspectLease();
    return !!cur && cur.ownerToken === ownerToken;
  }

  // fs.openSync(path, 'wx') 原子排他创建：拿到创建权即拿到本周期领导权。
  function acquireLease(opts = {}) {
    const ttlMs = opts.leaseTtlMs ?? deps.leaseTtlMs ?? 60000;
    const ownerToken = opts.ownerToken ?? deps.ownerToken;
    let fd = null;
    let tookOverExpired = false;
    try {
      fd = fsImpl.openSync(lockPath, 'wx');
    } catch (e) {
      if (e.code !== 'EEXIST') throw e;
      const cur = inspectLease();
      if (!cur || typeof cur.leaseExpiryMs !== 'number') {
        // 锁不可读一律 fail-closed，不接管、不猜测。
        return { acquired: false, ownerToken: null, reason: 'LOCK_UNREADABLE_FAIL_CLOSED' };
      }
      // 仅当 nowMs 严格大于 leaseExpiryMs 才允许接管过期锁。
      if (!(nowMs() > cur.leaseExpiryMs)) {
        return {
          acquired: false, ownerToken: null, holder: cur.ownerToken,
          leaseExpiryMs: cur.leaseExpiryMs, reason: 'LEASE_HELD_NOT_EXPIRED',
        };
      }
      fsImpl.unlinkSync(lockPath);
      fd = fsImpl.openSync(lockPath, 'wx');
      tookOverExpired = true;
    }
    const acquiredAtMs = nowMs();
    const leaseExpiryMs = acquiredAtMs + ttlMs;
    const record = { ownerToken, leaseExpiryMs, acquiredAtMs, ttlMs, tookOverExpired };
    fsImpl.writeFileSync(lockPath, canonicalize(record));
    if (fd !== null) fsImpl.closeSync(fd);
    return { acquired: true, ownerToken, leaseExpiryMs, acquiredAtMs, tookOverExpired };
  }

  // 释放锁时必须校验 ownerToken：非持有者不得删除他人锁。
  function releaseLease(ownerToken) {
    if (!ownerToken) return { released: false, reason: 'TOKEN_REQUIRED' };
    if (!fsImpl.existsSync(lockPath)) return { released: false, reason: 'NO_LOCK' };
    const cur = inspectLease();
    if (!cur || cur.ownerToken !== ownerToken) {
      return { released: false, reason: 'OWNER_MISMATCH', holder: cur ? cur.ownerToken : null };
    }
    fsImpl.unlinkSync(lockPath);
    return { released: true, ownerToken };
  }

  return { acquireLease, releaseLease, inspectLease, isHeldBy, lockPath };
}
