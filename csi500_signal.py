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

def fetch_realtime_quote():
    """从东方财富获取中证500实时行情"""
    secid = "1.000905"
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": secid,
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
    except Exception as e:
        pass
    return None


def fetch_csi500_kline(period=5200):
    """从东方财富获取中证500日K线数据（前复权）"""
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

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            data = resp.json()
            break
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(3)
            else:
                return {"error": f"网络请求失败(重试{max_retries}次): {e}"}

    if data.get("data") is None or data["data"].get("klines") is None:
        return {"error": "接口返回无数据"}

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
    动态回测：15日均线策略 T+1执行
    - 信号日T产生信号 → T+1日以开盘价买入/卖出
    - 持仓期间按close-to-close计算NAV
    - 空仓期间NAV不变
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

if __name__ == "__main__":
    result = fetch_csi500_kline(5200)  # 全部历史

    if "error" in result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 获取实时行情
    rt = fetch_realtime_quote()

    records = result["records"]
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
    script_dir = os.path.dirname(os.path.abspath(__file__))

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
