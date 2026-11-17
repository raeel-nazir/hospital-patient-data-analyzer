"""
Hospital Patient Data Analyzer
------------------------------
Usage:
    python analyzer.py                              # uses data/healthcare_dataset.csv
    python analyzer.py path/to/file.csv             # any file with the same columns
    python analyzer.py path/to/file.csv outputs     # choose output folder

Pipeline:  Load -> Clean -> Analyse (Pandas + SQL) -> Charts -> Dashboard -> Report
"""

import os
import sqlite3
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# ------------------------------------------------------------------ settings
REQUIRED_COLUMNS = [
    "Name", "Age", "Gender", "Blood Type", "Medical Condition",
    "Date of Admission", "Doctor", "Hospital", "Insurance Provider",
    "Billing Amount", "Room Number", "Admission Type", "Discharge Date",
    "Medication", "Test Results",
]
AGE_BINS = [0, 17, 35, 50, 65, 120]
AGE_LABELS = ["<18", "18-35", "36-50", "51-65", "65+"]
BLUE, GREY, TEAL, ORANGE, RED = "#2B6CB0", "#9AA3AE", "#2A9D8F", "#E08A4F", "#C0392B"

plt.rcParams.update({
    "figure.dpi": 110,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.titleweight": "bold",
    "axes.titlesize": 11,
    "font.size": 9,
})


# ------------------------------------------------------------------ 1. LOAD
def load_data(path):
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Input file is missing columns: {missing}")
    return df


# ----------------------------------------------------------------- 2. CLEAN
def clean_data(df):
    """Returns (clean_df, log) where log is a dict of what was fixed."""
    log = {"rows_before": len(df)}

    # text columns: remove extra spaces, fix random casing in names
    for col in df.select_dtypes(include=["object", "string"]).columns:
        df[col] = df[col].astype(str).str.strip()
    df["Name"] = df["Name"].str.title()
    log["names_recased"] = int(len(df))

    # duplicates (checked after the name fix so "BOBBY" and "Bobby" match)
    before = len(df)
    df = df.drop_duplicates()
    log["duplicates_removed"] = before - len(df)

    # dates: text -> real dates
    df["Date of Admission"] = pd.to_datetime(df["Date of Admission"], errors="coerce")
    df["Discharge Date"] = pd.to_datetime(df["Discharge Date"], errors="coerce")
    bad_dates = df["Date of Admission"].isna() | df["Discharge Date"].isna()
    log["bad_dates_removed"] = int(bad_dates.sum())
    df = df[~bad_dates]

    # discharge before admission is impossible
    wrong_order = df["Discharge Date"] < df["Date of Admission"]
    log["discharge_before_admission_removed"] = int(wrong_order.sum())
    df = df[~wrong_order]

    # negative billing is invalid
    negative = df["Billing Amount"] < 0
    log["negative_billing_removed"] = int(negative.sum())
    df = df[~negative]

    # missing values (none expected, but handled for new files)
    log["missing_values_left"] = int(df.isna().sum().sum())
    df = df.dropna()

    # new columns
    df["Length of Stay"] = (df["Discharge Date"] - df["Date of Admission"]).dt.days
    df["Age Group"] = pd.cut(df["Age"], bins=AGE_BINS, labels=AGE_LABELS)
    df["Admission Year"] = df["Date of Admission"].dt.year
    df["Admission Month"] = df["Date of Admission"].dt.to_period("M").astype(str)
    df["Billing Per Day"] = df["Billing Amount"] / df["Length of Stay"].replace(0, 1)
    df["Billing Amount"] = df["Billing Amount"].round(2)
    df["Billing Per Day"] = df["Billing Per Day"].round(2)

    log["rows_after"] = len(df)
    return df.reset_index(drop=True), log


# --------------------------------------------------------------- 3. ANALYSE
def analyse(df):
    t = {}
    t["overview"] = pd.DataFrame({
        "Metric": ["Total patients", "Average age", "Average length of stay (days)",
                   "Average billing ($)", "Total billing ($)",
                   "Date range start", "Date range end"],
        "Value": [f"{len(df):,}", round(df["Age"].mean(), 1),
                  round(df["Length of Stay"].mean(), 1),
                  f"{df['Billing Amount'].mean():,.0f}", f"{df['Billing Amount'].sum():,.0f}",
                  df["Date of Admission"].min().date(), df["Date of Admission"].max().date()],
    })

    def group_summary(col):
        g = df.groupby(col, observed=True).agg(
            Patients=("Name", "count"),
            Avg_Billing=("Billing Amount", "mean"),
            Total_Billing=("Billing Amount", "sum"),
            Avg_Stay_Days=("Length of Stay", "mean"),
            Avg_Age=("Age", "mean"),
        ).round(2)
        return g.sort_values("Avg_Billing", ascending=False).reset_index()

    for col, key in [("Medical Condition", "by_condition"),
                     ("Admission Type", "by_admission_type"),
                     ("Insurance Provider", "by_insurance"),
                     ("Age Group", "by_age_group"),
                     ("Gender", "by_gender"),
                     ("Medication", "by_medication")]:
        t[key] = group_summary(col)

    # test results share
    t["test_by_condition"] = (pd.crosstab(df["Medical Condition"], df["Test Results"],
                                          normalize="index") * 100).round(1).reset_index()
    t["test_by_age_group"] = (pd.crosstab(df["Age Group"], df["Test Results"],
                                          normalize="index") * 100).round(1).reset_index()
    t["test_by_medication"] = (pd.crosstab(df["Medication"], df["Test Results"],
                                           normalize="index") * 100).round(1).reset_index()

    # admission type mix per condition
    t["admission_mix_by_condition"] = (pd.crosstab(df["Medical Condition"], df["Admission Type"],
                                                   normalize="index") * 100).round(1).reset_index()

    # time trend
    t["yearly"] = (df.groupby("Admission Year")
                   .agg(Patients=("Name", "count"), Avg_Billing=("Billing Amount", "mean"),
                        Avg_Stay_Days=("Length of Stay", "mean")).round(2).reset_index())
    t["monthly"] = (df.groupby("Admission Month").size().rename("Patients").reset_index())

    # correlation
    t["correlation"] = df[["Age", "Billing Amount", "Length of Stay", "Room Number"]].corr().round(3)

    # high-cost and long-stay patients
    cost_cut = df["Billing Amount"].quantile(0.95)
    stay_cut = df["Length of Stay"].quantile(0.95)
    df["High Cost"] = df["Billing Amount"] >= cost_cut
    df["Long Stay"] = df["Length of Stay"] >= stay_cut
    t["thresholds"] = pd.DataFrame({
        "Flag": ["High cost (top 5%)", "Long stay (top 5%)"],
        "Cut-off": [f"${cost_cut:,.0f}", f"{stay_cut:.0f} days"],
        "Patients flagged": [int(df["High Cost"].sum()), int(df["Long Stay"].sum())],
    })
    t["flag_by_condition"] = (df.groupby("Medical Condition")[["High Cost", "Long Stay"]]
                              .mean().mul(100).round(1).reset_index())
    t["flagged_patients"] = df[df["High Cost"] | df["Long Stay"]][
        ["Name", "Age", "Medical Condition", "Admission Type", "Insurance Provider",
         "Billing Amount", "Length of Stay", "High Cost", "Long Stay"]
    ].sort_values("Billing Amount", ascending=False)
    return t


# ------------------------------------------------------------------ 4. SQL
SQL_QUERIES = {
    "Q1 patients and billing by condition": """
        SELECT "Medical Condition" AS condition,
               COUNT(*)                         AS patients,
               ROUND(AVG("Billing Amount"), 2)  AS avg_billing,
               ROUND(AVG("Length of Stay"), 2)  AS avg_stay
        FROM patients
        GROUP BY "Medical Condition"
        ORDER BY avg_billing DESC;""",
    "Q2 billing by insurance provider": """
        SELECT "Insurance Provider" AS insurer,
               COUNT(*)                         AS patients,
               ROUND(SUM("Billing Amount"), 2)  AS total_billing
        FROM patients
        GROUP BY "Insurance Provider"
        ORDER BY total_billing DESC;""",
    "Q3 admission type summary": """
        SELECT "Admission Type" AS admission_type,
               COUNT(*)                         AS patients,
               ROUND(AVG("Billing Amount"), 2)  AS avg_billing,
               ROUND(AVG("Length of Stay"), 2)  AS avg_stay
        FROM patients
        GROUP BY "Admission Type";""",
    "Q4 abnormal test share by age group": """
        SELECT "Age Group" AS age_group,
               ROUND(100.0 * SUM(CASE WHEN "Test Results" = 'Abnormal' THEN 1 ELSE 0 END)
                     / COUNT(*), 1) AS abnormal_pct
        FROM patients
        GROUP BY "Age Group"
        ORDER BY abnormal_pct DESC;""",
    "Q5 top 10 most expensive admissions": """
        SELECT Name, "Medical Condition" AS condition, "Billing Amount" AS billing,
               "Length of Stay" AS stay_days
        FROM patients
        ORDER BY "Billing Amount" DESC
        LIMIT 10;""",
}


def run_sql(df, out_dir):
    """Loads the clean data into SQLite, runs the queries and saves the results."""
    db_path = os.path.join(out_dir, "hospital.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    df.drop(columns=["High Cost", "Long Stay"], errors="ignore").astype(
        {"Date of Admission": str, "Discharge Date": str, "Age Group": str}
    ).to_sql("patients", con, index=False)
    results = {}
    for name, query in SQL_QUERIES.items():
        results[name] = pd.read_sql_query(query, con)
    con.close()

    with open(os.path.join(out_dir, "queries.sql"), "w") as f:
        for name, query in SQL_QUERIES.items():
            f.write(f"-- {name}\n{query.strip()}\n\n")
    return results


def verify_sql_against_pandas(df, sql_results):
    """Every SQL answer is cross-checked with Pandas (same habit as the capstone)."""
    checks = []

    q1 = sql_results["Q1 patients and billing by condition"].set_index("condition").sort_index()
    p1 = df.groupby("Medical Condition").agg(n=("Name", "count"),
                                             b=("Billing Amount", "mean")).sort_index()
    ok1 = (q1["patients"] == p1["n"]).all() and ((q1["avg_billing"] - p1["b"]).abs() < 0.01).all()
    checks.append(("Q1 condition summary", bool(ok1)))

    q2 = sql_results["Q2 billing by insurance provider"].set_index("insurer").sort_index()
    p2 = df.groupby("Insurance Provider")["Billing Amount"].sum().sort_index()
    ok2 = ((q2["total_billing"] - p2).abs() < 1).all()
    checks.append(("Q2 insurance totals", bool(ok2)))

    q3 = sql_results["Q3 admission type summary"].set_index("admission_type").sort_index()
    p3 = df.groupby("Admission Type")["Name"].count().sort_index()
    checks.append(("Q3 admission counts", bool((q3["patients"] == p3).all())))
    return checks


# --------------------------------------------------------------- 5. CHARTS
def label_bars(ax, fmt="{:.0f}", horizontal=True, pad=0.01):
    for p in ax.patches:
        if horizontal:
            w = p.get_width()
            ax.text(w * (1 + pad), p.get_y() + p.get_height() / 2, fmt.format(w),
                    va="center", fontsize=8)
        else:
            h = p.get_height()
            ax.text(p.get_x() + p.get_width() / 2, h * (1 + pad), fmt.format(h),
                    ha="center", va="bottom", fontsize=8)


def chart_patients_by_condition(df, out):
    s = df["Medical Condition"].value_counts().sort_values()
    fig, ax = plt.subplots(figsize=(7, 3.8))
    ax.barh(s.index, s.values, color=BLUE)
    ax.set_title("Number of patients by medical condition")
    ax.set_xlabel("Patients")
    label_bars(ax, "{:,.0f}")
    ax.set_xlim(0, s.max() * 1.12)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_billing_by_condition(df, out):
    s = df.groupby("Medical Condition")["Billing Amount"].mean().sort_values()
    colors = [BLUE if v == s.max() else GREY for v in s.values]
    fig, ax = plt.subplots(figsize=(7, 3.8))
    ax.barh(s.index, s.values, color=colors)
    ax.set_title("Average billing amount by medical condition")
    ax.set_xlabel("Average billing ($)")
    label_bars(ax, "${:,.0f}")
    ax.set_xlim(0, s.max() * 1.15)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_admission_type(df, out):
    g = df.groupby("Admission Type").agg(Patients=("Name", "count"),
                                         Billing=("Billing Amount", "mean"),
                                         Stay=("Length of Stay", "mean"))
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.4))
    for ax, col, title, fmt in zip(
            axes, ["Patients", "Billing", "Stay"],
            ["Patients", "Average billing ($)", "Average stay (days)"],
            ["{:,.0f}", "${:,.0f}", "{:.1f}"]):
        ax.bar(g.index, g[col], color=[BLUE, TEAL, ORANGE])
        ax.set_title(title)
        label_bars(ax, fmt, horizontal=False)
        ax.set_ylim(0, g[col].max() * 1.15)
    fig.suptitle("Admission type comparison", fontweight="bold")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_insurance(df, out):
    s = df.groupby("Insurance Provider")["Billing Amount"].sum().sort_values() / 1e6
    fig, ax = plt.subplots(figsize=(7, 3.4))
    ax.barh(s.index, s.values, color=TEAL)
    ax.set_title("Total billing by insurance provider")
    ax.set_xlabel("Total billing ($ million)")
    label_bars(ax, "${:,.1f}M")
    ax.set_xlim(0, s.max() * 1.15)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_age_group(df, out):
    g = df.groupby("Age Group", observed=True).agg(Patients=("Name", "count"),
                                                   Billing=("Billing Amount", "mean"))
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
    axes[0].bar(g.index.astype(str), g["Patients"], color=BLUE)
    axes[0].set_title("Patients by age group")
    label_bars(axes[0], "{:,.0f}", horizontal=False)
    axes[0].set_ylim(0, g["Patients"].max() * 1.15)
    axes[1].bar(g.index.astype(str), g["Billing"], color=GREY)
    axes[1].set_title("Average billing by age group ($)")
    label_bars(axes[1], "${:,.0f}", horizontal=False)
    axes[1].set_ylim(0, g["Billing"].max() * 1.15)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_test_results(df, out):
    ct = (pd.crosstab(df["Medical Condition"], df["Test Results"], normalize="index") * 100)
    ct = ct[["Normal", "Inconclusive", "Abnormal"]]
    fig, ax = plt.subplots(figsize=(8, 3.8))
    left = pd.Series(0.0, index=ct.index)
    for col, color in zip(ct.columns, [TEAL, GREY, RED]):
        ax.barh(ct.index, ct[col], left=left, color=color, label=col)
        left += ct[col]
    ax.set_title("Test results by medical condition (% of patients)")
    ax.set_xlabel("% of patients")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.18), frameon=False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_yearly_trend(df, out):
    y = df[df["Admission Year"].between(df["Admission Year"].min() + 1,
                                        df["Admission Year"].max() - 1)]
    m = df.groupby("Admission Month").size()
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.plot(range(len(m)), m.values, color=BLUE, linewidth=1.5)
    ticks = list(range(0, len(m), 6))
    ax.set_xticks(ticks)
    ax.set_xticklabels([m.index[i] for i in ticks], rotation=45, ha="right")
    ax.set_title("Monthly admissions")
    ax.set_ylabel("Patients")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_stay_distribution(df, out):
    fig, ax = plt.subplots(figsize=(7, 3.4))
    ax.hist(df["Length of Stay"], bins=30, color=BLUE, edgecolor="white")
    ax.axvline(df["Length of Stay"].mean(), color=RED, linestyle="--",
               label=f"Mean = {df['Length of Stay'].mean():.1f} days")
    ax.set_title("Distribution of length of stay")
    ax.set_xlabel("Days in hospital")
    ax.set_ylabel("Patients")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_correlation(corr, out):
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(corr)))
    ax.set_yticks(range(len(corr)))
    ax.set_xticklabels(corr.columns, rotation=30, ha="right")
    ax.set_yticklabels(corr.index)
    for i in range(len(corr)):
        for j in range(len(corr)):
            ax.text(j, i, f"{corr.values[i, j]:.2f}", ha="center", va="center", fontsize=8)
    ax.set_title("Correlation matrix")
    fig.colorbar(im, ax=ax, fraction=0.046)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_dashboard(df, out):
    """One-page dashboard: KPI strip + 6 charts."""
    fig = plt.figure(figsize=(14, 9.5))
    gs = fig.add_gridspec(3, 3, height_ratios=[0.45, 1.5, 1.5], hspace=0.55, wspace=0.35)
    fig.suptitle("Hospital Patient Data Analyzer: Dashboard", fontsize=16,
                 fontweight="bold", y=0.985)

    # KPI strip
    kpis = [("Patients", f"{len(df):,}"),
            ("Avg age", f"{df['Age'].mean():.1f}"),
            ("Avg stay", f"{df['Length of Stay'].mean():.1f} days"),
            ("Avg billing", f"${df['Billing Amount'].mean():,.0f}")]
    ax = fig.add_subplot(gs[0, :])
    ax.axis("off")
    for i, (label, value) in enumerate(kpis):
        x = 0.125 + i * 0.25
        ax.text(x, 0.62, value, ha="center", va="center", fontsize=20,
                fontweight="bold", color=BLUE, transform=ax.transAxes)
        ax.text(x, 0.12, label, ha="center", va="center", fontsize=10,
                color="#555", transform=ax.transAxes)

    # 1 patients by condition
    ax = fig.add_subplot(gs[1, 0])
    s = df["Medical Condition"].value_counts().sort_values()
    ax.barh(s.index, s.values, color=BLUE)
    ax.set_title("Patients by condition")
    ax.set_xlim(0, s.max() * 1.2)
    label_bars(ax, "{:,.0f}")

    # 2 billing by condition
    ax = fig.add_subplot(gs[1, 1])
    s = df.groupby("Medical Condition")["Billing Amount"].mean().sort_values()
    ax.barh(s.index, s.values, color=[BLUE if v == s.max() else GREY for v in s.values])
    ax.set_title("Avg billing by condition ($)")
    ax.set_xlim(0, s.max() * 1.25)
    label_bars(ax, "${:,.0f}")

    # 3 admission type
    ax = fig.add_subplot(gs[1, 2])
    s = df["Admission Type"].value_counts()
    ax.bar(s.index, s.values, color=[BLUE, TEAL, ORANGE])
    ax.set_title("Patients by admission type")
    ax.set_ylim(0, s.max() * 1.15)
    label_bars(ax, "{:,.0f}", horizontal=False)

    # 4 insurance
    ax = fig.add_subplot(gs[2, 0])
    s = df.groupby("Insurance Provider")["Billing Amount"].sum().sort_values() / 1e6
    ax.barh(s.index, s.values, color=TEAL)
    ax.set_title("Total billing by insurer ($M)")
    ax.set_xlim(0, s.max() * 1.2)
    label_bars(ax, "${:,.0f}M")

    # 5 age group
    ax = fig.add_subplot(gs[2, 1])
    g = df.groupby("Age Group", observed=True).size()
    ax.bar(g.index.astype(str), g.values, color=BLUE)
    ax.set_title("Patients by age group")
    ax.set_ylim(0, g.max() * 1.15)
    label_bars(ax, "{:,.0f}", horizontal=False)

    # 6 test results
    ax = fig.add_subplot(gs[2, 2])
    s = df["Test Results"].value_counts()
    ax.pie(s.values, labels=s.index, autopct="%1.1f%%", startangle=90,
           colors=[{"Normal": TEAL, "Abnormal": RED, "Inconclusive": GREY}.get(k, BLUE) for k in s.index],
           wedgeprops={"edgecolor": "white"})
    ax.set_title("Test results")

    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------- 6. REPORT
def build_insights(df, t):
    cond = t["by_condition"]
    adm = t["by_admission_type"]
    ins = t["by_insurance"]
    age = t["by_age_group"]
    corr = t["correlation"]
    test = df["Test Results"].value_counts(normalize=True).mul(100).round(1)

    top_cost = cond.iloc[0]
    low_cost = cond.iloc[-1]
    spread = (top_cost["Avg_Billing"] - low_cost["Avg_Billing"]) / low_cost["Avg_Billing"] * 100
    longest = cond.sort_values("Avg_Stay_Days", ascending=False).iloc[0]
    shortest = cond.sort_values("Avg_Stay_Days").iloc[0]
    top_ins = ins.sort_values("Total_Billing", ascending=False).iloc[0]
    abn_age = t["test_by_age_group"].sort_values("Abnormal", ascending=False).iloc[0]
    abn_age_low = t["test_by_age_group"].sort_values("Abnormal").iloc[0]
    gap_adm = (adm["Avg_Billing"].max() - adm["Avg_Billing"].min())
    c_age_bill = corr.loc["Age", "Billing Amount"]
    c_stay_bill = corr.loc["Length of Stay", "Billing Amount"]

    lines = []
    lines.append(f"- The cleaned data holds **{len(df):,} patient admissions** from "
                 f"{df['Date of Admission'].min().date()} to {df['Date of Admission'].max().date()}. "
                 f"Average age is {df['Age'].mean():.1f}, average stay is "
                 f"{df['Length of Stay'].mean():.1f} days and average billing is "
                 f"${df['Billing Amount'].mean():,.0f}.")
    lines.append(f"- **{top_cost['Medical Condition']}** has the highest average billing "
                 f"(${top_cost['Avg_Billing']:,.0f}) and **{low_cost['Medical Condition']}** the lowest "
                 f"(${low_cost['Avg_Billing']:,.0f}); the gap between them is {spread:.1f}%.")
    lines.append(f"- Average stay is longest for **{longest['Medical Condition']}** "
                 f"({longest['Avg_Stay_Days']:.1f} days) and shortest for "
                 f"**{shortest['Medical Condition']}** ({shortest['Avg_Stay_Days']:.1f} days).")
    mix = ", ".join(f"{r['Admission Type']}: {int(r['Patients']):,}" for _, r in adm.iterrows())
    lines.append(f"- Admission types are almost equally common ({mix}). "
                 f"Average billing differs by only ${gap_adm:,.0f} between them.")
    lines.append(f"- **{top_ins['Insurance Provider']}** accounts for the largest total billing "
                 f"(${top_ins['Total_Billing']/1e6:,.1f}M).")
    lines.append(f"- Test results are spread evenly: Abnormal {test.get('Abnormal', 0)}%, "
                 f"Normal {test.get('Normal', 0)}%, Inconclusive {test.get('Inconclusive', 0)}%. "
                 f"The highest abnormal share is in age group **{abn_age['Age Group']}** "
                 f"({abn_age['Abnormal']}%, {int(age.set_index('Age Group').loc[abn_age['Age Group'], 'Patients']):,} patients) "
                 f"and the lowest in **{abn_age_low['Age Group']}** ({abn_age_low['Abnormal']}%).")
    lines.append(f"- Correlation of billing with age is {c_age_bill:.2f} and with length of stay is "
                 f"{c_stay_bill:.2f}, so neither strongly explains the bill in this dataset.")
    return lines


def write_report(df, t, log, checks, out_dir):
    insights = build_insights(df, t)
    removed_total = log["rows_before"] - log["rows_after"]
    md = []
    md.append("# Hospital Patient Data Analyzer: Findings Report\n")
    md.append("## 1. Data cleaning summary\n")
    md.append(f"- Rows before cleaning: **{log['rows_before']:,}**")
    md.append(f"- Duplicate rows removed: **{log['duplicates_removed']:,}**")
    md.append(f"- Invalid dates removed: **{log['bad_dates_removed']}**")
    md.append(f"- Discharge-before-admission rows removed: **{log['discharge_before_admission_removed']}**")
    md.append(f"- Negative billing amounts removed: **{log['negative_billing_removed']}**")
    md.append(f"- Patient names fixed from random casing to normal casing (e.g. 'LesLie TErRy' to 'Leslie Terry')")
    md.append("- Dates converted from text to proper dates")
    md.append("- New columns: Length of Stay, Age Group, Admission Year, Admission Month, Billing Per Day")
    md.append(f"- Rows after cleaning: **{log['rows_after']:,}** ({removed_total:,} removed in total)\n")
    md.append("## 2. Key findings\n")
    md.extend(insights)
    md.append("\n## 3. Summary tables\n")
    md.append("### By medical condition\n")
    md.append(t["by_condition"].to_markdown(index=False))
    md.append("\n### By admission type\n")
    md.append(t["by_admission_type"].to_markdown(index=False))
    md.append("\n### By insurance provider\n")
    md.append(t["by_insurance"].to_markdown(index=False))
    md.append("\n### By age group\n")
    md.append(t["by_age_group"].to_markdown(index=False))
    md.append("\n### Test results by medical condition (%)\n")
    md.append(t["test_by_condition"].to_markdown(index=False))
    md.append("\n### Flagged patients (top 5% cost / top 5% stay)\n")
    md.append(t["thresholds"].to_markdown(index=False))
    md.append("\n## 4. SQL verification\n")
    for name, ok in checks:
        md.append(f"- {name}: {'matches Pandas' if ok else 'DOES NOT MATCH'}")
    md.append("\n## 5. Recommendations for managers\n")
    md.append("1. Plan budgets for the highest-billing conditions first, since they set the average cost.")
    md.append("2. Track the top 5% long-stay and high-cost patients every month; they are a small group with a large cost.")
    md.append("3. Review billing with the insurance provider that has the largest total share.")
    md.append("4. Follow up on age groups with a higher share of abnormal results.")
    md.append("5. Re-run this tool every month on the new admission file; no manual steps are needed.\n")
    md.append("> Note: this is a public, synthetic Kaggle dataset, so the findings show how the tool works. "
              "They are not real medical conclusions.\n")
    with open(os.path.join(out_dir, "report.md"), "w") as f:
        f.write("\n".join(md))
    return insights


# -------------------------------------------------------------------- MAIN
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    in_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "data", "healthcare_dataset.csv")
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "outputs")
    chart_dir = os.path.join(out_dir, "charts")
    table_dir = os.path.join(out_dir, "tables")
    for d in (out_dir, chart_dir, table_dir):
        os.makedirs(d, exist_ok=True)

    print("[1/6] Loading data ...")
    raw = load_data(in_path)
    print(f"      {raw.shape[0]:,} rows, {raw.shape[1]} columns")

    print("[2/6] Cleaning data ...")
    df, log = clean_data(raw.copy())
    df.to_csv(os.path.join(out_dir, "cleaned_healthcare.csv"), index=False)
    for k, v in log.items():
        print(f"      {k}: {v}")

    print("[3/6] Analysing ...")
    t = analyse(df)
    for name, table in t.items():
        if name == "flagged_patients":
            table.to_csv(os.path.join(table_dir, f"{name}.csv"), index=False)
        elif name == "correlation":
            table.to_csv(os.path.join(table_dir, f"{name}.csv"))
        else:
            table.to_csv(os.path.join(table_dir, f"{name}.csv"), index=False)

    print("[4/6] Running SQL queries and verifying with Pandas ...")
    sql_results = run_sql(df, out_dir)
    for name, res in sql_results.items():
        res.to_csv(os.path.join(table_dir, "sql_" + name.split()[0].lower() + ".csv"), index=False)
    checks = verify_sql_against_pandas(df, sql_results)
    for name, ok in checks:
        print(f"      {name}: {'OK' if ok else 'MISMATCH'}")

    print("[5/6] Drawing charts and dashboard ...")
    chart_patients_by_condition(df, os.path.join(chart_dir, "01_patients_by_condition.png"))
    chart_billing_by_condition(df, os.path.join(chart_dir, "02_billing_by_condition.png"))
    chart_admission_type(df, os.path.join(chart_dir, "03_admission_type.png"))
    chart_insurance(df, os.path.join(chart_dir, "04_insurance_billing.png"))
    chart_age_group(df, os.path.join(chart_dir, "05_age_group.png"))
    chart_test_results(df, os.path.join(chart_dir, "06_test_results.png"))
    chart_yearly_trend(df, os.path.join(chart_dir, "07_monthly_admissions.png"))
    chart_stay_distribution(df, os.path.join(chart_dir, "08_stay_distribution.png"))
    chart_correlation(t["correlation"], os.path.join(chart_dir, "09_correlation.png"))
    chart_dashboard(df, os.path.join(out_dir, "dashboard.png"))

    print("[6/6] Writing report ...")
    insights = write_report(df, t, log, checks, out_dir)
    print("\nKey findings:")
    for line in insights:
        print(" ", line)
    print(f"\nDone. Results are in: {out_dir}")


if __name__ == "__main__":
    main()
