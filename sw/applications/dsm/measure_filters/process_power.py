#In[]:

import pandas as pd
import matplotlib.pyplot as plt

csv_file = "filter_power_2.csv"

plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})
df = pd.read_csv(csv_file)

# Expected columns:
# Filter, Combs, Wg, Ww, DF, AS, Power (µW)

df = df.rename(columns={"Power (µW)": "Power"})

for col in ["Wg", "Ww", "DF", "AS", "Power"]:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

cic = df[df["Filter"] == "CIC"].copy()
ses = df[df["Filter"] == "SES"].copy()

ses_avg = ses["Power"].mean()
ses_min = ses["Power"].min()
ses_max = ses["Power"].max()

fig, ax = plt.subplots(figsize=(5, 3))

# SES average + min/max band
ax.axhspan(
    ses_min,
    ses_max,
    alpha=0.18,
    label=f"SES min–max",
    color='green'
)

ax.axhline(
    ses_avg,
    linewidth=2.5,
    linestyle="--",
    label=f"SES average",
    color='green'
)

# CIC points grouped by DF
for df_val, group in sorted(cic.groupby("DF")):
    group = group.sort_values("AS")

    ax.scatter(
        group["AS"],
        group["Power"],
        s=75,
        # alpha=0.85,
        label=f"CIC DF={int(df_val)/200}× OSR",
        color='red',
        alpha=32/df_val
    )

    if len(group) > 1:
        ax.plot(
            group["AS"],
            group["Power"],
            alpha=32/df_val,
            color='red'
        )

# Annotate CIC comb count if != 1
# for _, r in cic.iterrows():
#     if pd.notna(r["Combs"]) and r["Combs"] != 1:
#         ax.annotate(
#             f"C{int(r['Combs'])}",
#             (r["AS"], r["Power"]),
#             textcoords="offset points",
#             xytext=(4, 4),
#             fontsize=8,
#         )

ax.set_xlabel("Active stages (AS)")
ax.set_ylabel("Power (µW)")
ax.set_title(r"$V_{DD}=1.0\,V, f_{sys}=8\,MHz, f_s=1\,MHz$")
ax.set_xticks(sorted(df["AS"].dropna().unique()))
ax.grid(True, alpha=0.3)
ax.legend(fontsize=8)

plt.tight_layout()
plt.show()

print(f"SES average = {ses_avg:.2f} µW")
print(f"SES range   = {ses_min:.2f}–{ses_max:.2f} µW")