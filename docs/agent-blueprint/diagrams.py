"""Diagrams for the AI trading agent integration report.

Every box is coloured by delivery status so the pictures and the tables use one
legend: Built / Reusable / Needs implementation / Human approval / Hazard.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from pathlib import Path

OUT = Path(__file__).parent / "img"
OUT.mkdir(exist_ok=True)

FONT = "Helvetica"
plt.rcParams.update({"font.family": [FONT, "DejaVu Sans"], "font.size": 9})

# status palette — fill, edge, text
S = {
    "built":  ("#E3F4EA", "#1E8E5A", "#10593A"),
    "reuse":  ("#E4EDFC", "#2F6FDE", "#1B4394"),
    "needs":  ("#FDF1DC", "#D08A0E", "#7A4F05"),
    "human":  ("#EFE8FB", "#7B4FD6", "#4A2A94"),
    "hazard": ("#FBE6E4", "#C8372D", "#8A1F18"),
    "neutral":("#F3F4F6", "#8A94A4", "#2B3240"),
    "dark":   ("#1F2937", "#1F2937", "#FFFFFF"),
}
INK = "#1F2937"
MUTED = "#5B6472"


def canvas(w, h):
    fig, ax = plt.subplots(figsize=(w, h), dpi=220)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100 * h / w)
    ax.axis("off")
    fig.subplots_adjust(0, 0, 1, 1)
    return fig, ax


def box(ax, x, y, w, h, title, sub=None, kind="neutral", size=8.6, subsize=7.0,
        lw=1.3, dashed=False, bold=True):
    fill, edge, text = S[kind]
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.1",
                       fc=fill, ec=edge, lw=lw, ls="--" if dashed else "-", zorder=2)
    ax.add_patch(p)
    if sub:
        ax.text(x + w / 2, y + h * 0.63, title, ha="center", va="center", color=text,
                fontsize=size, fontweight="bold" if bold else "normal", zorder=3, wrap=True)
        ax.text(x + w / 2, y + h * 0.29, sub, ha="center", va="center", color=MUTED if kind != "dark" else "#D1D5DB",
                fontsize=subsize, zorder=3, linespacing=1.25)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", color=text,
                fontsize=size, fontweight="bold" if bold else "normal", zorder=3, linespacing=1.25)
    return (x, y, w, h)


def band(ax, x, y, w, h, label, kind):
    fill, edge, text = S[kind]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.4",
                                fc=fill, ec=edge, lw=0.9, alpha=0.55, zorder=0))
    ax.text(x + 1.2, y + h - 1.4, label, ha="left", va="top", color=text,
            fontsize=7.4, fontweight="bold", zorder=5,
            bbox=dict(fc=fill, ec="none", pad=0.8))


def arrow(ax, a, b, label=None, color=INK, dashed=False, lw=1.2, rad=0.0, lpos=0.5, loff=(0, 1.2), ls=None):
    style = "-|>"
    ar = FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=9, color=color, lw=lw,
                         ls=ls or ("--" if dashed else "-"),
                         connectionstyle=f"arc3,rad={rad}", zorder=1, shrinkA=1, shrinkB=1)
    ax.add_patch(ar)
    if label:
        mx = a[0] + (b[0] - a[0]) * lpos + loff[0]
        my = a[1] + (b[1] - a[1]) * lpos + loff[1]
        ax.text(mx, my, label, ha="center", va="center", fontsize=6.6, color=color,
                bbox=dict(fc="white", ec="none", pad=0.6), zorder=4)


def legend(ax, x, y, items=("built", "reuse", "needs", "human", "hazard")):
    names = {"built": "Already Built", "reuse": "Reusable (built, not wired)",
             "needs": "Needs Implementation", "human": "Human Approval", "hazard": "Hazard / fix first"}
    cx = x
    for k in items:
        fill, edge, _ = S[k]
        ax.add_patch(FancyBboxPatch((cx, y), 2.2, 1.8, boxstyle="round,pad=0,rounding_size=0.5",
                                    fc=fill, ec=edge, lw=1.1))
        ax.text(cx + 2.9, y + 0.9, names[k], va="center", fontsize=7.0, color=INK)
        cx += 2.9 + len(names[k]) * 0.52 + 3.2


def save(fig, name):
    fig.savefig(OUT / name, dpi=220, facecolor="white")
    plt.close(fig)
    print("wrote", name)


# ----------------------------------------------------------------------------- 1
def header(ax, H, title, sub, items):
    ax.text(1, H - 1.6, title, fontsize=11.5, fontweight="bold", color=INK, va="top")
    ax.text(1, H - 5.0, sub, fontsize=7.8, color=MUTED, va="top")
    legend(ax, 1, H - 9.6, items)


COLS = [4.0, 23.2, 42.4, 61.6, 80.8]   # five columns, 17.2 wide, with a left gutter
CW = 17.2


def as_built():
    fig, ax = canvas(10, 6.4)
    H = 64
    header(ax, H, "FTMO_MT5 today — what exists and how it is wired",
           "Inspected 30 Sep 2026 from the source tree. Colour = delivery status.",
           ("built", "reuse", "needs", "human", "hazard"))

    box(ax, 4, 49.2, 94, 4.2, "Sir / team — choose stocks · read results · place trades by hand · clear halts",
        None, "human", size=8)

    band(ax, 1, 31.5, 98, 14.2, "INTERFACES", "neutral")
    r1 = 32.5
    box(ax, COLS[0], r1, CW, 9.5, "Terminal :8600", "broker_hub.py\nmanual orders", "built")
    box(ax, COLS[1], r1, CW, 9.5, "Backtest Lab", "/lab · live runs", "built")
    box(ax, COLS[2], r1, CW, 9.5, "Systematic panel", ":8601 · stocks, P&L,\ntrades, research", "built")
    box(ax, COLS[3], r1, CW, 9.5, "Claude Code + MCP", "ftmo-lab server\n6 tools · 3 resources", "built")
    box(ax, COLS[4], r1, CW, 9.5, "Claude runner /agents", "Agent SDK · shell + edits\nin the order process", "hazard")

    band(ax, 1, 9.5, 98, 20.5, "DETERMINISTIC CORE", "neutral")
    r2, r3 = 19.8, 10.6
    box(ax, COLS[0], r2, CW, 7.6, "Live loop main.py", "one hardcoded EMA rule\nno bridge from the Lab", "needs")
    box(ax, COLS[1], r2, CW, 7.6, "Research Lab", "16 strategies · verdict", "built")
    box(ax, COLS[2], r2, CW, 7.6, "Systematic pipeline", "screen→validate→rank", "built")
    box(ax, COLS[3], r2, CW, 7.6, "RiskAgent", "NORMAL→…→HALT", "built")
    box(ax, COLS[4], r2, CW, 7.6, "ML layer", "imported by nothing", "reuse")
    box(ax, COLS[0], r3, CW, 7.6, "Supervisor", "heartbeats · reconcile", "built")
    box(ax, COLS[1], r3, CW, 7.6, "Health checks", "3 hardcoded healthy", "needs")
    box(ax, COLS[2], r3, CW, 7.6, "PaperBroker", "in-process ledger", "built")
    box(ax, COLS[3], r3, CW, 7.6, "EventStore", "append-only · 8 events", "built")
    box(ax, COLS[4], r3, CW, 7.6, "Meta-label gate", "fail-open: no score = pass", "reuse")

    band(ax, 1, 0.6, 98, 7.6, "BROKERS & DATA", "neutral")
    for (t, k), x in zip([("tastytrade sandbox", "built"), ("MT5 FTMO-Demo (VM)", "built"),
                          ("Webull sandbox", "neutral"), ("Yahoo daily bars", "built"),
                          ("FMP market data", "reuse")], COLS):
        box(ax, x, 1.2, CW, 4.2, t, None, k, size=7.6)

    # short, honest flows only
    arrow(ax, (COLS[1] + CW / 2, r1), (COLS[1] + CW / 2, r2 + 7.6))            # lab page -> lab
    arrow(ax, (COLS[2] + CW / 2, r1), (COLS[2] + CW / 2, r2 + 7.6))            # panel -> pipeline
    arrow(ax, (COLS[3] + 3, r1), (COLS[1] + CW - 2, r2 + 7.6), color=MUTED, rad=0.05)  # MCP -> lab
    arrow(ax, (COLS[2] + CW, r2 + 3.8), (COLS[3], r2 + 3.8))                     # pipeline -> risk
    arrow(ax, (COLS[2] + CW / 2, r2), (COLS[2] + CW / 2, r3 + 7.6))            # pipeline -> paper
    arrow(ax, (COLS[3] + CW / 2, r2), (COLS[3] + CW / 2, r3 + 7.6))            # risk -> events
    arrow(ax, (COLS[1], r2 + 3.8), (COLS[0] + CW, r2 + 3.8), color=S["needs"][1], dashed=True)
    # manual orders: down the left gutter
    ax.plot([2.2, 2.2], [r1 + 4.7, 3.3], color="#8A1F18", lw=1.2)
    arrow(ax, (2.2, r1 + 4.7), (COLS[0], r1 + 4.7), color="#8A1F18")
    arrow(ax, (2.2, 3.3), (COLS[0], 3.3), color="#8A1F18")
    ax.text(0.5, 20, "manual orders", rotation=90, fontsize=6.6, color="#8A1F18", va="center")
    # hazard: the runner can reach the terminal's order route in its own process
    arrow(ax, (COLS[4] + CW / 2, r1 + 9.5), (COLS[0] + CW / 2, r1 + 9.5),
          color=S["hazard"][1], dashed=True, rad=0.085)
    ax.text(50, 47.35, "shell → curl POST /api/{broker}/order — same process as the order routes",
            ha="center", va="center", fontsize=6.8, color=S["hazard"][1],
            bbox=dict(fc="white", ec="none", pad=0.4), zorder=6)
    save(fig, "01_as_built.png")


# ----------------------------------------------------------------------------- 2
def target():
    fig, ax = canvas(10, 7.2)
    H = 72
    header(ax, H, "Target — an independent AI trading agent, in layers",
           "The LLM proposes; deterministic code disposes. Tools read or propose — none can place an order.",
           ("built", "reuse", "needs", "human"))

    band(ax, 1, 51.5, 98, 9.5, "L0  HUMAN APPROVAL GATES", "human")
    for i, t in enumerate(["Promote a rule", "Approve cycle plan", "Clear a HALT", "Change risk limits", "Any LIVE mode"]):
        box(ax, COLS[i], 52.3, CW, 4.8, t, None, "human", size=7.8)

    band(ax, 1, 36.5, 98, 13, "L1  AI TRADING AGENT  ·  Claude Agent SDK · own process · allowed_tools = MCP only · no shell · $ cap", "needs")
    roles = [("Analyst", "market brief ·\nevent calendar"), ("Researcher", "run & read\nresearch"),
             ("Planner", "dry-run cycle ·\nexplain each trade"), ("Monitor", "risk state ·\nanomalies"),
             ("Reporter", "P&L · incidents ·\nmanager memo")]
    for i, (t, sub) in enumerate(roles):
        box(ax, COLS[i], 37.3, CW, 8.2, t, sub, "needs")

    band(ax, 1, 21.5, 98, 13, "L2  MCP TOOLS", "neutral")
    tools = [("ftmo-lab", "6 tools · 3 resources", "built"), ("ftmo-research", "run · rank · explain", "needs"),
             ("ftmo-paper", "read · dry-run plan", "needs"), ("market-data", "FMP quotes · calendar", "reuse"),
             ("ftmo-reports", "memo · incident · P&L", "needs")]
    for i, (t, sub, k) in enumerate(tools):
        box(ax, COLS[i], 22.3, CW, 8.2, t, sub, k)

    band(ax, 1, 6.5, 98, 13, "L3  CORE", "built")
    core = [("verdict.validate", "the grade"), ("systematic cycle", "exits, then entries"),
            ("RiskAgent", "FTMO limits · sizing"), ("PaperBroker", "fills · ledger"),
            ("EventStore", "append-only audit")]
    for i, (t, sub) in enumerate(core):
        box(ax, COLS[i], 7.3, CW, 8.2, t, sub, "built")

    box(ax, 1, 0.6, 98, 4.4, "L4  BROKER SANDBOX — future execution adapter behind the cycle (Phase 3+). No agent tool reaches it.",
        None, "neutral", size=7.6, dashed=True)

    mid = [c + CW / 2 for c in COLS]
    for x in mid:                                   # every role uses the tool layer
        arrow(ax, (x, 37.3), (x, 30.5), color=MUTED)
    # only the connections that are true
    arrow(ax, (mid[0], 22.3), (mid[0], 15.5), "calls", color=MUTED, loff=(3.2, 1.6))
    arrow(ax, (mid[1], 22.3), (mid[1], 15.5), "runs", color=MUTED, loff=(3.0, 1.6))
    arrow(ax, (mid[2], 22.3), (mid[2], 15.5), "dry-run", color=MUTED, loff=(3.8, 1.6))
    arrow(ax, (mid[4], 15.5), (mid[4], 22.3), "reads", color=MUTED, loff=(3.2, 1.6))
    ax.text(mid[3], 20.5, "external data · no core access", ha="center", va="center", fontsize=6.6,
            color=MUTED, bbox=dict(fc="white", ec="none", pad=0.4), zorder=5)
    arrow(ax, (mid[3], 7.3), (mid[3], 5.0), color=MUTED, dashed=True)
    save(fig, "02_target.png")


# ----------------------------------------------------------------------------- 3
def loop():
    fig, ax = canvas(10, 6.6)
    H = 66
    header(ax, H, "The agent's operating rhythm (New York time)",
           "FTMO US shares trade 09:35–16:00 ET only, so every agent decision sits outside the session.",
           ("built", "needs", "human"))
    cols = [("WEEKLY · Sunday", 1), ("PRE-MARKET · 08:30", 26), ("POST-CLOSE · 16:15", 51), ("ALWAYS-ON", 76)]
    for t, x in cols:
        ax.text(x + 11.5, 50.5, t, ha="center", fontsize=8.4, fontweight="bold", color=INK)
    w = 23
    spec = {
        1:  [("Refresh universe", "FTMO symbol list", "needs"), ("Run research", "screen · validate · rank", "built"),
             ("Research memo", "what graded, and why not", "needs"), ("Promote rules?", "grade ≥ MODERATE and\ntradable on FTMO", "human")],
        26: [("Mode + data check", "paper? bars fresh?", "needs"), ("Event scan", "earnings · macro · gaps", "needs"),
             ("Flag positions", "gap risk vs daily-loss room", "needs"), ("Morning brief", "read by the team", "needs")],
        51: [("Dry-run the cycle", "planned exits + entries", "needs"), ("Deterministic preview", "RiskAgent · sizing · caps", "built"),
             ("Approve plan", "Phase 2: always\nPhase 3: out-of-policy only", "human"), ("Execute cycle", "RiskAgent re-checks → fill → log", "built")],
        76: [("Watch risk state", "EventStore tail", "needs"), ("HALT detected", "RiskAgent flattened", "built"),
             ("Incident report", "cause · P&L · timeline", "needs"), ("Clear HALT?", "a named person only —\nagent has no tool for it", "human")],
    }
    ys = [41, 31, 21, 10.5]
    for x, items in spec.items():
        for (t, sub, k), y in zip(items, ys):
            box(ax, x, y, w, 7.5 if y != 10.5 else 8, t, sub, k)
        for y1, y2 in [(41, 38.5), (31, 28.5), (21, 18.5)]:
            arrow(ax, (x + w / 2, y1), (x + w / 2, y2))
    box(ax, 1, 1, 98, 6.2, "End of day: P&L report — realised · unrealised · win rate · risk state · what the agent proposed vs what executed",
        None, "needs", size=7.8)
    for x in [12.5, 37.5, 62.5, 87.5]:
        arrow(ax, (x, 10.5), (x, 7.2), color=MUTED)
    save(fig, "03_loop.png")


# ----------------------------------------------------------------------------- 4
def proposal():
    fig, ax = canvas(10, 4.5)
    H = 45
    header(ax, H, "One trade, end to end — where the agent stops and the code takes over",
           "A real case from this codebase: VZ graded MODERATE in research, but FTMO does not list VZ.",
           ("built", "needs", "human", "hazard"))
    y = 20
    steps = [
        ("Agent proposes", "LONG VZ · MEAN_REV\n'fresh signal today'", "needs"),
        ("Tradable?", "on FTMO list?\nprice & size OK?", "needs"),
        ("Grade gate", "rule ≥ MODERATE\nin ranking.json", "built"),
        ("Human gate", "approve the plan\n(Phase 2)", "human"),
        ("RiskAgent", "re-checked at\nexecution time", "built"),
        ("PaperBroker", "fill at close ±\nslippage + fee", "built"),
        ("EventStore", "signal · risk ·\nfill logged", "built"),
    ]
    w, gap = 12.4, 1.75
    xs = [1 + i * (w + gap) for i in range(len(steps))]
    for (t, sub, k), x in zip(steps, xs):
        box(ax, x, y, w, 11, t, sub, k, size=8.2, subsize=6.6)
    for i in range(len(steps) - 1):
        arrow(ax, (xs[i] + w, y + 5.5), (xs[i + 1], y + 5.5))
    ax.text(1, 33.8, "BEFORE EXECUTION — the agent proposes, gates filter", fontsize=7.6, color=S["needs"][2], fontweight="bold")
    ax.text(xs[4], 33.8, "EXECUTION — deterministic code only", fontsize=7.6, color=S["built"][2], fontweight="bold")
    ax.plot([xs[3] + w + gap / 2] * 2, [1, 33], color="#C7CCD4", lw=1.2, ls=":")
    rej = [(1, "VZ not on FTMO list\n→ drop, explain why"), (2, "FAILED / FRAGILE\n→ never trades"),
           (3, "rejected\n→ no order"), (4, "RISK_OFF → refused\nHALT → flatten all")]
    for idx, txt in rej:
        cx = xs[idx] + w / 2
        arrow(ax, (cx, y), (cx, 10.2), color=S["hazard"][1], dashed=True)
        box(ax, xs[idx], 2, w, 8, txt, None, "hazard", size=6.8, bold=False)
    save(fig, "04_proposal.png")


# ----------------------------------------------------------------------------- 5
def roadmap():
    fig, ax = canvas(10, 4.0)
    H = 40
    ax.text(1, H - 1.6, "Delivery roadmap — each phase must pass its exit test before the next starts",
            fontsize=11.5, fontweight="bold", color=INK, va="top")
    phases = [
        ("Phase 0", "Make it safe", "lock /agents · fix health checks", "hazard", 0, 8),
        ("Phase 1", "Read-only analyst", "research / paper / report MCP tools · briefs", "needs", 8, 22),
        ("Phase 2", "Proposer + human gate", "dry-run plans · approve in panel", "human", 22, 42),
        ("Phase 3", "Autonomous paper", "policy envelope · 4-week forward test", "needs", 42, 72),
        ("Phase 4", "Live — out of scope", "separate sign-off · capital limits", "neutral", 72, 99),
    ]
    X0, K = 29, 0.70
    rowh, y0 = 6.0, 26.5
    for i, (ph, t, sub, k, a, b) in enumerate(phases):
        yy = y0 - i * rowh
        ax.text(1, yy + 2.1, ph, fontsize=8.4, fontweight="bold", color=INK, va="center")
        ax.text(9.2, yy + 2.1, t, fontsize=7.9, color=MUTED, va="center")
        fill, edge, text = S[k]
        x0, ww = X0 + a * K, (b - a) * K
        ax.add_patch(FancyBboxPatch((x0, yy), ww, 4.2, boxstyle="round,pad=0,rounding_size=0.8",
                                    fc=fill, ec=edge, lw=1.2, ls="--" if k == "neutral" else "-"))
        inside = len(sub) * 0.44 < ww - 1.5 or x0 + ww + len(sub) * 0.44 > 97
        if inside:
            ax.text(x0 + ww / 2, yy + 2.1, sub, ha="center", va="center", fontsize=6.9, color=text)
        else:
            ax.text(x0 + ww + 1.0, yy + 2.1, sub, ha="left", va="center", fontsize=6.9, color=text)
    for wk, a in [("wk 0", 0), ("wk 2", 8), ("wk 5", 22), ("wk 9", 42), ("wk 15+", 72)]:
        x = X0 + a * K
        ax.plot([x, x], [1.8, 31.5], color="#E5E7EB", lw=0.8, zorder=0)
        ax.text(x, 32.3, wk, fontsize=6.8, color=MUTED, ha="center")
    ax.text(1, 0.4, "Indicative: one engineer, part-time, working with Claude Code. Phase 3's length is set by the forward test, not by build effort.",
            fontsize=6.6, color=MUTED)
    save(fig, "05_roadmap.png")


if __name__ == "__main__":
    as_built(); target(); loop(); proposal(); roadmap()
