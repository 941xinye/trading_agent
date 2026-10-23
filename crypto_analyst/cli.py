"""命令行入口：brief / ask / watch / snapshot。"""
from __future__ import annotations

import argparse
import json
import sys

from . import analysis, alerts, backtest, config, report
from .agent import MarketAnalyst
from .llm import LLMClient, LLMError
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
    p_ask.add_argument("question", help="你的问题，例如：BTC 现在能追多吗")
    p_ask.add_argument("--symbols", help="临时覆盖观察列表")

    p_watch = sub.add_parser("watch", parents=[exchange], help="轮询监控异动并推送告警")
    p_watch.add_argument("--interval", type=int, help="轮询间隔秒数，默认 300")
    p_watch.add_argument("--once", action="store_true", help="只跑一轮，便于测试")

    p_snap = sub.add_parser("snapshot", parents=[exchange], help="输出结构化 JSON 快照（调试/二次开发用）")
    p_snap.add_argument("--symbols", help="逗号分隔的币种")
    p_snap.add_argument("--out", help="保存 JSON 的文件路径")

    p_push_test = sub.add_parser("push-test", help="向 Webhook 发送一条测试消息，验证飞书机器人连通性")
    p_push_test.add_argument("--card", action="store_true", help="发送 interactive 卡片样式而非纯文本")

    p_backtest = sub.add_parser("backtest", help="评分回测：用落盘快照检验综合分与未来收益的关系（离线）")
    p_backtest.add_argument("--hours", type=float, default=24.0, help="未来收益窗口，默认 24 小时")

    return parser


def _cmd_brief(args: argparse.Namespace) -> int:
    snapshot = analysis.build_snapshot(symbols=_parse_symbols(args.symbols))
    if not snapshot.symbols:
        print("未获取到任何标的，请检查 CACHE_DIR / 网络。", file=sys.stderr)
        return 1

    commentary = None
    llm = LLMClient()
    if not args.no_llm and llm.available:
        try:
            commentary = MarketAnalyst(llm).comment(report.facts_for_llm(snapshot))
        except LLMError as exc:
            print(f"[提示] 分析师点评生成失败，仅输出规则部分：{exc}", file=sys.stderr)

    text = report.generate(snapshot, commentary=commentary)
    path = report.save(text)
    if args.stdout:
        print(text)
    print(f"简报已保存：{path}")
    if args.push:
        card = alerts.build_brief_card(snapshot, commentary=commentary, report_path=path)
        if alerts.push_card(card):
            print("已推送到飞书。")
        else:
            print("推送失败，请检查 WEBHOOK_URL / WEBHOOK_SECRET 配置。", file=sys.stderr)
            return 2
    if snapshot.warnings:
        print("数据缺口：" + "；".join(snapshot.warnings))
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    analyst = MarketAnalyst()
    try:
        answer = analyst.ask(args.question)
    except LLMError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(answer)
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    alerts.run_watch(interval=args.interval, once=args.once)
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    print(backtest.run(horizon_hours=args.hours))
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
    snapshot = analysis.build_snapshot(symbols=_parse_symbols(args.symbols))
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
        "snapshot": _cmd_snapshot,
        "push-test": _cmd_push_test,
        "backtest": _cmd_backtest,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
