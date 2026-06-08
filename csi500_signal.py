#!/usr/bin/env python3
"""
中证500 15日均线买卖信号计算工具
- 获取中证500全部日K数据（前复权）
- 计算15日均线
- 判断当前信号：上穿=买入 / 跌破=卖出 / 无信号
- 动态回测（T+1次日开盘价成交）
- 输出JSON供HTML展示
"""

import json
import sys
import os
import time
from datetime import datetime, timedelta

import requests


# ==================== 数据获取 ====================

def fetch_realtime_quote_tencent():
    """从腾讯获取中证500实时行情"""
    url = "https://qt.gtimg.cn/q=sh000905"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "gbk"
        text = resp.text
        # 格式: v_sh000905="1~中证500~000905~7963.45~7978.66~8142.34~..."
        if "~" not in text:
            return None
        parts = text.split("~")
        if len(parts) < 40:
            return None
        # parts[3]=当前价, parts[4]=昨收, parts[5]=开盘, parts[33]=最高, parts[34]=最低
        price = float(parts[3]) if parts[3] else None
        yclose = float(parts[4]) if parts[4] else None
        open_p = float(parts[5]) if parts[5] else None
        high = float(parts[33]) if len(parts) > 33 and parts[33] else None
        low = float(parts[34]) if len(parts) > 34 and parts[34] else None
        chg_pct = round((price - yclose) / yclose * 100, 2) if price and yclose else None
        return {
            "price": round(price, 2) if price else None,
            "chg_pct": chg_pct,
            "chg_amt": round(price - yclose, 2) if price and yclose else None,
            "open": round(open_p, 2) if open_p else None,
            "high": round(high, 2) if high else None,
            "low": round(low, 2) if low else None,
            "yclose": round(yclose, 2) if yclose else None,
        }
    except Exception:
        return None


def fetch_realtime_quote_eastmoney():
    """从东方财富获取中证500实时行情（备用）"""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": "1.000905",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields": "f43,f44,f45,f46,f169,f170,f60,f86",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if data.get("data"):
            d = data["data"]
            price = d.get("f43", 0) / 100 if d.get("f43") else None
            return {
                "price": round(price, 2) if price else None,
                "chg_pct": round(d.get("f170", 0) / 100, 2) if d.get("f170") is not None else None,
                "chg_amt": round(d.get("f169", 0) / 100, 2) if d.get("f169") is not None else None,
                "open": round(d.get("f46", 0) / 100, 2) if d.get("f46") else None,
                "high": round(d.get("f44", 0) / 100, 2) if d.get("f44") else None,
                "low": round(d.get("f45", 0) / 100, 2) if d.get("f45") else None,
                "yclose": round(d.get("f60", 0) / 100, 2) if d.get("f60") else None,
            }
    except Exception:
        pass
    return None


def fetch_realtime_quote():
    """获取中证500实时行情 —— 优先腾讯，降级东方财富"""
    rt = fetch_realtime_quote_tencent()
    if rt:
        return rt
    return fetch_realtime_quote_eastmoney()


def fetch_csi500_kline_tencent(lmt=320):
    """从腾讯获取中证500日K线（支持最多~2000条，lmt越大越久远）—— 主数据源"""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    # lmt 最大约2000, 按需调整: 320≈1年, 640≈2.5年, 2000≈8年
    actual_lmt = min(lmt, 2000)
    params = {"param": f"sh000905,day,,,{actual_lmt},qfq"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            return {"error": f"腾讯接口返回错误: {data.get('msg','')}"}
        stock_data = data.get("data", {})
        if not isinstance(stock_data, dict):
            return {"error": "腾讯接口数据格式异常"}
        sh_data = stock_data.get("sh000905", {})
        if not isinstance(sh_data, dict):
            return {"error": "腾讯接口数据格式异常"}
        days = sh_data.get("day", [])
        if not days:
            return {"error": "腾讯接口返回无数据"}
        records = []
        for d in days:
            if not isinstance(d, list) or len(d) < 5:
                continue
            records.append({
                "date": str(d[0]),
                "open": float(d[1]),
                "close": float(d[2]),
                "high": float(d[3]),
                "low": float(d[4]),
                "volume": float(d[5]) if len(d) > 5 and d[5] else 0,
            })
        # 同时提取实时行情 qt
        qt = sh_data.get("qt", {}).get("sh000905", [])
        return {"records": records, "count": len(records), "qt": qt}
    except Exception as e:
        return {"error": f"腾讯接口请求失败: {e}"}


def fetch_csi500_kline_eastmoney(period=5200):
    """从东方财富获取中证500日K线数据（前复权）—— 备用数据源"""
    secid = "1.000905"
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "end": "20500101",
        "lmt": period,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            data = resp.json()
            if data.get("data") is None or data["data"].get("klines") is None:
                return {"error": "东方财富接口返回无数据"}
            klines = data["data"]["klines"]
            records = []
            for line in klines:
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                records.append({
                    "date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                })
            return {"records": records, "count": len(records)}
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return {"error": "东方财富接口请求失败(重试3次)"}


def fetch_csi500_kline(period=5200):
    """获取中证500日K线 —— 优先腾讯，降级东方财富"""
    print("📡 尝试腾讯数据源...")
    result = fetch_csi500_kline_tencent(period)
    if "error" not in result:
        print(f"✅ 腾讯数据源成功: {result['count']}条")
        return result
    print(f"⚠️ 腾讯数据源失败: {result['error']}")
    print("📡 尝试东方财富数据源...")
    result = fetch_csi500_kline_eastmoney(period)
    if "error" not in result:
        print(f"✅ 东方财富数据源成功: {result['count']}条")
        return result
    print(f"⚠️ 东方财富数据源也失败: {result['error']}")
    return result


def calc_ma15(records):
    """计算15日均线"""
    for i in range(len(records)):
        if i >= 14:
            ma15 = sum(r["close"] for r in records[i-14:i+1]) / 15
        else:
            ma15 = None
        records[i]["ma15"] = ma15
    return records


def find_signals(records):
    """
    检测信号：
    - 上穿（买入）：今日收盘 > 今日MA15 且 昨日收盘 <= 昨日MA15
    - 跌破（卖出）：今日收盘 < 今日MA15 且 昨日收盘 >= 昨日MA15
    - 持仓中：今日收盘 > 今日MA15 (继续持有)
    - 空仓中：今日收盘 < 今日MA15 (继续观望)
    """
    signals = []
    for i in range(1, len(records)):
        t = records[i]
        y = records[i-1]

        if t["ma15"] is None or y["ma15"] is None:
            continue

        t_close, t_ma = t["close"], t["ma15"]
        y_close, y_ma = y["close"], y["ma15"]

        if t_close > t_ma and y_close <= y_ma:
            signal = "BUY"
        elif t_close < t_ma and y_close >= y_ma:
            signal = "SELL"
        elif t_close > t_ma:
            signal = "HOLD"
        else:
            signal = "WAIT"

        signals.append({
            "date": t["date"],
            "close": round(t_close, 2),
            "ma15": round(t_ma, 2),
            "signal": signal,
            "pct_from_ma": round((t_close - t_ma) / t_ma * 100, 2),
        })
    return signals


def calc_ma_extra(records):
    """计算MA20, MA60, MA120用于趋势判断"""
    for i in range(len(records)):
        if i >= 19:
            records[i]["ma20"] = round(sum(r["close"] for r in records[i-19:i+1]) / 20, 2)
        else:
            records[i]["ma20"] = None

        if i >= 59:
            records[i]["ma60"] = round(sum(r["close"] for r in records[i-59:i+1]) / 60, 2)
        else:
            records[i]["ma60"] = None

        if i >= 119:
            records[i]["ma120"] = round(sum(r["close"] for r in records[i-119:i+1]) / 120, 2)
        else:
            records[i]["ma120"] = None
    return records


def run_backtest(records):
    """
    动态回测：15日均线策略
    T+1执行 - 信号日T产生信号 → T+1日以开盘价买入/卖出
    持仓期间按close-to-close计算NAV
    空仓期间NAV不变

    返回回测摘要统计
    """
    # 1) 生成信号序列
    sigs = [None]  # day 0 no signal
    for i in range(1, len(records)):
        t = records[i]; y = records[i-1]
        if t["ma15"] is None or y["ma15"] is None:
            sigs.append(None)
            continue
        if t["close"] > t["ma15"] and y["close"] <= y["ma15"]:
            sigs.append("BUY")
        elif t["close"] < t["ma15"] and y["close"] >= y["ma15"]:
            sigs.append("SELL")
        elif t["close"] > t["ma15"]:
            sigs.append("HOLD")
        else:
            sigs.append("WAIT")

    # 2) 持仓状态（T+1执行：信号日i → i+1日开盘执行）
    in_pos = [False] * len(records)
    for i in range(1, len(records)):
        if sigs[i-1] == "BUY":
            in_pos[i] = True
        elif sigs[i-1] == "SELL":
            in_pos[i] = False
        else:
            in_pos[i] = in_pos[i-1]

    # 3) NAV计算
    nav = [1.0]
    bah_nav = [1.0]
    for i in range(1, len(records)):
        bah_nav.append(bah_nav[-1] * records[i]["close"] / records[i-1]["close"])

        if in_pos[i] and not in_pos[i-1]:
            # T+1开盘买入
            nav.append(nav[-1] * records[i]["close"] / records[i]["open"])
        elif not in_pos[i] and in_pos[i-1]:
            # T+1开盘卖出
            nav.append(nav[-1] * records[i]["open"] / records[i-1]["close"])
        elif in_pos[i]:
            # 持仓中 close-to-close
            nav.append(nav[-1] * records[i]["close"] / records[i-1]["close"])
        else:
            # 空仓
            nav.append(nav[-1])

    # 4) 统计交易
    trades = []
    entry_idx = None
    for i in range(1, len(records)):
        if in_pos[i] and not in_pos[i-1]:
            entry_idx = i
        elif not in_pos[i] and in_pos[i-1]:
            if entry_idx is not None:
                pnl = (records[i]["open"] - records[entry_idx]["open"]) / records[entry_idx]["open"] * 100
                trades.append({
                    "entry_date": records[entry_idx]["date"],
                    "exit_date": records[i]["date"],
                    "entry_price": records[entry_idx]["open"],
                    "exit_price": records[i]["open"],
                    "pnl_pct": round(pnl, 2),
                    "hold_days": i - entry_idx,
                })
                entry_idx = None

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    total_trades = len(trades)
    win_trades = len(wins)
    loss_trades = len(losses)

    # 5) 最大回撤
    def max_drawdown(nav_list):
        peak = nav_list[0]
        mdd = 0
        for n in nav_list:
            if n > peak:
                peak = n
            dd = (peak - n) / peak * 100
            if dd > mdd:
                mdd = dd
        return round(mdd, 1)

    # 6) 年化收益
    d0 = datetime.strptime(records[0]["date"], "%Y-%m-%d")
    d1 = datetime.strptime(records[-1]["date"], "%Y-%m-%d")
    years = (d1 - d0).days / 365.25

    strat_return = round((nav[-1] - 1) * 100, 2)
    bah_return_val = round((bah_nav[-1] - 1) * 100, 2)
    ann_return = round((nav[-1] ** (1 / years) - 1) * 100, 2) if years > 0 else 0
    ann_bah = round((bah_nav[-1] ** (1 / years) - 1) * 100, 2) if years > 0 else 0

    win_rate = round(win_trades / total_trades * 100, 1) if total_trades > 0 else 0
    avg_win = round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0
    pl_ratio = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0

    return {
        "total_return": f"{strat_return:.0f}%",
        "annual_return": f"{ann_return}%",
        "win_rate": f"{win_rate}%",
        "profit_loss_ratio": pl_ratio,
        "total_trades": total_trades,
        "win_trades": win_trades,
        "loss_trades": loss_trades,
        "period": f"{records[0]['date']} ~ {records[-1]['date']}",
        "bah_return": f"{bah_return_val:.0f}%",
        "excess_return": f"+{strat_return - bah_return_val:.0f}%",
        "avg_win": f"+{avg_win}%",
        "avg_loss": f"{avg_loss}%",
        "strategy_mdd": f"{max_drawdown(nav)}%",
        "bah_mdd": f"{max_drawdown(bah_nav)}%",
        "note": "T+1 信号次日开盘价成交",
        # 数值版供图表使用
        "_strat_return_num": strat_return,
        "_bah_return_num": bah_return_val,
    }


# ==================== 主流程 ====================

def load_existing_chart(script_dir):
    """加载仓库中已有的 csi500_chart.json 作为历史数据"""
    chart_path = os.path.join(script_dir, "csi500_chart.json")
    if not os.path.exists(chart_path):
        return None
    try:
        with open(chart_path, "r", encoding="utf-8") as f:
            chart = json.load(f)
        klines = chart.get("kline", [])
        records = []
        for k in klines:
            records.append({
                "date": k["date"],
                "open": float(k["open"]),
                "close": float(k["close"]),
                "high": float(k["high"]),
                "low": float(k["low"]),
                "volume": 0,
            })
        print(f"\U0001f4c2 已加载本地历史数据: {len(records)}条 ({records[0]['date']} ~ {records[-1]['date']})")
        return records
    except Exception as e:
        print(f"\u26a0\ufe0f 加载本地数据失败: {e}")
        return None


def merge_records(existing_records, new_records):
    """合并新旧数据：用新数据覆盖重叠日期，追加新日期"""
    date_map = {r["date"]: r for r in existing_records} if existing_records else {}
    for r in new_records:
        date_map[r["date"]] = r
    merged = sorted(date_map.values(), key=lambda x: x["date"])
    return merged


def extract_rt_from_qt(qt):
    """从腾讯K线接口返回的qt字段提取实时行情"""
    if not qt or len(qt) < 35:
        return None
    try:
        price = float(qt[3]) if qt[3] else None
        yclose = float(qt[4]) if qt[4] else None
        open_p = float(qt[5]) if qt[5] else None
        high = float(qt[33]) if qt[33] else None
        low = float(qt[34]) if qt[34] else None
        chg_pct = round((price - yclose) / yclose * 100, 2) if price and yclose else None
        return {
            "price": round(price, 2) if price else None,
            "chg_pct": chg_pct,
            "chg_amt": round(price - yclose, 2) if price and yclose else None,
            "open": round(open_p, 2) if open_p else None,
            "high": round(high, 2) if high else None,
            "low": round(low, 2) if low else None,
            "yclose": round(yclose, 2) if yclose else None,
        }
    except (ValueError, IndexError):
        return None


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 1) 加载已有历史数据（必须，作为数据底座）
    existing_records = load_existing_chart(script_dir)
    if existing_records is None:
        print("\u274c 无法加载本地历史数据，退出")
        sys.exit(1)

    # 2) 从 API 获取最新数据（腾讯优先）
    result = fetch_csi500_kline(320)  # 拉取最近~1年数据用于合并
    rt = None

    if "error" not in result and result.get("records"):
        new_records = result["records"]
        records = merge_records(existing_records, new_records)

        # 从腾讯K线接口自带qt中提取实时行情
        if "qt" in result and result["qt"]:
            rt = extract_rt_from_qt(result["qt"])
        api_success = True
    else:
        print(f"\u26a0\ufe0f API 获取失败，使用本地数据")
        records = existing_records
        api_success = False

    # 3) 如果腾讯qt没拿到实时行情，单独拉取
    if rt is None:
        rt = fetch_realtime_quote()
        if rt:
            print(f"\U0001f4ca 腾讯实时行情获取成功: price={rt['price']}")

    # 4) 用实时收盘价更新最后一天K线
    if rt and rt.get("price") and records:
        records[-1]["close"] = rt["price"]
        if rt.get("high"):
            records[-1]["high"] = rt["high"]
        if rt.get("low"):
            records[-1]["low"] = rt["low"]
        if rt.get("open"):
            records[-1]["open"] = rt["open"]
        print(f"\U0001f4ca 已用实时行情更新数据: close={rt['price']} (K线日期={records[-1]['date']})")

    records = calc_ma15(records)
    records = calc_ma_extra(records)
    signals = find_signals(records)

    # 动态回测
    bt = run_backtest(records)

    # 最近一条信号
    latest_signal = signals[-1] if signals else None

    # 最近10条信号
    recent = signals[-10:] if len(signals) >= 10 else signals

    # 当前最新数据
    latest = records[-1]

    # 判断大盘趋势（MA60, MA120）
    trend_short = "上升" if latest.get("ma20") and latest["close"] > latest["ma20"] else "下降"
    trend_mid = "上升" if latest.get("ma60") and latest["close"] > latest["ma60"] else "下降"
    trend_long = "上升" if latest.get("ma120") and latest["close"] > latest["ma120"] else "下降"

    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "index_name": "中证500",
        "index_code": "000905",
        "data_range": f"{records[0]['date']} ~ {records[-1]['date']}",
        "latest": {
            "date": latest["date"],
            "close": round(latest["close"], 2),
            "ma15": round(latest["ma15"], 2) if latest["ma15"] else None,
            "ma20": latest.get("ma20"),
            "ma60": latest.get("ma60"),
            "ma120": latest.get("ma120"),
            "pct_from_ma15": round((latest["close"] - latest["ma15"]) / latest["ma15"] * 100, 2) if latest["ma15"] else None,
        },
        "current_signal": {
            "signal": latest_signal["signal"],
            "signal_cn": {
                "BUY": "🟢 上穿买入",
                "SELL": "🔴 跌破卖出",
                "HOLD": "🟡 持仓中（线上）",
                "WAIT": "⚪ 空仓观望（线下）",
            }.get(latest_signal["signal"], "未知"),
            "date": latest_signal["date"],
            "close": latest_signal["close"],
            "ma15": latest_signal["ma15"],
            "pct_from_ma": latest_signal["pct_from_ma"],
        },
        "trend": {
            "ma20": f"{trend_short}（价格{'>' if trend_short=='上升' else '<'}MA20={latest.get('ma20')}）",
            "ma60": f"{trend_mid}（价格{'>' if trend_mid=='上升' else '<'}MA60={latest.get('ma60')}）",
            "ma120": f"{trend_long}（价格{'>' if trend_long=='上升' else '<'}MA120={latest.get('ma120')}）",
        },
        "recent_signals": recent,
        "price_stats": {
            "high_30d": round(max(r["high"] for r in records[-30:]), 2),
            "low_30d": round(min(r["low"] for r in records[-30:]), 2),
            "avg_30d": round(sum(r["close"] for r in records[-30:]) / 30, 2),
        },
        "backtest_summary": {k: v for k, v in bt.items() if not k.startswith("_")},
        "realtime": rt,
    }

    # ==================== 保存输出文件 ====================
    # script_dir 已在主流程开头定义

    # 1) csi500_signal.json（完整信号数据，兼容旧格式）
    out_path = os.path.join(script_dir, "csi500_signal.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ csi500_signal.json 已保存")

    # 2) csi500_data.json（仪表盘用，HTML fetch加载）
    data_path = os.path.join(script_dir, "csi500_data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    data_size = os.path.getsize(data_path)
    print(f"✅ csi500_data.json 已保存: {data_size//1024}KB")

    # 3) csi500_chart.json（图表用，全部K线+MA15+信号）
    chart_kline = [{"date": r["date"], "open": r["open"], "close": r["close"], "high": r["high"], "low": r["low"]} for r in records]
    chart_ma15 = [r["ma15"] for r in records]
    chart_signals = []
    position = 0
    for i in range(len(records)):
        if records[i]["ma15"] is None or (i > 0 and records[i-1]["ma15"] is None):
            chart_signals.append(None)
            continue
        r = records[i]; prev_r = records[i-1]
        if r["close"] > r["ma15"] and prev_r["close"] <= prev_r["ma15"]:
            chart_signals.append("HOLD" if position == 1 else "BUY")
            if position == 0: position = 1
        elif r["close"] < r["ma15"] and prev_r["close"] >= prev_r["ma15"]:
            chart_signals.append("WAIT" if position == 0 else "SELL")
            if position == 1: position = 0
        else:
            chart_signals.append("HOLD" if position == 1 else "WAIT")
    chart_data = {"kline": chart_kline, "ma15": chart_ma15, "signals": chart_signals}
    chart_path = os.path.join(script_dir, "csi500_chart.json")
    with open(chart_path, "w", encoding="utf-8") as f:
        json.dump(chart_data, f, ensure_ascii=False, separators=(',', ':'))
    chart_size = os.path.getsize(chart_path)
    print(f"✅ csi500_chart.json 已保存: {chart_size//1024}KB ({len(records)}条K线)")

    # 4) 确保HTML是最新版本（32KB fetch版）
    html_path = os.path.join(script_dir, "csi500_dashboard.html")
    html_size = os.path.getsize(html_path) if os.path.exists(html_path) else 0
    if html_size > 100000:
        # HTML文件太大，说明还是旧版内嵌数据版本，重新生成
        import subprocess
        build_script = os.path.join(script_dir, "_build_html.py")
        if os.path.exists(build_script):
            subprocess.run([sys.executable, build_script], check=True)
            print("✅ HTML已重新生成（fetch版本）")
        else:
            print("⚠️ _build_html.py 不存在，跳过HTML重建")
    else:
        print(f"✅ csi500_dashboard.html 已是最新版 ({html_size//1024}KB)")

    # 简要输出
    print(f"\n数据范围: {records[0]['date']} ~ {records[-1]['date']} ({len(records)}条)")
    print(f"当前信号: {latest_signal['signal']} @ {latest_signal['date']}")
    print(f"回测总收益: {bt['total_return']}, 买入持有: {bt['bah_return']}")
# auto update Sat Jun  6 04:27:27 PM CST 2026
