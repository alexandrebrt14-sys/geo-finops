"""Formatters do digest (markdown, whatsapp compacto, json indent)."""

from __future__ import annotations

import json


def format_markdown(d: dict) -> str:
    """Markdown detalhado, pronto para arquivar / enviar por email."""
    lines = [
        f"# FinOps Digest {d['label']}",
        "",
        f"**Janela**: {d['window']['start'][:10]} -> {d['window']['end'][:10]}",
        "",
        "## Resumo total",
        f"- LLM:        ${d['llm']['current']['cost_usd']:.2f} "
        f"({d['llm']['delta_pct']} vs sem. anterior)",
        f"- Fly.io:     ${d['cloud']['fly_usd']:.2f} (estimado constante)",
        f"- Vercel:     ${d['cloud']['vercel_usd_estimate']:.2f} (estimado)",
        f"- **Total:    ${d['total']['current_usd']:.2f}** ({d['total']['delta_pct']})",
        "",
        "## Top 3 hotspots LLM",
    ]
    for i, h in enumerate(d["llm"]["hotspots"], 1):
        lines.append(
            f"{i}. `{h['project']}` / `{h['model_id']}` / {h['task_type']} — "
            f"{h['calls']} calls / ${h['cost']:.2f}"
        )
    lines += ["", "## Por provider"]
    for p in d["llm"]["by_provider"]:
        lines.append(f"- {p['provider']}: {p['calls']} calls / ${p['cost']:.4f}")
    if d["cloud"]["github_actions_minutes_month"] is not None:
        lines += [
            "",
            f"## GitHub Actions: "
            f"{d['cloud']['github_actions_minutes_month']} min consumidos no mes",
        ]
    if d["alerts"]:
        lines += ["", "## ALERTAS"]
        for a in d["alerts"]:
            lines.append(f"- {a}")
    return "\n".join(lines)


def format_whatsapp(d: dict) -> str:
    """Compacto (<1500 chars) sem emoji nem markdown pesado.

    A Meta Graph API aceita ate ~4096 chars em mensagem text, mas
    mantemos curto para caber numa notificacao de tela de lock.
    """
    lines = [
        f"FinOps {d['label']}",
        f"Total: ${d['total']['current_usd']:.2f} ({d['total']['delta_pct']})",
        f"LLM ${d['llm']['current']['cost_usd']:.2f} | "
        f"Fly ${d['cloud']['fly_usd']:.2f} | "
        f"Vercel ${d['cloud']['vercel_usd_estimate']:.2f}",
        "",
        "Top hotspots:",
    ]
    for i, h in enumerate(d["llm"]["hotspots"], 1):
        proj_short = h["project"][:18]
        model_short = h["model_id"][:22]
        lines.append(f"{i}. {proj_short}/{model_short}")
        lines.append(f"   {h['calls']} calls / ${h['cost']:.2f}")
    if d["alerts"]:
        lines.append("")
        for a in d["alerts"]:
            lines.append("ALERTA: " + a)
    return "\n".join(lines)


def format_json(d: dict) -> str:
    """JSON indentado, util para pipes (``| jq``) ou logs."""
    return json.dumps(d, indent=2, ensure_ascii=False)
