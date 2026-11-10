// 定点金额计算模块（BigInt, scale = 1e8）
//
// 设计目标：消除 IEEE-754 浮点在货币/数量累加中的表示误差。
// - 所有货币（USDT）、价格、数量、名义金额统一以 BigInt 存储，比例因子 1e8。
// - BigInt 提供任意精度整数算术，仅在 mul/div 时按 SCALE 四舍五入到最近。
// - JSON 不能序列化 BigInt，故对象落库时金额字段以**十进制字符串**表示；
//   读取时 parseDecimal 还原为 BigInt。状态哈希（sha256）对字符串化结果一致计算，
//   不影响事件回放/审计。
//
// 这是对分支 A 原 `REAL` 浮点 + `toFixed(12)` 方案的定点化修正（指令 Phase 1 硬要求）。

export const SCALE = 10n ** 8n; // 1e8：8 位小数精度，覆盖 BTC/USDT 量级
export const DECIMALS = 8;
export const ZERO = 0n;
export const ONE_UNIT = SCALE; // 1.00000000 个计价单位

// 解析任意源（number/string/bigint）为定点 BigInt。
// 超出 8 位小数的部分按"最近"四舍五入（远离零）。
export function D(value) {
  if (typeof value === "bigint") return value;
  if (typeof value === "string") {
    const s = value.trim();
    if (s === "" || s === "-") return 0n;
    if (!/^-?\d+(\.\d+)?$/.test(s)) throw new Error("INVALID_DECIMAL_STRING:" + value);
    return _fromString(s);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("INVALID_DECIMAL_NUMBER:" + value);
    // 通过字符串桥接，避免 number->BigInt 直接截断误差
    return _fromString(value.toFixed(DECIMALS + 4));
  }
  throw new Error("UNSUPPORTED_DECIMAL_INPUT:" + typeof value);
}

function _fromString(s) {
  const neg = s.startsWith("-");
  const body = neg ? s.slice(1) : s;
  const [intPart, fracPart = ""] = body.split(".");
  const fracPadded = (fracPart + "0".repeat(DECIMALS)).slice(0, DECIMALS + 1); // 多取 1 位用于四舍五入
  const intVal = BigInt(intPart || "0") * SCALE;
  const fracVal = BigInt(fracPadded.slice(0, DECIMALS));
  const roundDigit = Number(fracPadded[DECIMALS]);
  let total = intVal + fracVal;
  if (roundDigit >= 5) total += 1n;
  return neg ? -total : total;
}

// 四舍五入除法（远离零）
function _roundDiv(num, den) {
  if (den === 0n) throw new Error("DIVIDE_BY_ZERO");
  const neg = (num < 0n) !== (den < 0n);
  const a = num < 0n ? -num : num;
  const b = den < 0n ? -den : den;
  const q = a / b;
  const r = a % b;
  let result = q;
  if (r * 2n >= b) result += 1n; // 半向上舍入
  return neg ? -result : result;
}

export const add = (a, b) => D(a) + D(b);
export const sub = (a, b) => D(a) - D(b);
export const neg = (a) => -D(a);
export const abs = (a) => { const x = D(a); return x < 0n ? -x : x; };

// 乘法：先乘再按 SCALE 归约
export const mul = (a, b) => _roundDiv(D(a) * D(b), SCALE);
// 除法：先放大再除
export const div = (a, b) => _roundDiv(D(a) * SCALE, D(b));

export const mulInt = (a, bInt) => D(a) * BigInt(bInt); // 乘以整数（无归约）
export const scaleUp = (a, decimals) => D(a) * (10n ** BigInt(decimals));

export const isZero = (a) => D(a) === 0n;
export const isPos = (a) => D(a) > 0n;
export const isNeg = (a) => D(a) < 0n;

export function cmp(a, b) {
  const x = D(a), y = D(b);
  return x < y ? -1 : x > y ? 1 : 0;
}
export const lt = (a, b) => cmp(a, b) < 0;
export const lte = (a, b) => cmp(a, b) <= 0;
export const gt = (a, b) => cmp(a, b) > 0;
export const gte = (a, b) => cmp(a, b) >= 0;
export const eq = (a, b) => cmp(a, b) === 0;
export const min = (a, b) => (cmp(a, b) <= 0 ? D(a) : D(b));
export const max = (a, b) => (cmp(a, b) >= 0 ? D(a) : D(b));

// BigInt -> 固定 8 位小数字符串（用于 JSON / 审计落库）
export function U(b, decimals = DECIMALS) {
  const x = D(b);
  const neg = x < 0n;
  const s = (neg ? -x : x).toString().padStart(decimals + 1, "0");
  const intPart = s.slice(0, s.length - decimals);
  const fracPart = s.slice(s.length - decimals);
  return (neg ? "-" : "") + intPart + "." + fracPart;
}

// 去除末尾多余的 0 与小数点（用于 API 展示）
export function trimmed(b) {
  return U(b).replace(/\.?0+$/, "");
}

// 落库/读取桥接：对象内的金额字段以字符串表示
export const toJson = (b) => U(b);
export const fromJson = (s) => (typeof s === "string" ? D(s) : D(s ?? 0));

// 危险：转 Number 仅用于 UI 展示且数值较小处；大额会丢失精度，调用方自负。
export function toNumber(b) {
  return Number(U(b));
}

export const __test = { SCALE, D, U, add, sub, mul, div, cmp, _fromString, _roundDiv };
