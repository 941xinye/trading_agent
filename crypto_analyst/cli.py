"""命令行入口：brief / ask / watch / snapshot。"""
from __future__ import annotations

import argparse
import json
import sys

from . import analysis, alerts, backtest, briefing, config, net
from .agent import MarketAnalyst
from .llm import LLMError
from .providers.exchanges import EXCHANGE_NAMES


def _parse_symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crypto-analyst",
        description="币圈行情分析师 Agent：多周期技术面 + 持仓结构 + 链上流动性 + 情绪 + 宏观联动",
    )
    sub = parser.add_subparsers(dest="command")

    exchange = argparse.ArgumentParser(add_help=False)
    exchange.add_argument(
        "--exchange",
        choices=list(EXCHANGE_NAMES),
        help="临时切换行情/永续数据源，不填则读 .env 的 EXCHANGE",
    )

    p_brief = sub.add_parser("brief", parents=[exchange], help="生成结构化行情简报（Markdown）")
    p_brief.add_argument("--symbols", help="逗号分隔的币种，如 BTC,ETH,SOL")
    p_brief.add_argument("--no-llm", action="store_true", help="只输出规则引擎结果，不调用大模型")
    p_brief.add_argument("--stdout", action="store_true", help="同时把简报打印到终端")
    p_brief.add_argument("--push", action="store_true", help="推送卡片消息到飞书机器人（需配置 WEBHOOK_URL）")

    p_ask = sub.add_parser("ask", parents=[exchange], help="对话式提问，Agent 会自主调用数据工具")
    p_ask.add_argument("question", nargs="?", help="你的问题，例如：BTC 现在能追多吗")
    p_ask.add_argument("--symbols", help="临时覆盖观察列表")
    p_ask.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="进入多轮对话模式：/reset 清空上下文，/exit 退出",
    )

    p_watch = sub.add_parser("watch", parents=[exchange], help="轮询监控异动并推送告警")
    p_watch.add_argument("--interval", type=int, help="轮询间隔秒数，默认 300")
    p_watch.add_argument("--once", action="store_true", help="只跑一轮，便于测试")

    p_serve = sub.add_parser(
        "serve",
        parents=[exchange],
        help="内置调度：异动监控 + 每日定时简报推送（替代 schtasks 双任务）",
    )
    p_serve.add_argument("--interval", type=int, help="异动轮询间隔秒数，默认 300")
    p_serve.add_argument(
        "--times",
        help="每日简报推送时间，逗号分隔，如 09:00,21:00；默认读 BRIEF_PUSH_TIMES",
    )
    p_serve.add_argument("--once", action="store_true", help="只跑一轮监控与到点简报，便于测试")

    p_snap = sub.add_parser("snapshot", parents=[exchange], help="输出结构化 JSON 快照（调试/二次开发用）")
    p_snap.add_argument("--symbols", help="逗号分隔的币种")
    p_snap.add_argument("--out", help="保存 JSON 的文件路径")

    p_push_test = sub.add_parser("push-test", help="向 Webhook 发送一条测试消息，验证飞书机器人连通性")
    p_push_test.add_argument("--card", action="store_true", help="发送 interactive 卡片样式而非纯文本")

    p_backtest = sub.add_parser("backtest", help="评分回测：用落盘快照检验综合分与未来收益的关系（离线）")
    p_backtest.add_argument(
        "--hours",
        default="24",
        help="未来收益窗口（小时），可逗号分隔多个：4,24,72，默认 24",
    )
    p_backtest.add_argument(
        "--sweep",
        action="store_true",
        help="附加权重敏感性扫描（各维度 ±5pp 对相关系数的影响）",
    )
    p_backtest.add_argument(
        "--recommend",
        action="store_true",
        help="按各维度 IC 给出推荐权重（样本不足 30 个窗口时只展示 IC）",
    )
    p_backtest.add_argument(
        "--source",
        choices=["watch", "brief", "snapshot", "ask", "all"],
        default="watch",
        help="只统计该来源的快照，默认 watch（持续采集，样本最干净）；all 为全量",
    )

    p_dash = sub.add_parser("dashboard", help="把落盘快照渲染成静态 HTML 仪表盘（评分走势/维度拆解）")
    p_dash.add_argument("--days", type=int, default=7, help="回看天数，默认 7")
    p_dash.add_argument(
        "--source",
        choices=["watch", "brief", "snapshot", "ask", "all"],
        default="all",
        help="只统计该来源的快照，默认 all",
    )
    p_dash.add_argument("--out", help="输出路径，默认 reports/dashboard.html")

    p_api = sub.add_parser("api", help="启动本地 HTTP API（快照 / 简报 / 问答 / 仪表盘）")
    p_api.add_argument("--host", help="监听地址，默认 127.0.0.1（对外暴露前请配置 API_TOKEN）")
    p_api.add_argument("--port", type=int, help="监听端口，默认 8080")

    p_feishu = sub.add_parser("feishu-hook", help="飞书事件回调：群内 @机器人 直接问答（需自建应用）")
    p_feishu.add_argument("--host", help="监听地址，默认 127.0.0.1")
    p_feishu.add_argument("--port", type=int, help="监听端口，默认 8787")

    return parser


def _cmd_brief(args: argparse.Namespace) -> int:
    snapshot, text, commentary, path = briefing.build(
        symbols=_parse_symbols(args.symbols),
        use_llm=not args.no_llm,
    )
    if not snapshot.symbols:
        print("未获取到任何标的，请检查 CACHE_DIR / 网络。", file=sys.stderr)
        return 1

    if args.stdout:
        print(text)
    print(f"简报已保存：{path}")
    push_failed = False
    if args.push:
        card = alerts.build_brief_card(snapshot, commentary=commentary, report_path=path)
        if alerts.push_card(card):
            print("已推送到飞书。")
        else:
            print("推送失败（简报已正常落盘），请检查 WEBHOOK_URL / WEBHOOK_SECRET 配置。", file=sys.stderr)
            push_failed = True
    if snapshot.warnings:
        print("数据缺口：" + "；".join(snapshot.warnings))
    # 退出码：0 全部成功；3 简报成功但推送失败（便于外部调度区分处理）
    return 3 if push_failed else 0


def _interactive_ask(analyst: MarketAnalyst, symbols: list[str] | None) -> int:
    print("已进入多轮对话：/reset 清空上下文，/exit 退出。")
    while True:
        try:
            question = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        if question in ("/exit", "/quit"):
            return 0
        if question == "/reset":
            analyst.reset()
            print("上下文已清空。")
            continue
        try:
            print("\n分析师> " + analyst.chat(question, symbols=symbols))
        except LLMError as exc:
            print(str(exc), file=sys.stderr)


def _cmd_ask(args: argparse.Namespace) -> int:
    analyst = MarketAnalyst()
    symbols = _parse_symbols(args.symbols)
    if args.interactive:
        return _interactive_ask(analyst, symbols)
    if not args.question:
        print("请提供问题（如 `ask \"BTC 现在能追多吗\"`），或用 -i 进入多轮对话。", file=sys.stderr)
        return 1
    try:
        answer = analyst.ask(args.question, symbols=symbols)
    except LLMError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(answer)
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    from . import dashboard

    path = dashboard.save(days=args.days, source=args.source, out=args.out)
    print(f"仪表盘已生成：{path}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    alerts.run_watch(interval=args.interval, once=args.once)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from . import scheduler

    scheduler.serve(interval=args.interval, times=args.times, once=args.once)
    return 0


def _cmd_api(args: argparse.Namespace) -> int:
    from . import api

    api.serve(host=args.host, port=args.port)
    return 0


def _cmd_feishu_hook(args: argparse.Namespace) -> int:
    from . import feishu_bot

    feishu_bot.serve(host=args.host, port=args.port)
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    horizons = [float(part) for part in str(args.hours).split(",") if part.strip()]
    print(
        backtest.run(
            horizon_hours=horizons or 24.0,
            source=args.source,
            sweep_weights=args.sweep,
            recommend=args.recommend,
        )
    )
    return 0


def _cmd_push_test(args: argparse.Namespace) -> int:
    from datetime import datetime

    if not config.WEBHOOK_URL:
        print("未配置 WEBHOOK_URL，请先在 .env 中填入飞书机器人 Webhook 地址。", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if args.card:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "green",
                "title": {"tag": "plain_text", "content": "推送链路自检"},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**链析 · 币圈行情分析师**\n"
                            f"interactive 卡片推送链路正常。\n"
                            f"时间：{stamp}"
                        ),
                    },
                }
            ],
        }
        ok = alerts.push_card(card)
    else:
        ok = alerts.push(f"[链析] 推送链路自检 {stamp}")

    if ok:
        print("推送成功，请到飞书群确认收到消息。")
        return 0
    print("推送失败，请检查 WEBHOOK_URL / WEBHOOK_SECRET 与网络。", file=sys.stderr)
    return 2


def _cmd_snapshot(args: argparse.Namespace) -> int:
    snapshot = analysis.build_snapshot(symbols=_parse_symbols(args.symbols), source="snapshot")
    payload = snapshot.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.out:
        from pathlib import Path

        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
        print(f"快照已保存：{path}")
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    removed = net.prune_cache()
    if removed:
        print(f"[缓存] 已清理 {removed} 个过期缓存文件", file=sys.stderr)

    if getattr(args, "exchange", None):
        config.EXCHANGE = args.exchange
    if config.EXCHANGE not in EXCHANGE_NAMES:
        print(
            f"不支持的 EXCHANGE={config.EXCHANGE}，可选：{', '.join(EXCHANGE_NAMES)}",
            file=sys.stderr,
        )
        return 1

    handlers = {
        "brief": _cmd_brief,
        "ask": _cmd_ask,
        "watch": _cmd_watch,
        "serve": _cmd_serve,
        "api": _cmd_api,
        "feishu-hook": _cmd_feishu_hook,
        "snapshot": _cmd_snapshot,
        "push-test": _cmd_push_test,
        "backtest": _cmd_backtest,
        "dashboard": _cmd_dashboard,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
