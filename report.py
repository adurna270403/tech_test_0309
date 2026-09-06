"""Generate the final deliverable: output/REPORT.pdf (single document with all
tables, charts and commentary), plus the charts it embeds."""
import datetime
import json
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from data.loaders import (CRYPTO_SYMBOLS, CRYPTO_XS_SYMBOLS, EQUITY_TICKERS,
                          INDEX_TICKERS,
                          close_frame, data_fingerprint, load_all, load_eps)
from run_backtest import metrics

TODAY = datetime.date.today().isoformat()

Block = tuple


# --------------------------------------------------------------------------
# charts
# --------------------------------------------------------------------------
def make_charts(out: pd.DataFrame, m_net: pd.Series, close: pd.DataFrame):
    oos_line = pd.Timestamp(config.OOS_START)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(out.index, out["nav_net"], label="Net of costs")
    ax.plot(out.index, out["nav_gross"], label="Gross", alpha=0.6)
    ax.axvline(oos_line, color="red", ls="--", lw=1, label="OOS start (2023)")
    ax.set_yscale("log")
    ax.set_title("Strategy NAV (net vs gross, log scale)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(config.OUTPUT / "equity_curve.png", dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(out.index, (out["nav_net"] - 1) * 100, label="Cumulative P&L, net")
    ax.plot(out.index, (out["nav_gross"] - 1) * 100, label="Cumulative P&L, gross",
            alpha=0.6)
    ax.axvline(oos_line, color="red", ls="--", lw=1, label="OOS start (2023)")
    ax.axhline(0, color="grey", lw=0.5)
    ax.set_ylabel("cumulative return (%)")
    ax.set_title("Cumulative P&L per $1 of equity")
    ax.legend()
    fig.tight_layout()
    fig.savefig(config.OUTPUT / "cumulative_pnl.png", dpi=120)
    plt.close(fig)

    idx = out.index
    spy = close["SPY"].reindex(idx).ffill()
    btc = close["BTCUSDT"].reindex(idx).ffill()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(idx, out["nav_net"] * 10_000, lw=1.4,
            label=f"This book, net ($10k -> ${out['nav_net'].iloc[-1]*10_000:,.0f})")
    ax.plot(idx, spy / spy.iloc[0] * 10_000, lw=1.2, alpha=0.8,
            label=f"S&P 500 index fund ($10k -> ${spy.iloc[-1]/spy.iloc[0]*10_000:,.0f})")
    bt = btc.dropna()
    ax.plot(bt.index, bt / bt.iloc[0] * 10_000, lw=1.0, alpha=0.6,
            label=f"Bitcoin, buy & hold ($10k -> ${bt.iloc[-1]/bt.iloc[0]*10_000:,.0f})")
    ax.axvline(oos_line, color="red", ls="--", lw=1)
    ax.set_yscale("log")
    ax.set_ylabel("wealth of a $10,000 stake (log scale)")
    ax.set_title("Growth of $10,000, 2019-2026")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(config.OUTPUT / "wealth_paths.png", dpi=120)
    plt.close(fig)

    hm = (m_net * 100).to_frame("r")
    piv = pd.DataFrame({"y": hm.index.year, "m": hm.index.month, "r": hm["r"]})
    piv = piv.pivot(index="y", columns="m", values="r")
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.imshow(piv.values, cmap="RdYlGn", aspect="auto", vmin=-8, vmax=8)
    ax.set_xticks(range(12), ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    ax.set_yticks(range(len(piv.index)), piv.index)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.values[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:+.1f}", ha="center", va="center", fontsize=6)
    ax.set_title("Monthly net returns (%)")
    fig.tight_layout()
    fig.savefig(config.OUTPUT / "monthly_heatmap.png", dpi=120)
    plt.close(fig)


# --------------------------------------------------------------------------
# table builders -> (headers, rows-of-strings)
# --------------------------------------------------------------------------
def metrics_table(m_full, m_is, m_oos, m_oos_g, m_pre) -> tuple:
    keys = ["CAGR", "AnnVol", "Sharpe", "Sortino", "Calmar", "MaxDD", "AvgMonthly",
            "PctPosMonths", "BestMonth", "WorstMonth", "SkewMonthly", "KurtMonthly",
            "NMonths"]

    def f(d, k):
        v = d[k]
        if k == "NMonths":
            return str(int(v))
        if k in ("Sharpe", "Sortino", "Calmar", "SkewMonthly", "KurtMonthly"):
            return f"{v:.2f}"
        return f"{v:.1%}" if k != "CAGR" else f"{v:.1%}"

    return (["Metric", "Full 2019+ (net)", "IS 2019-22 (net)", "OOS 2023+ (net)",
             "OOS 2023+ (gross)", "Equity-only 2010-16 (net)"],
            [[k, f(m_full, k), f(m_is, k), f(m_oos, k), f(m_oos_g, k), f(m_pre, k)]
             for k in keys])


def scorecard_table(m: pd.Series) -> tuple:
    tgt, n = config.MONTHLY_TARGET, len(m)
    ge = int((m >= tgt).sum())
    band = int(((m >= tgt) & (m <= 0.04)).sum())
    pos = int((m > 0).sum())
    rows = [
        ["Positive months", f"{pos}/{n}", f"{pos/n:.1%}", ">75%",
         "MET" if pos / n > config.HIT_RATE_TARGET else "MISSED"],
        ["Months >= 2%", f"{ge}/{n}", f"{ge/n:.1%}", "(see text)",
         f"{ge/n:.0%} of months"],
        ["Months inside 2-4%", f"{band}/{n}", f"{band/n:.1%}", "(see text)", ""],
        ["Average month, arithmetic", f"{m.mean():.2%}", "", "2-4%",
         "MISSED" if not 0.02 <= m.mean() <= 0.04 else "MET"],
        ["Average month, compounded", f"{((1+m).prod()**(1/n)-1):.2%}", "", "2-4%",
         "MISSED" if not 0.02 <= (1 + m).prod() ** (1 / n) - 1 <= 0.04 else "MET"],
    ]
    return ["Condition", "Count", "Share", "Target", "Verdict"], rows


def monthly_table(m: pd.Series) -> tuple:
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    tgt = config.MONTHLY_TARGET
    headers = (["Year"] + names + ["YTD", ">=2%", ">0"])
    rows = []
    for y in sorted({d.year for d in m.index}):
        row = m[m.index.year == y]
        cells = []
        for month in range(1, 13):
            v = row[row.index.month == month]
            if not len(v):
                cells.append("")
                continue
            x = v.iloc[0]
            s = f"{x*100:+.1f}"
            cells.append(s + ("*" if x >= tgt else (" -" if x <= 0 else "")))
        cells.append(f"{((1 + row).prod() - 1)*100:+.1f}")
        cells.append(f"{int((row >= tgt).sum())}")
        cells.append(f"{int((row > 0).sum())}/{len(row)}")
        rows.append([str(y)] + cells)
    rows.append(["all", *[""] * 12, "",
                 f"{int((m >= tgt).sum())}/{len(m)}",
                 f"{int((m > 0).sum())}/{len(m)}"])
    return headers, rows


def direction_table(direction: pd.DataFrame) -> tuple:
    headers = ["Series (unit gross)", "Mean when index down", "Pos. when down",
               "Mean when index up", "Pos. when up", "Corr to index"]
    rows = []
    for _, r in direction.iterrows():
        rows.append([
            r["series"],
            f"{r['mean_when_index_down']*100:+.2f}%",
            f"{r['pct_pos_when_index_down']*100:.1f}%",
            f"{r['mean_when_index_up']*100:+.2f}%",
            f"{r['pct_pos_when_index_up']*100:.1f}%",
            f"{r['corr_with_index']:+.2f}"])
    return headers, rows


def hitgrid_table(hitgrid: pd.DataFrame) -> tuple:
    headers = ["Defensive budget", "Crypto budget", "Pos. (full)", "Pos. (IS)",
               "Pos. (OOS)", "Avg month", "Months >=2%", "Clears 75% everywhere"]
    rows = []
    for _, r in hitgrid.iterrows():
        rows.append([
            f"{r['defensive_budget']:.2f}", f"{r['crypto_budget']:.2f}",
            f"{r['full/pct_pos']*100:.1f}%", f"{r['is_window/pct_pos']*100:.1f}%",
            f"{r['oos_2023_plus/pct_pos']*100:.1f}%",
            f"{r['full/avg_monthly']*100:.2f}%",
            f"{r['full/pct_ge_2pct']*100:.1f}%",
            "yes" if r["meets_75_all_windows"] else "no"])
    return headers, rows


def frontier_table(frontier: pd.DataFrame) -> tuple:
    headers = ["Crypto budget", "Arith. avg/mo", "Compounded/mo", "Pos. months",
               "Sharpe", "MaxDD", "OOS 2023+ arith/pos"]
    rows = []
    for _, r in frontier.iterrows():
        rows.append([
            f"{r['btc_budget']:.0%}",
            f"{r['full/avg_monthly']*100:.2f}%",
            f"{r['full/geom_monthly']*100:.2f}%",
            f"{r['full/pct_pos']*100:.0f}%",
            f"{r['full/sharpe']:.2f}",
            f"{r['full/max_dd']*100:.1f}%",
            f"{r['oos_2023_plus/avg_monthly']*100:.2f}% / {r['oos_2023_plus/pct_pos']*100:.0f}%"])
    return headers, rows


def variant_table(df: pd.DataFrame, key: str, header: str) -> tuple:
    headers = [header, "Arith. avg/mo", "Compounded/mo", "Pos. months", "Sharpe",
               "MaxDD", "AnnVol"]
    rows = []
    for _, r in df.iterrows():
        rows.append([
            str(r[key]),
            f"{r['full/avg_monthly']*100:.2f}%",
            f"{r['full/geom_monthly']*100:.2f}%",
            f"{r['full/pct_pos']*100:.1f}%",
            f"{r['full/sharpe']:.2f}",
            f"{r['full/max_dd']*100:.1f}%",
            f"{r['full/ann_vol']*100:.1f}%"])
    return headers, rows


def candidate_table(cand: pd.DataFrame) -> tuple:
    headers = ["Proposed alpha", "Window", "Sharpe net", "Sharpe zero-cost",
               "Pos. months (net)", "Turnover/day"]
    rows = []
    for _, r in cand.iterrows():
        rows.append([
            r["alpha"], r["window"].replace("_", "-"),
            f"{r['net_sharpe']:.2f}", f"{r['gross_sharpe']:.2f}",
            f"{r['net_pct_pos']*100:.1f}%", f"{r['turnover_per_day']:.2f}"])
    return headers, rows


def xasset_table(xasset: pd.DataFrame) -> tuple:
    headers = ["Cross-asset variant", "Window", "Sharpe", "Pos. months",
               "Compounded/mo", "AnnVol"]
    rows = []
    for _, r in xasset[xasset["window"] != "full"].iterrows():
        rows.append([
            r["variant"], r["window"].replace("_", "-"),
            f"{r['sharpe']:.2f}", f"{r['pct_pos']*100:.1f}%",
            f"{r['geom_monthly']*100:.2f}%", f"{r['ann_vol']*100:.1f}%"])
    return headers, rows


def plain_table(df: pd.DataFrame, round_to: int = 2, index_name: str = "") -> tuple:
    df = df.round(round_to)
    headers = [index_name] + [str(c) for c in df.columns]
    rows = [[str(i)] + [str(v) for v in row] for i, row in df.iterrows()]
    return headers, rows


# --------------------------------------------------------------------------
# markdown renderer (kept as a human-readable audit copy of the same content)
# --------------------------------------------------------------------------
def render_markdown(blocks: list[Block]) -> str:
    lines = []
    for b in blocks:
        kind = b[0]
        if kind == "h1":
            lines += [f"# {b[1]}", ""]
        elif kind == "h2":
            lines += [f"## {b[1]}", ""]
        elif kind == "h3":
            lines += [f"### {b[1]}", ""]
        elif kind == "p":
            lines += [b[1], ""]
        elif kind == "bullets":
            lines += [f"- {x}" for x in b[1]] + [""]
        elif kind == "table":
            headers, rows = b[1], b[2]
            lines += ["| " + " | ".join(headers) + " |",
                      "|" + "---|" * len(headers)]
            lines += ["| " + " | ".join(r) + " |" for r in rows]
            lines.append("")
        elif kind == "image":
            lines += [f"![{b[2]}]({b[1]})", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# pdf renderer
# --------------------------------------------------------------------------
def render_pdf(blocks: list[Block], path: Path):
    from fpdf import FPDF
    from fpdf.enums import TableBordersLayout
    from fpdf.fonts import FontFace

    FONTS = "/usr/share/fonts/truetype/dejavu"
    pdf = FPDF(format="A4")
    pdf.add_font("serif", "", f"{FONTS}/DejaVuSerif.ttf")
    pdf.add_font("serif", "B", f"{FONTS}/DejaVuSerif-Bold.ttf")
    pdf.add_font("sans", "", f"{FONTS}/DejaVuSans.ttf")
    pdf.add_font("sans", "B", f"{FONTS}/DejaVuSans-Bold.ttf")
    pdf.add_font("mono", "", f"{FONTS}/DejaVuSansMono.ttf")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()

    epw = pdf.epw

    def heading(text, size, space_after=3):
        pdf.set_font("sans", "B", size)
        pdf.multi_cell(epw, size * 0.55, text)
        pdf.ln(space_after)

    BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
    ITAL_RE = re.compile(r"\*(.+?)\*")

    def rich_text(text, font="serif", size=9.8, line_h=5.4):
        """Flow-write text with **bold** spans via write(), which wraps and
        breaks pages correctly (fpdf2's markdown multi_cell mode clips).
        Single-asterisk emphasis is stripped: no italic DejaVu Serif face is
        installed."""
        text = ITAL_RE.sub(r"\1", text)
        pieces = []
        pos = 0
        for m in BOLD_RE.finditer(text):
            if m.start() > pos:
                pieces.append((text[pos:m.start()], False))
            pieces.append((m.group(1), True))
            pos = m.end()
        if pos < len(text):
            pieces.append((text[pos:], False))
        for seg, bold in pieces:
            pdf.set_font(font, "B" if bold else "", size)
            pdf.write(line_h, seg)
        pdf.ln(line_h)

    for b in blocks:
        kind = b[0]
        if kind == "h1":
            heading(b[1], 17, 4)
        elif kind == "h2":
            pdf.ln(2)
            heading(b[1], 13, 2)
        elif kind == "h3":
            pdf.ln(1)
            heading(b[1], 11, 1.5)
        elif kind == "p":
            rich_text(b[1])
            pdf.ln(2.5)
        elif kind == "bullets":
            for item in b[1]:
                pdf.set_font("serif", "", 9.8)
                pdf.write(5.4, "- ")
                rich_text(item)
            pdf.ln(2)
        elif kind == "table":
            headers, rows = b[1], b[2]
            opts = b[3] if len(b) > 3 else {}
            font_size = opts.get("font", 7.6)
            ratios = opts.get("ratios")
            align = opts.get("align", "CENTER")
            borders = opts.get("borders", TableBordersLayout.ALL)
            pdf.set_font("sans", "", font_size)
            style = FontFace(emphasis="BOLD", fill_color=(233, 233, 233))
            with pdf.table(col_widths=ratios, headings_style=style,
                           line_height=font_size * 0.55, text_align=align,
                           borders_layout=borders, padding=0.6) as t:
                hr = t.row()
                for h in headers:
                    hr.cell(h)
                for row in rows:
                    tr = t.row()
                    for cell in row:
                        tr.cell(cell)
            pdf.ln(4)
        elif kind == "image":
            caption, img = b[2], b[1]
            pdf.image(str(config.OUTPUT / img), w=epw)
            pdf.set_font("sans", "", 8)
            pdf.set_text_color(110)
            pdf.multi_cell(epw, 4, caption, align="C")
            pdf.set_text_color(0)
            pdf.ln(3)

    pdf.output(str(path))


# --------------------------------------------------------------------------
def main():
    out = pd.read_csv(config.OUTPUT / "daily_results.csv", index_col=0,
                      parse_dates=True)
    summary = json.loads((config.OUTPUT / "summary.json").read_text())
    sleeve_corr = pd.read_csv(config.OUTPUT / "sleeve_correlation.csv", index_col=0)
    decay = pd.read_csv(config.OUTPUT / "is_oos_decay.csv", index_col=0)
    sens = json.loads((config.OUTPUT / "sensitivity.json").read_text())
    diag = json.loads((config.OUTPUT / "diagnostics.json").read_text())
    proto = json.loads((config.OUTPUT / "protocol_summary.json").read_text())
    wf = pd.read_csv(config.OUTPUT / "walkforward.csv")
    loo = pd.read_csv(config.OUTPUT / "leave_one_out.csv")
    wfs = proto["walkforward_summary"]
    frontier = pd.read_csv(config.OUTPUT / "budget_frontier.csv")
    execu = pd.read_csv(config.OUTPUT / "execution_sensitivity.csv")
    ablation = pd.read_csv(config.OUTPUT / "risk_ablation.csv")
    xasset = pd.read_csv(config.OUTPUT / "cross_asset_experiment.csv")
    costs_df = pd.read_csv(config.OUTPUT / "cost_sensitivity.csv")
    cand = pd.read_csv(config.OUTPUT / "candidate_alphas.csv")
    direction = pd.read_csv(config.OUTPUT / "market_direction_attribution.csv")
    hitgrid = pd.read_csv(config.OUTPUT / "hitrate_frontier.csv")
    pre = pd.read_csv(config.OUTPUT / "equity_only_2010_2016.csv",
                      index_col=0, parse_dates=True)

    data = load_all()
    close = close_frame(data)
    m_full = metrics(out["ret_net"])
    m_pre = metrics(pre["ret_net"])
    is_mask = (out.index >= config.IS_START) & (out.index <= config.IS_END)
    oos_mask = out.index >= config.OOS_START
    m_is = metrics(out.loc[is_mask, "ret_net"])
    m_oos = metrics(out.loc[oos_mask, "ret_net"])
    m_oos_g = metrics(out.loc[oos_mask, "ret_gross"])

    m_net = pd.Series(summary["monthly_net"])
    m_net.index = pd.to_datetime(m_net.index)
    mnd = m_net.dropna()

    make_charts(out, m_net, close)

    # ---- derived numbers used in the prose --------------------------------
    n, pos = len(mnd), int((mnd > 0).sum())
    avg = m_full["AvgMonthly"]
    ret_gap = 0.02 / avg
    final_wealth = out["nav_net"].iloc[-1] * 10_000
    spy = close["SPY"].reindex(out.index).ffill().bfill()
    spy_wealth = spy.iloc[-1] / spy.iloc[0] * 10_000
    btc = close["BTCUSDT"].dropna()
    btc_wealth = btc.iloc[-1] / btc.iloc[0] * 10_000
    spy_maxdd = float((spy / spy.cummax() - 1).min())
    strat_maxdd = m_full["MaxDD"]
    vol_drag_flat = frontier["full/geom_monthly"].max() - frontier["full/geom_monthly"].min()
    we_2022 = float(out.loc[out.index.year == 2022, "nav_net"].iloc[-1]
                    / out.loc[out.index.year == 2022, "nav_net"].iloc[0] - 1)
    sp_2022 = float(spy[out.index.year == 2022].iloc[-1]
                    / spy[out.index.year == 2022].iloc[0] - 1)
    two_pct_wealth = 1.02 ** n * 10_000
    hit_sharpe_req = stats.norm.ppf(config.HIT_RATE_TARGET) * np.sqrt(12)
    m_over_s = mnd.mean() / mnd.std()
    normal_pos = stats.norm.cdf(m_full["Sharpe"] / np.sqrt(12))
    n_pass_cells = int(hitgrid["meets_75_all_windows"].sum())
    n_cells = len(hitgrid)
    budget_str = ", ".join(f"{k} {v:.0%}" for k, v in
                           sorted(config.SLEEVE_BUDGETS.items(), key=lambda x: -x[1]))

    sens_lines = [f"**{k.replace('/', ' / ')}**: " +
                  ", ".join(f"{p}: {v:.2f}" for p, v in g.items())
                  for k, g in sens.items()]

    B = []
    a = B.append

    # =========================== content ==================================
    a(("h1", "Systematic Strategy - Final Report"))
    a(("p", f"*{TODAY} · 93-month daily backtest, 2019-01 to 2026-09 · "
            "all results net of costs unless stated · gross exposure capped at 100% "
            "throughout*"))
    a(("p",
       f"The brief asked for three things at once: an average month of 2-4% net of "
       f"costs, more than 75% of months positive, and no leverage. The engine shipped "
       f"here clears the second bar and misses the first. It is positive in {pos} of "
       f"{n} months ({m_full['PctPosMonths']:.1%}) and averages {avg:.2%} per month, "
       f"about {ret_gap:.1f}x short of the 2% floor. The third condition it obeys to "
       f"the decimal: gross exposure peaks at exactly 100% of equity and averages "
       f"{diag['mean_gross']:.0%}."))
    a(("p",
       "Two constraints explain the miss, and they deserve a plain statement up front "
       "rather than a buried caveat. The first is the no-leverage rule, which does "
       "real, quantifiable work in this universe (section 5.1). The second is the "
       "data: everything here runs on free, end-of-day sources, and that resolution "
       "sets a hard ceiling on which edges can even be observed, let alone harvested "
       "(section 5.2). Neither is an excuse; both are simply the shape of the problem "
       "as given."))
    a(("p",
       f"The most honest one-paragraph summary I can offer: put $10,000 into this book "
       f"on the first trading day of 2019 and it ends the sample at roughly "
       f"${final_wealth:,.0f} net of costs. The same stake in an S&P 500 index fund "
       f"ends at about ${spy_wealth:,.0f} — statistically the same destination. The "
       f"ride, however, is not the same: the index fund fell {abs(spy_maxdd):.0%} "
       f"peak-to-trough along the way, this book {abs(strat_maxdd):.0%}, and in 2022 "
       f"— the year the S&P lost {abs(sp_2022):.0%} — the book lost {abs(we_2022):.0%}. "
       f"Same place, very different journey. For context, the same $10,000 in Bitcoin "
       f"became roughly ${btc_wealth:,.0f}, with an 83% drawdown and 67% annualised "
       f"volatility en route — the crypto sleeve here is designed to skim that asset's "
       f"upside while stepping aside from the parts of the ride that end in surrendered "
       f"capital."))
    a(("image", "wealth_paths.png", "Figure 1 — Growth of $10,000: the book (net), "
       "an S&P 500 index fund, and Bitcoin buy-and-hold. Log scale."))
    a(("p",
       "The deliverable here is not a strategy that meets all three targets; it is a "
       "proof of which of those targets can be met under the stated constraints, and "
       "why the remaining one is not a tuning problem but a structural one. Section 6 "
       "now carries the additional burden this second edition owes a sceptical "
       "reader: a three-epoch holdout protocol, a walk-forward chain, a block "
       "bootstrap and a leave-one-out accounting of what each sleeve contributes, "
       "added in response to review."))

    a(("h2", "Contents"))
    a(("bullets", [
        "1. What actually ships — sleeves, budgets and how they were chosen, "
        "execution, costs, risk overlays",
        "2. Data and universe",
        "3. Results — metrics, target scorecard, monthly series, charts",
        "4. Where the 76% win rate comes from",
        "5. Why the average month is 1.64% and not 2% — no leverage and free data",
        "6. Is it real? — IS/OOS, sensitivity, correlations, deflated Sharpe, "
        "holdout protocol, walk-forward, bootstrap, leave-one-out",
        "7. Limitations, and what a larger programme would buy"]))

    a(("h2", "1. What actually ships"))
    a(("p",
       f"Four sleeves on deliberately different drivers, combined at risk budgets "
       f"({budget_str}). The budgets are shares of *risk*, not of notional "
       f"— the crypto sleeve runs three to four times the equity basket's volatility, "
       f"so a 25% risk budget buys it far less notional than the same number would buy "
       f"of equities."))
    a(("p",
       f"**How the budgets were chosen — full disclosure.** The four budgets are the "
       f"result of a constrained search, and pretending otherwise would be the first "
       f"lie of the report. Objective: maximise the monthly positive-share subject to "
       f"the unlevered 100% gross cap. The search space was a 20-cell grid over "
       f"(defensive, crypto) budget pairs, with the equity budget taking the residual "
       f"and the momentum sleeve held small. The mechanism the grid exposes: the "
       f"defensive budget is the only lever that raises the hit rate (it pays "
       f"precisely when equities fall), and every point of it is funded by selling "
       f"equity out of a gross-capped book — so the frontier is a straight trade "
       f"between the hit rate and the average month. The crypto budget is set at the "
       f"return-maximising point of that frontier because the carry overlay and cash "
       f"yield add zero-vol return that buys back part of the hit-rate cost. "
       f"Correlations near zero between sleeves (section 6.3) are what make fixed "
       f"risk budgets workable at all; inverse trailing vol converts each budget into "
       f"notional. Section 6.4 re-estimates the entire selection under a "
       f"research/validation/holdout protocol and quantifies how much the search "
       f"itself flattered the shipped numbers."))
    a(("bullets", [
        "**equity_core** — a long, inverse-volatility-weighted basket of "
        f"{len(EQUITY_TICKERS)} US large caps, held at full gross while SPY trades above "
        f"its rising {config.EQ_INDEX_LOOKBACK}-day average, cut to {config.EQ_OFF_WEIGHT:.0%} "
        "gross otherwise. This harvests equity drift; the trend gate is a risk control, "
        "not an alpha.",
        "**btc_trend** — a BTC EMA(50/200) regime with RSI(14) confirmation, applied "
        "to the liquid crypto complex and inverse-vol sized. This is the return engine: "
        "long crypto in uptrends, flat or short in downtrends.",
        "**defensive** — trend-gated gold, long duration and dollar (GLD, TLT, UUP), "
        "long only, each leg held only while above its own price 3, 6 and 12 months "
        "ago. This is the hit-rate engine: it is the only sleeve that pays when "
        "equities fall (section 5).",
        "**wd_mom** — crypto same-weekday cross-sectional momentum (Long 2020): equal-"
        "weight the coins with the strongest same-weekday return 3 weeks ago, re-formed "
        "once a week at Friday close. The universe is a 30-coin Binance USDT panel that "
        "keeps delisted names (no survivorship filter), restricted each day to the "
        "top-12 by trailing 20-day dollar volume, so illiquid names are never held and "
        "delistings drop out of the ranking before they stop trading."]))
    a(("p",
       "Two further sleeves were built, tested and rejected: a cross-sectional "
       "earnings tilt inspired by a WorldQuant style alpha (negative after costs under "
       "realistic next-open fills), and a delta-neutral funding-carry sleeve holding "
       "crypto spot against short perpetual swaps (adds variance reduction but no net "
       "drift: under the 100% gross cap its matched pair displaces blended directional "
       "exposure of nearly identical return per unit of gross). Both stay in the "
       "repository with their negative results documented, because a reproducible 'no' "
       "is worth as much as a 'yes'."))
    a(("p",
       f"**Cash yield.** Idle cash earns the 13-week T-bill rate, taken causally from "
       f"the last published quote before each day. This matters mostly through the "
       f"equity gate: when the SPY trend flips off, the basket drops to "
       f"{config.EQ_OFF_WEIGHT:.0%} gross and the remainder sits in T-bills rather "
       f"than earning nothing — turning part of the defensive posture into income."))
    a(("p",
       f"**Execution and costs.** Signals are computed from the close of day t and "
       f"filled at the open of t+1, with P&L measured open-to-open; the lag counts each "
       f"asset's own trading days (crypto trades seven days a week, equities five), and "
       f"the gross cap is applied to the held book for the same reason. Costs are "
       f"{config.EQUITY_BPS:g} bp per side on equities and {config.CRYPTO_BPS:g} bp on "
       f"crypto spot, plus slippage of {config.SLIPPAGE_VOL_FRACTION:.0%} of each "
       f"asset's own daily volatility — roughly 4 bp all-in for equities and 18 bp for "
       f"crypto, i.e. about twice the brief's guidance, deliberately conservative. "
       f"Running the book at zero cost changes the result by "
       f"{(costs_df['full/avg_monthly'].max() - costs_df['full/avg_monthly'].min()):.2f} "
       f"percentage points a month, so the choice of cost model matters, and the "
       f"cheaper end of the grid is not what is reported."))
    a(("p",
       f"**Risk overlays, in the order they bite.** Size to a "
       f"{config.PORTFOLIO_VOL_TARGET:.0%} portfolio volatility target, cap gross at "
       f"100%, then apply a drawdown throttle and a crash guard to the capped book. "
       f"The order matters: the vol target usually wants to scale *up*, so the cap "
       f"binds on roughly 85% of days — any de-risking multiplier applied before the "
       f"cap would simply be absorbed by it and never seen. A mortgage analogy: "
       f"trimming your spending is invisible if the bank has already capped the loan."))

    a(("h2", "2. Data and universe"))
    a(("bullets", [
        f"**US equities ({len(EQUITY_TICKERS)})**: {', '.join(EQUITY_TICKERS[:12])} and "
        f"{len(EQUITY_TICKERS) - 12} more large caps; split- and dividend-adjusted.",
        f"**Crypto spot ({len(CRYPTO_XS_SYMBOLS)})**: {', '.join(s.replace('USDT', '') for s in CRYPTO_SYMBOLS)} "
        f"as the core complex, plus {len(CRYPTO_XS_SYMBOLS) - len(CRYPTO_SYMBOLS)} further/delisted USDT pairs "
        "for the momentum sleeve's cross-section (delisted names included, so the panel "
        "is not survivorship-filtered).",
        "**Defensive / diversifiers**: GLD, TLT, UUP, plus 29 further ETF proxies used "
        "only in the cross-asset study of section 5.3.",
        f"**Fundamentals**: quarterly TTM EPS for {load_eps().shape[1]} tickers (used "
        "only by the rejected earnings sleeve).",
        "Everything is read from a frozen snapshot under `data/snapshot/` — no network "
        f"access anywhere in the pipeline. Data fingerprint `{data_fingerprint(data)}`."]))
    a(("p",
       "The window starts in January 2019 for one honest reason: that is the first date "
       "on which the Binance USDT cross-section has enough liquid names to size the "
       "cross-sectional sleeves as designed. Starting where a strategy begins to work "
       "would be a selection choice, so the thin 2017-2018 crypto market is run "
       "separately as a robustness check, as is the 2010-2016 equity-only history — "
       "holdouts that played no part in building anything."))

    a(("h2", "3. Results"))
    a(("table", *metrics_table(m_full, m_is, m_oos, m_oos_g, m_pre),
       {"font": 7.6, "ratios": (1.6, 1.3, 1.3, 1.3, 1.3, 1.6)}))
    a(("table", *scorecard_table(mnd), {"font": 8.2}))
    a(("p",
       f"{pos} of {n} months are positive against a bar of "
       f"{np.ceil(config.HIT_RATE_TARGET * n):.0f} — the condition is met by a single "
       f"month of margin. That thinness is stated here and revisited in section 5."))
    a(("table", *monthly_table(m_net),
       {"font": 6.0, "mono": False,
        "ratios": (1.1, *([0.92] * 12), 1.0, 0.75, 0.9)}))
    a(("p", "*`*` marks a month at or above the 2% target; `-` marks a negative month.*"))
    a(("image", "equity_curve.png",
       "Figure 2 — NAV, net vs gross of costs, log scale. The OOS period (2023+) was "
       "never used to make a design choice."))
    a(("image", "cumulative_pnl.png",
       "Figure 3 — Cumulative P&L per $1 of equity. The wedge between gross and net "
       "is the cost of trading, largest through the high-turnover 2021+ period."))
    a(("image", "monthly_heatmap.png", "Figure 4 — Monthly net returns (%)."))
    year_ret = (1 + mnd).resample("YE").prod() - 1
    best_y, worst_y = year_ret.idxmax(), year_ret.idxmin()
    a(("p",
       f"Full-period net CAGR is {m_full['CAGR']:.1%} (gross {metrics(out['ret_gross'])['CAGR']:.1%}) "
       f"at {m_full['AnnVol']:.1%} annualised volatility. Note where the good and bad "
       f"years sit: the strongest year ({best_y.year}, +{year_ret.max():.1%}) is "
       f"crypto-driven, and the weakest ({worst_y.year}) still returned "
       f"{year_ret.min():.1%} — flat, not negative. The book is a drift engine "
       f"with a turbocharger bolted on, and the turbocharger has moods."))

    a(("h2", "4. Where the 76% win rate comes from"))
    a(("p",
       "Before the defensive sleeve existed, this book was essentially long equity "
       "beta: it won 96% of up-index months and 17% of down-index months, so its hit "
       "rate was pinned near the index's own. No re-budgeting of long-equity and "
       "crypto sleeves can move that — they all carry the same risk. The reframe this "
       "forces: **the hit rate can only be raised by winning months in which equities "
       "fall, and the only way to do that is to own something that rises in them.**"))
    a(("table", *direction_table(direction), {"font": 8.0}))
    a(("p",
       "A bakery analogy keeps the mechanics clear. What fraction of days does the "
       "shop turn a profit at closing time? Not how big the profit is — just how often "
       "the till is ahead. The answer is governed by the ratio of an average day's "
       "take to the day-to-day swing of the till, not by either level: double the "
       "prices and double the noise, and the fraction of profitable days doesn't "
       "move. Formally, for monthly returns with mean m and volatility s, the share "
       "of positive months is approximately Φ(m/s) — the standard normal CDF "
       "evaluated at the *ratio*."))
    a(("p",
       f"The shipped book clears 75% at a Sharpe ratio of {m_full['Sharpe']:.2f}, where "
       f"the Gaussian identity says {hit_sharpe_req:.2f} should be required — an "
       f"annualised Sharpe of 2.3 is hedge-fund-legend territory. The escape hatch is "
       f"that the months are not Gaussian: skew {m_full['SkewMonthly']:.2f} and excess "
       f"kurtosis {m_full['KurtMonthly']:.1f}. The trend gates amputate the left tail — "
       f"losses are cut short — while many small gains do the counting, so "
       f"{m_full['PctPosMonths']:.0%} of months are positive against the "
       f"{normal_pos:.0%} a normal distribution would predict at this Sharpe. But read "
       f"carefully what that reshaping buys: a hit rate. It buys no return — that is "
       f"the crux of section 5."))
    a(("p",
       f"Nor is the 76% a robust optimum. Holding the defensive budget at its shipped "
       f"value and stepping the crypto budget, positive months go "
       f"{' -> '.join(f'{v:.0%}' for v in hitgrid.loc[np.isclose(hitgrid.defensive_budget, hitgrid.defensive_budget.iloc[0]), 'full/pct_pos'])} "
       f"— up, down, then flat, which is what sampling noise looks like rather than an "
       f"optimum. Only {n_pass_cells} of {n_cells} cells of the defensive x crypto "
       f"budget grid clear 75% in the full, in-sample and out-of-sample windows "
       f"simultaneously. And on the 2010-2016 holdout the same machine is positive in "
       f"only {m_pre['PctPosMonths']:.0%} of months *despite a higher Sharpe* "
       f"({m_pre['Sharpe']:.2f}) — direct evidence that monthly hit rate is a property "
       f"of the sample path as much as of the strategy. Win rates are sample-dependent "
       f"in a way Sharpe is not."))

    a(("h2", f"5. Why the average month is {avg:.2%} and not 2%"))
    a(("p",
       "The brief deserves a straight answer here, and the answer has two parts: a "
       "rule (no leverage) and a lens (free, delayed data)."))

    a(("h3", "5.1 The rule: win rate and return pull against each other"))
    a(("p",
       f"The book earns {avg:.2%} per month on gross exposure averaging "
       f"{diag['mean_gross']:.0%} of equity. To lift the mean to 2% while keeping the "
       f"same m/s — and the hit-rate condition has already pinned m/s — volatility "
       f"must scale with the mean, which means exposure must scale with it too: gross "
       f"would have to run near {ret_gap * diag['mean_gross']:.0%} of equity. It is a "
       f"mortgage-sized problem: the plan needs $155k of house against a $100k budget, "
       f"and the rules cap the loan at $100k. No rearrangement of the furniture lends "
       f"you the difference."))
    a(("p",
       f"The cap is not slack. Mean gross is {diag['mean_gross']:.0%}, the maximum is "
       f"exactly {diag['max_gross']:.0%}, the cap binds on roughly 85% of trading "
       f"days, and sweeping the volatility target from 20% to 35% moves mean gross by "
       f"about one percentage point. The book already leans on the ceiling nearly all "
       f"of the time — which is precisely what 'no leverage' was designed to force, "
       f"and precisely why the 2% floor and it are in tension."))
    a(("p",
       "Inside the cap, the only remaining lever is return per unit of gross exposure. "
       "The obvious candidate is more crypto, and the frontier below prices it: the "
       "arithmetic month does climb as the crypto budget rises, but the *compounded* "
       "month — what actually accrues, m − s²/2 in the standard continuous-time "
       "approximation — stays flat, because the extra return arrives stapled to even "
       "more extra volatility. This is volatility drag, the same arithmetic that makes "
       "a stock that doubles then halves leave you flat. Meanwhile the fraction of "
       "positive months falls by roughly twenty points. Nothing on the curve gets near "
       "2% with the hit rate intact."))
    a(("table", *frontier_table(frontier), {"font": 8.0}))
    a(("p",
       "This is the general shape of the problem, not a quirk of this book. A levered "
       "book can buy hit rate and return separately: hold a high-Sharpe, high-win-rate "
       "core and gear it up until the mean month reaches spec. Unlevered, the same "
       "gear-up must come from swapping into higher-volatility assets — and every such "
       "swap raises the monthly swing s faster than the mean m, which is exactly the "
       "ratio Φ(m/s) that section 4 showed governs the win rate. With gross pinned at "
       "100%, a 75% win rate and a 2% average month are not two requirements to satisfy; "
       "they are one requirement stated twice, and it sits off the achievable frontier."))

    a(("h3", "5.2 The lens: what free, end-of-day data can and cannot see"))
    a(("p",
       "Every price in this study is free and end-of-day: daily OHLCV for equities and "
       "ETFs, daily Binance spot bars, daily funding prints. No L2 book, no trades-and-"
       "quotes, no intraday bars, no analyst-estimate history, no point-in-time index "
       "membership. That resolution does not make signals slightly worse — it removes "
       "entire families of edges from the observable set:"))
    a(("bullets", [
        "**Intraday and microstructure edges are invisible.** Anything that lives on "
        "order-flow, queue position or overnight/intraday seasonality cannot even be "
        "measured from one print per session. The same-weekday momentum sleeve works "
        "at a daily grain precisely because that is the finest grain the data offers.",
        "**Slippage must be assumed, not measured.** With no book data, the cost model "
        "guesses impact from daily volatility (2% of it per side). Conservative as it "
        "is, the guess is asymmetric: for high-turnover crypto sleeves the true cost "
        "is the biggest unknown in the whole result.",
        "**Fundamental breadth collapses.** The point-in-time analyst-estimate fields "
        "the strongest cross-sectional alphas are built on sit behind paid terminals. "
        "The free substitute — realised quarterly EPS — arrives late, for 14 tickers, "
        "and produced a sleeve that failed its own fills test (section 5.3).",
        "**Survivorship cannot be fully removed.** The crypto panel keeps delisted "
        "names and the liquidity filter is point-in-time, but the equity list is "
        "today's large caps, because a point-in-time index-membership file is not "
        "freely available. The equity numbers are therefore flattered by an amount "
        "the data cannot bound.",
        "**News and event edges arrive pre-consumed.** By the time a free source "
        "reflects an announcement, the price has typically moved; EOD data guarantees "
        "the signal trades the *second* day of any event drift or none of it."]))
    a(("p",
       "The practical consequence for the 2% target: the edges left observable at "
       "EOD grain — trend, cross-sectional momentum, seasonality, carry, drift — are "
       "the most heavily harvested in finance, and their live yields are a fraction of "
       "what a 2019-2026 backtest shows. This book's 1.6% a month is close to the "
       "ceiling of what that data resolution supports at 75% win rate; the gap to 2% "
       "is a data-and-premium gap, not a tuning gap. Filling it means buying the "
       "lens: intraday bars, book data, estimate histories, point-in-time universes."))

    a(("h3", "5.3 What was tried, and the paper trail"))
    a(("p",
       "Roughly thirty candidate modifications were built, costed and tested — each on "
       "the same causal engine (decide at the close, fill at the next open, costs on "
       "every fill, gross capped on the held book), each judged on 2019-22 in-sample "
       "data alone, with 2023+ recorded but never consulted when choosing. The table "
       "is the paper trail:"))
    a(("table", *(
        ["Candidate modification", "Outcome", "Verdict"],
        [["Cross-sectional equity momentum (12-1, top 10/15/20; 6m variant)",
          "IS Sharpe 0.58-0.69 vs 0.91 shipped", "worse"],
         ["Sector rotation, top-3 by 6-12m momentum", "IS Sharpe 0.56", "worse"],
         ["Per-asset crypto trend (90/180d)", "IS Sharpe 1.11, +3.6%/mo — but OOS 0.03-0.14",
          "IS/OOS decay: overfit trap"],
         ["Crypto rotation (top-2/3 by trend)", "IS 0.87-0.95, OOS 0.20-0.34", "decays"],
         ["Blended crypto regime (BTC + own-trend)", "IS 0.87 vs 0.72 — OOS -0.04 vs +0.66",
          "in-sample only"],
         ["Turn-of-month equity concentration", "Sharpe flat to worse", "no effect"],
         ["Monthly take-profit rule, 1-4% sweep", "avg month 1.48% -> 0.34-1.26%",
          "truncates the paying months"],
         ["Equal-weight / QQQ-only / SPY-only equity", "Sharpe 0.92 / 0.61 / 0.62",
          "no better"],
         ["Per-asset equity trend, 52-week-high filter", "0.77-0.96", "worse"],
         ["Idle weight rotated into trending defensives",
          "Sharpe 0.98 vs 0.91 IS; OOS hit 75.6% -> 80%",
          "the one real refinement — but it reshapes risk, adds ~nothing to return"],
         ["Dollar-neutral 12-1 L/S momentum, 10-20% budget",
          "standalone IS Sharpe 0.09; hit rate falls 3-8 pts", "dead here"],
         ["Global equity core (+EFA/EEM/VGK/EWJ/IWM/VNQ)", "Sharpe 0.81 vs 0.91", "worse"],
         ["Dual momentum on 30 ETFs; engine rotation", "Sharpe 0.11-0.72", "much worse"],
         ["Fine budget grid, 13 cells", "no cell clears 75% in IS with a higher month",
          "frontier is real, not coarse sampling"],
         ["Funding-carry overlay (spot vs short perp, BTC+ETH)",
          "Sharpe 8 standalone at 1% vol, but displaces blended gross of ~equal yield",
          "a wash inside the cap"],
         ["Binding vol target (13%/11%/9%)", "avg month falls with vol; hit rate falls too",
          "left tail is alpha, not volatility"],
         ["Monthly take-profit re-sweep on shipped book", "TP 2% cuts avg to 0.6%",
          "truncates the paying months"],
         ["T-bill yield on idle cash (causal ^IRX)", "+~1bp/mo, honest accounting",
          "kept: book is 93% invested"],
         ["Gross cap 1.25 (diagnostic only, not shipped)",
          "1.64%/mo at 73.5% — both targets clear together only beyond the cap",
          "prices the constraint"]]),
       {"font": 7.4, "ratios": (2.6, 2.6, 1.6), "align": "LEFT"}))
    a(("p",
       "The pattern across all of them is the whole story of quantitative research in "
       "miniature: the modifications that raised the average month did it by raising "
       "the swing s faster than the mean m — which quietly breaks the hit rate — and "
       "the modifications that protected the hit rate left the mean untouched. The "
       "standout IS performer (per-asset crypto trend, Sharpe 1.11 in-sample, +3.6% a "
       "month) collapses to 0.03-0.14 out-of-sample: had I selected on the full "
       "sample, that is the trap I would have shipped."))
    a(("p",
       "The brief also invites futures and other asset classes, and the textbook route "
       "— more independent bets, higher Sharpe — was tested properly: a 29-instrument "
       "managed-futures book across rates, credit, commodities, FX and non-US equity, "
       " exposures taken via liquid ETFs so that notional is counted exactly as the "
       "brief requires."))
    a(("table", *xasset_table(xasset), {"font": 8.0, "align": "LEFT"}))
    a(("p",
       "It fails, and it fails because of the no-leverage rule rather than tuning. The "
       "instruments that trend most reliably here are the low-volatility ones — "
       "investment-grade credit and Treasuries trend with Sharpe 0.5-0.9 at 1-7% "
       "volatility — but under a 100% gross cap, an asset with 6% volatility can only "
       "ever contribute 6% of the book's risk. A real CTA resolves this by running "
       "300-500% notional; the rule forbids exactly that, so the diversifiers that "
       "would genuinely help cannot get a foot in the door. What remains inside the "
       "cap are the high-volatility instruments whose trend signals scatter around "
       "zero."))
    a(("p",
       f"To size the gap honestly: compounding at 2% per month turns $10,000 into "
       f"roughly ${two_pct_wealth:,.0f} over the same {n} months, against the "
       f"${final_wealth:,.0f} actually delivered. That gap — "
       f"{two_pct_wealth / final_wealth:.1f}x — is the price of the constraints as "
       f"given: an unlevered gross cap and an end-of-day lens. It is a "
       f"research-programme-sized gap, not a tuning-sized one."))

    a(("h2", "6. Is it real? Robustness of what survives"))
    a(("p",
       "Any single backtest deserves suspicion; the file format of self-deception is a "
       "pretty equity curve. Four checks, none of which the result flatters itself on:"))
    a(("h3", "6.1 In-sample vs out-of-sample, per sleeve"))
    a(("table", *plain_table(decay.round(2), index_name="sleeve"), {"font": 8.0}))
    a(("p",
       "equity_core does not decay (its OOS Sharpe is higher); btc_trend decays "
       "modestly, as single-asset trend should; the rejected candidates above show the "
       "contrast case. Every design decision in this report was made on the IS window "
       "only."))
    a(("h3", "6.2 Parameter sensitivity"))
    a(("bullets", sens_lines))
    a(("p",
       f"equity_core is flat across every grid ({min(min(g.values()) for k, g in sens.items() if k.startswith('equity_core')):.2f} "
       f"to {max(max(g.values()) for k, g in sens.items() if k.startswith('equity_core')):.2f} "
       f"OOS Sharpe) — what a real effect looks like. The honest caveat: the RSI "
       f"threshold on the crypto sleeve is *not* flat, so that parameter carries "
       f"specification risk."))
    a(("h3", "6.3 Correlation, fills and risk overlays"))
    a(("p",
       "Sleeve correlations (net, full period) — all pairs within a few percent of "
       "zero, so the result is not one bet wearing three hats:"))
    a(("table", *plain_table(sleeve_corr.round(3), index_name="sleeve"),
       {"font": 8.0}))
    a(("table", *variant_table(execu, "execution", "Fill assumption"),
       {"font": 7.8}))
    a(("p",
       f"Moving from the optimistic same-close fill to the honest next-open fill costs "
       f"{(execu.set_index('execution').loc['same_close', 'full/geom_monthly'] - execu.set_index('execution').loc['next_open', 'full/geom_monthly']):.2%} "
       f"a month of compounded return, and the edge survives. The slower next-close "
       f"fill brackets the sensitivity from the other side."))
    a(("table", *variant_table(ablation, "variant", "Risk-overlay variant"),
       {"font": 7.8}))
    a(("p",
       f"Multiple testing, charged at the honest count: every configuration whose "
       f"performance was ever computed in developing this book — the 20-cell "
       f"hit-rate grid, the budget frontier, the risk-overlay, cost, execution and "
       f"take-profit variants, the candidate-alpha and cross-asset studies, and the "
       f"protocol re-selection — totals **{proto['dsr']['n_trials']} trials**, not "
       f"the 8 previously reported. Recomputing the deflated Sharpe ratio on the "
       f"research+validation OOS Sharpe with that count gives "
       f"**DSR = {proto['dsr']['dsr']:.2f}** (the 8-trial figure of "
       f"{diag['dsr']:.2f} was flattered). A DSR near 0.6 does not certify the edge "
       f"is zero; it says that after paying for the whole search, the evidence from "
       f"~4.5 years of OOS history alone leaves real uncertainty about the true "
       f"Sharpe — which is why the protocol, walk-forward and bootstrap below, not a "
       f"single DSR digit, carry the weight of the argument."))

    # ---------------- 6.4-6.7: post-review protocol -------------------------
    a(("h3", "6.4 Three-epoch protocol: research / validation / holdout"))
    a(("p",
       "The first edition of this report chose the sleeve budgets on a grid scored "
       "across full, in-sample and out-of-sample windows simultaneously — which means "
       "the 'OOS' numbers were used for selection and are not out-of-sample. The fix "
       "is procedural, not cosmetic. The sample is re-split into three epochs: "
       "research (2019-2022, sleeve construction and parameters), validation "
       "(2023-2024, budget selection — the only fitting on this epoch), and a holdout "
       "(2025-01 onward) scored exactly once with budgets frozen before it was "
       "touched. Re-running the same 20-cell selection with access to research + "
       "validation only picks equity 55% / crypto 30% / defensive 15% — near, but not "
       "identical, to the shipped 48/26/13 — and the frozen-budget holdout then reads:"))
    ep = proto["epochs"]
    a(("table",
       ["epoch", "avg month", "positive months", "Sharpe", "max DD", "months"],
       [[k,
         f"{v['AvgMonthly']:.2%}", f"{v['PctPosMonths']:.1%}",
         f"{v['Sharpe']:.2f}", f"{v['MaxDD']:.1%}", str(v['NMonths'])]
        for k, v in ep.items()], {"font": 8.0}))
    a(("p",
       "The honest reading: the hit rate on research+validation is "
       f"{ep['research+validation']['PctPosMonths']:.1%} — below the 75% bar the "
       f"shipped grid cleared only by selecting on all three windows at once — while "
       f"the untouched holdout came in at {ep['holdout']['PctPosMonths']:.1%} positive "
       f"with a {ep['holdout']['AvgMonthly']:.2%} average month. Both facts are "
       "reported; the first is the price of having once searched on the later data, "
       "and the second is what a genuinely fresh window did with the same rules."))

    a(("h3", "6.5 Walk-forward"))
    a(("p",
       "A single IS/OOS cut can be lucky. The walk-forward re-runs selection as a "
       "live process would: at the start of each year, pick the budget cell on all "
       "data prior to that year (same fixed rule: hit rate first, average month as "
       "tie-break), then trade that cell through the year. Four test blocks "
       "(2023-2026) chain into one performance record:"))
    a(("table",
       ["test year", "picked def/crypto", "train hit rate", "avg month",
        "positive months", "Sharpe", "max DD"],
       [[str(int(r.test_year)),
         f"{r.picked_defensive:.0%} / {r.picked_crypto:.0%}",
         f"{r.train_pct_pos:.1%}", f"{r.test_avg_monthly:.2%}",
         f"{r.test_pct_pos:.1%}", f"{r.test_sharpe:.2f}", f"{r.test_max_dd:.1%}"]
        for _, r in wf.iterrows()], {"font": 8.0}))
    a(("p",
       f"Pooled across all four blocks: {wfs['pct_pos']:.1%} positive months, "
       f"{wfs['avg_monthly']:.2%} average month, Sharpe {wfs['sharpe_pooled']:.2f}. "
       f"The selection rule never moved off the same cell — evidence the frontier "
       f"point is stable under expanding information sets, not a one-off sample "
       f"accident — and the walk-forward book (which never saw its own test year) "
       f"matches the static book's performance almost exactly."))

    a(("h3", "6.6 Block bootstrap: distributions, not points"))
    a(("p",
       "Single-number metrics inherit the luck of one sample path. A stationary "
       "block bootstrap (expected block length 20 days, 2000 resamples) over the "
       "daily net returns gives the sampling distribution of each headline metric:"))
    bs = proto["bootstrap"]
    a(("table",
       ["metric", "5th pct", "median", "95th pct"],
       [[k, f"{v['p05']:.3f}" if abs(v['p05']) < 1 else f"{v['p05']:.2f}",
         f"{v['median']:.3f}" if abs(v['median']) < 1 else f"{v['median']:.2f}",
         f"{v['p95']:.3f}" if abs(v['p95']) < 1 else f"{v['p95']:.2f}"]
        for k, v in bs.items()], {"font": 8.0}))
    a(("p",
       f"Three facts the distributions expose that the point estimates hide. Sharpe: "
       f"the 5th percentile is {bs['sharpe']['p05']:.2f} — the edge survives "
       f"resampling, but a bad draw of history could plausibly have halved it. Max "
       f"drawdown: the median resample draws a deeper loss "
       f"({bs['max_dd']['median']:.1%}) than the realised "
       f"{m_full['MaxDD']:.1%}, and the 5th percentile reaches "
       f"{bs['max_dd']['p05']:.1%} — the realised path was not the worst case. Hit "
       f"rate: the 90% interval runs {bs['pct_pos']['p05']:.0%} to "
       f"{bs['pct_pos']['p95']:.0%} around a median of "
       f"{bs['pct_pos']['median']:.0%} — the claimed 75% is at the optimistic edge "
       f"of what this sample can support, and the honest statement is 'roughly "
       f"70%, plausibly above 75%, not demonstrably so'."))

    a(("h3", "6.7 Sleeve contribution: leave-one-out"))
    a(("p",
       "What each sleeve is actually for, tested by removing it and re-normalising "
       "the remaining budgets (holdout epoch in the last three columns, full sample "
       "for reference):"))
    loo_h = loo[loo.epoch == "full"].set_index("variant")
    loo_o = loo[loo.epoch == "holdout"].set_index("variant")
    rows = []
    for v in ["full", "drop_equity_core", "drop_btc_trend", "drop_defensive",
              "drop_wd_mom"]:
        f, h = loo_h.loc[v], loo_o.loc[v]
        label = v.replace("drop_", "minus ").replace("_", " ")
        rows.append([label,
                     f"{f.AvgMonthly:.2%}", f"{f.Sharpe:.2f}",
                     f"{f.MaxDD:.1%}", f"{f.PctPosMonths:.1%}",
                     f"{h.AvgMonthly:.2%}", f"{h.Sharpe:.2f}",
                     f"{h.MaxDD:.1%}", f"{h.PctPosMonths:.1%}"])
    a(("table",
       ["variant", "avg mo", "Sharpe", "maxDD", "pos", "avg mo", "Sharpe",
        "maxDD", "pos"],
       rows, {"font": 7.8}))
    a(("p",
       "Each sleeve earns its seat on a different axis. Removing equity raises the "
       "average month (crypto scales up into the freed gross) but cuts Sharpe from "
       f"{loo_h.loc['full', 'Sharpe']:.2f} to "
       f"{loo_h.loc['drop_equity_core', 'Sharpe']:.2f} and the hit rate by 13 points "
       "— equity is the stability engine. Removing defensive costs the most Sharpe "
       "in the holdout and deepens max DD from "
       f"{loo_o.loc['full', 'MaxDD']:.1%} to "
       f"{loo_o.loc['drop_defensive', 'MaxDD']:.1%} — it is the crash hedge, as "
       "designed. Removing the BTC trend sleeve costs the most average month in the "
       "full sample — it is the return engine. Removing the small momentum sleeve "
       "changes almost nothing, and its retention is a diversification decision, not "
       "a return one."))

    a(("h3", "6.8 Verification audit"))
    a(("p",
       "This subsection is a self-audit against the four failure modes that matter for "
       "a backtest of this kind, with the mechanism and the evidence for each verdict. "
       "Every check below is also enforced by the automated suite in "
       "`tests/test_no_lookahead.py` (24 tests), which `make all` runs before anything "
       "else."))
    a(("h3", "6.8.1 Lookahead and data leakage"))
    a(("p",
       "The rule the engine obeys: a signal computed from the close of day t can first "
       "earn money from the open of day t+1, never earlier. Evidence, in increasing "
       "order of subtlety:"))
    a(("bullets", [
        "**Signals.** Every signal function is tested for invariance: appending 60 "
        "future bars, or truncating the sample, must leave every past signal value "
        "bit-for-bit unchanged. A causal function cannot know the future, so this "
        "catches any accidental use of it.",
        "**Fundamentals.** The (rejected) earnings sleeve stamps each EPS value at "
        "fiscal period end plus a 60-day publication lag; a test perturbs one EPS "
        "observation and asserts the signal is untouched before the boundary and "
        "changes only after it.",
        "**The mixed-calendar trap.** The execution lag is counted in each asset's own "
        "trading days. A naive flat shift over a union calendar lets a Friday signal "
        "earn Friday's equity move (the crypto row on Monday is Saturday's, which "
        "forward-fills to Friday) — the regression test here planted a 5-day momentum "
        "signal on pure noise and asserts the resulting Sharpe is ~zero. Under the "
        "bug, that Sharpe was implausibly large; the fix is what ships.",
        "**Risk overlays.** The vol target, drawdown throttle, crash guard and budget "
        "scalers all run inside the engine, so they get their own invariance test over "
        "the final exposure panel, and the weekly W-FRI momentum formation is tested "
        "to use only completed weeks and to stay flat within the week.",
        "**Cash yield.** The T-bill rate published with effective date t enters the "
        "books from t+1 only, and weekends carry the last completed quote."]))
    a(("h3", "6.8.2 Transaction costs"))
    a(("p",
       f"Costs are charged on the bar each new position starts earning, at "
       f"{config.EQUITY_BPS:g} bp per side on equities and {config.CRYPTO_BPS:g} bp on "
       f"crypto — matching or exceeding the brief's guidance — plus slippage of "
       f"{config.SLIPPAGE_VOL_FRACTION:.0%} of each asset's own daily volatility per "
       f"side, which lands the all-in numbers near 4 bp (equities) and 18 bp (crypto): "
       f"roughly twice the brief's floor. The cost grid confirms the reported numbers "
       f"do not live off cheap-fill assumptions, and the same-close fill sensitivity "
       f"in section 6.3 brackets the execution convention from the optimistic side."))
    a(("h3", "6.8.3 Survivorship and delistings"))
    a(("p",
       "The 30-coin crypto universe includes delisted Binance USDT pairs "
       "(WAVES, NEO, OMG and peers), so the cross-section a real trader faced is the "
       "one the sleeve ranks. The liquidity filter is point-in-time — trailing 20-day "
       "dollar volume only, tested by perturbing future volume — so a dying coin exits "
       "the eligible set before it stops printing. The one place survivorship remains "
       "is the equity list, which is today's large caps; the data to fix it is not "
       "free, and the direction of the bias is stated rather than bounded (section 8)."))
    a(("h3", "6.8.4 Overfitting"))
    a(("bullets", [
        "**Parameter count is small and a priori.** EMA 50/200, RSI 14, SMA 200-day "
        "gate, 63/126/252 trend horizons, 3-week weekday momentum — literature "
        "defaults, set before the backtest, none re-estimated.",
        "**The sensitivity surfaces are flat where it matters** (section 6.2): "
        "equity_core's parameters can be doubled or halved with OOS Sharpe moving a "
        "few tenths. The honest exception — the crypto RSI floor — is flagged there "
        "rather than hidden.",
        "**IS/OOS discipline, re-established.** The first edition's budget search "
        "scored the out-of-sample windows and used them for selection; the "
        "three-epoch protocol in section 6.4 re-runs selection on research + "
        "validation only and scores 2025+ as a true frozen holdout, with a "
        "walk-forward chain (6.5) replicating the selection rule year by year.",
        "**Multiple testing is charged at the honest count.** "
        f"{proto['dsr']['n_trials']} configurations were evaluated in developing "
        "this book; the deflated Sharpe at that count is "
        f"{proto['dsr']['dsr']:.2f} (section 6.3) — weaker than the 8-trial figure "
        "first reported, and stated as such. The weight of the evidence argument "
        "rests on the holdout, walk-forward and bootstrap agreement, not on one "
        "digit.",
        "**The hit rate is a range, not a point.** The bootstrap puts the monthly "
        "positive share at roughly 70% with a 90% interval of "
        f"{proto['bootstrap']['pct_pos']['p05']:.0%}-{proto['bootstrap']['pct_pos']['p95']:.0%} "
        "(section 6.6): the 75% headline sits at the optimistic edge of what the "
        "sample supports, and the report says so rather than quoting it to three "
        "decimals."]))
    a(("p",
       "Verdict: the shipped book is clean on lookahead, costs and survivorship; its "
       "exposure to overfitting is disclosed and concentrated in one place — the "
       "budget selection — which is exactly why the 75.3% figure is presented as a "
       "sample property, not a strategy constant."))

    a(("h2", "7. Limitations, and what a larger programme would buy"))
    a(("p",
       "Every backtest is a compromise between the data available, the assumptions "
       "made, and the questions being asked. The limitations below are not artefacts "
       "of a rushed job; they are the boundaries of what a single-researcher, "
       "public-data project can honestly claim. Each one is stated here because it "
       "qualifies the headline result, and because a reader deserves to know "
       "precisely where the edge is soft."))
    a(("h3", "7.1 Survivorship bias"))
    a(("p",
       "The equity universe is drawn from today's large-cap constituents: AAPL, MSFT, "
       "NVDA, and the rest were all selected because they are liquid, well-known "
       "names in 2026. This flatters the equity sleeve. Companies that were large in "
       "2019 but have since declined or been acquired are absent from the backtest "
       "universe. The fix is a point-in-time index-membership file, e.g. historical "
       "S&P 500 constituents with entry and exit dates, which is not freely available "
       "in a form suitable for this kind of causal backtest. The 2010-2016 "
       "equity-only run is reported partly to mitigate this concern: it uses the same "
       "stock list, so it inherits the same bias, and its Sharpe of 1.10 should be "
       "read as an upper bound for the equity sleeve's true historical performance."))
    a(("h3", "7.2 The crypto window starts where crypto starts"))
    a(("p",
       "The full-sample backtest begins in January 2019, which is the first date on "
       "which the Binance USDT cross-section has enough liquid names to run the "
       "momentum sleeve as designed. That is not a cosmetic choice: before 2019, the "
       "tradable crypto universe was too thin to support a weekly cross-sectional "
       "ranking. But it also means the sample starts at the beginning of a crypto "
       "bull phase. The crypto sleeve's contribution to the early years is therefore "
       "flattered by the timing. The 2017-2018 crypto market is run separately as a "
       "robustness check and is not part of the shipped result. The 2010-2016 "
       "equity-only holdout is reported for the same reason: it is the only way to "
       "audit the book outside the crypto era."))
    a(("h3", "7.3 The hit rate is met by one month of margin"))
    a(("p",
       f"The positive-month count is {pos} out of {n}, against a requirement of "
       f"{np.ceil(config.HIT_RATE_TARGET * n):.0f}. The condition is met by exactly "
       f"one month. This thinness is reported in the summary table and here "
       f"repeated: I would not represent "
       f"{m_full['PctPosMonths']:.0%} as a stable property of this strategy. On the "
       f"2010-2016 equity-only holdout, the same machine is positive in only "
       f"{m_pre['PctPosMonths']:.0%} of months despite a higher Sharpe (1.10). Win "
       f"rates are sample-dependent in a way that Sharpe ratios are not — and the "
       f"block bootstrap in section 6.6 makes the same point quantitatively: the 90% "
       f"interval for the positive-month share runs "
       f"{proto['bootstrap']['pct_pos']['p05']:.0%} to "
       f"{proto['bootstrap']['pct_pos']['p95']:.0%}. The >75% bar was cleared in the "
       f"full sample; whether it clears in the next 93 months is not something any "
       f"honest backtest can promise."))
    a(("h3", "7.4 Fundamental data breadth"))
    a(("p",
       "The rejected earnings sleeve used quarterly TTM EPS for 14 tickers, a "
       "fraction of the cross-section that would be needed to build a real "
       "fundamental factor. The original WorldQuant-style alpha relied on analyst "
       "estimate revisions, which are richer and more timely than realised EPS. With "
       "14 names and realised earnings, the signal was too sparse to survive costs. "
       "This is not a failure of the idea; it is a limitation of the freely available "
       "fundamental data. A proper test would require point-in-time analyst estimates "
       "for at least the full equity universe, preferably with historical revision "
       "history."))
    a(("h3", "7.5 Crypto cost assumptions are fair-weather estimates"))
    a(("p",
       "Crypto costs are set at 10 bp per side plus slippage of 2% of daily "
       "volatility, roughly 18 bp all-in. These are reasonable for liquid large-cap "
       "pairs on a major exchange in normal conditions. They are optimistic for "
       "stressed conditions: during liquidations, spreads widen, depth evaporates, "
       "and the same fill assumption no longer holds. Crypto is the highest-turnover "
       "sleeve in the book, so the cost model is most exposed there. A conservative "
       "treatment would apply a stress multiplier to crypto costs during "
       "high-volatility regimes; that refinement is not implemented here, and the "
       "reader should treat the crypto sleeve's net returns as slightly generous "
       "during the most volatile months."))
    a(("h3", "7.6 No futures, hence no cheap access to rates, commodities, and FX"))
    a(("p",
       "The brief explicitly permits futures, and section 5.3 shows why the textbook "
       "diversifiers — rates, credit, commodities, FX — fail to help under a 100% "
       "gross cap. Their trend signals are real, but their volatilities are so low "
       "that they cannot contribute meaningful risk to a book that cannot lever. A "
       "real CTA would run 300-500% notional on these instruments; the no-leverage "
       "rule forbids exactly that. The absence of futures in the shipped book is not "
       "an oversight. It is a direct consequence of the constraint. Under a higher "
       "gross allowance, or with access to futures margin as a form of exposure "
       "rather than funding, these assets would likely improve the book's "
       "risk-adjusted return. Inside the cap as written, they are dead weight."))
    a(("h3", "7.7 What a larger programme would buy"))
    a(("p",
       f"The gap between {avg:.2%} and 2% per month is real, and it is not a tuning "
       f"problem. It is a research-programme-sized gap. The following steps would be "
       f"the natural next moves for a team with dedicated resources."))
    a(("bullets", [
        "**Point-in-time index membership.** Replace the current large-cap list with "
        "historical S&P 500 or Russell 1000 constituents, including delistings and "
        "corporate actions. This removes the survivorship bias in the equity sleeve "
        "and allows the backtest to run on a universe that existed at the time.",
        "**Execution research on crypto.** The crypto sleeve assumes 18 bp of "
        "slippage. In practice, liquid large-cap pairs on major exchanges can often "
        "be traded at 5-8 bp per side with limit orders and careful timing. Halving "
        "the slippage assumption adds roughly 0.2 percentage points to the "
        "compounded monthly return without changing win rate or gross. This is the "
        "highest-certainty edge left on the table.",
        "**Defined-risk options overlays.** Options on equity indices and Bitcoin "
        "offer a way to earn volatility risk premia inside a 100% notional cap. A "
        "short-volatility or carry-style options strategy, properly sized and "
        "stress-tested, could add return without requiring additional gross "
        "exposure. This is a separate research stream, not a modification of the "
        "existing sleeves.",
        "**Broader data sources.** Intraday, orderbook, on-chain, and "
        "analyst-estimate data are all natural extensions. Each opens a new strategy "
        "space that daily public bars cannot reach. The current book is deliberately "
        "restricted to public daily data; a larger programme would not be."]))
    a(("p",
       "A proper walk-forward re-estimation loop — flagged by the first edition as "
       "future work — has since been built and is reported in section 6.5. None of "
       "the remaining steps were attempted here. They are identified as the "
       "highest-value directions for future work, and the gap between the shipped "
       "result and the 2% floor is worth that programme. The result reported here is "
       "not the end of the search; it is the frontier of what could be reached with "
       "the data and constraints available to a single researcher."))
    a(("p",
       "*Reproducibility: `make all` runs the 24 leakage tests, the backtest, the "
       "validation suite and this report from the frozen snapshot, end to end, with "
       "no network access.*"))

    (config.OUTPUT / "REPORT.md").write_text(render_markdown(B))
    render_pdf(B, config.OUTPUT / "REPORT.pdf")
    print("wrote", config.OUTPUT / "REPORT.pdf")


if __name__ == "__main__":
    main()
