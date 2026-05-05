#In[]:
import pandas as pd
import matplotlib.pyplot as plt

csv_file = "filter_power_2.csv"

plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})
df = pd.read_csv(csv_file)

df = df.rename(columns={"Power (µW)": "Power"})

for col in ["Wg", "Ww", "DF", "AS", "Power"]:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

cic = df[df["Filter"] == "CIC"].copy()
ses = df[df["Filter"] == "SES"].copy()

# Normalize DF to OSR units
OSR = 200
cic["DF_norm"] = cic["DF"] / OSR

# ------------------------------------------------------------
# Extrapolate DF=0.5xOSR and DF=1.0xOSR for AS < 6
# using stage-6 scaling relative to the lowest DF curve
# ------------------------------------------------------------
base_df = 0.25
target_dfs = [0.5, 1.0]
ref_as = 6

base_ref_power = cic.loc[
    (cic["DF_norm"] == base_df) & (cic["AS"] == ref_as),
    "Power"
].iloc[0]

extra_rows = []

for target_df in target_dfs:
    target_ref_power = cic.loc[
        (cic["DF_norm"] == target_df) & (cic["AS"] == ref_as),
        "Power"
    ].iloc[0]

    scale = target_ref_power / base_ref_power

    base_curve = cic[
        (cic["DF_norm"] == base_df) &
        (cic["AS"] < ref_as)
    ].copy()

    base_curve["DF_norm"] = target_df
    base_curve["DF"] = target_df * OSR
    base_curve["Power"] = base_curve["Power"] * scale
    base_curve["Extrapolated"] = True

    extra_rows.append(base_curve)

cic["Extrapolated"] = False
cic = pd.concat([cic] + extra_rows, ignore_index=True)

ses_avg = ses["Power"].mean()
ses_min = ses["Power"].min()
ses_max = ses["Power"].max()

fig, ax = plt.subplots(figsize=(5, 2.5))

ax.axhspan(
    ses_min,
    ses_max,
    alpha=0.18,
    label="SES min–max",
    color="green"
)

ax.axhline(
    ses_avg,
    linewidth=2.5,
    linestyle="--",
    label="SES average",
    color="green"
)

for df_val, group in sorted(cic.groupby("DF_norm")):
    group = group.sort_values("AS")

    real = group[~group["Extrapolated"]]
    ext  = group[group["Extrapolated"]]

    alpha = min(1.0, 0.25 / df_val)

    ax.plot(
        group["AS"],
        group["Power"],
        alpha=alpha,
        color="red"
    )

    ax.scatter(
        real["AS"],
        real["Power"],
        s=75,
        label=f"R={df_val:1.2g}×OSR",
        color="red",
        alpha=alpha
    )

    ax.scatter(
        ext["AS"],
        ext["Power"],
        s=75,
        color="red",
        alpha=alpha
    )

ax.axhline(20, linestyle='--', color='gray', label="DSM @ 1 MHz", zorder=0)
ax.axhline(4.7, linestyle='--', color='lightgray', label="DSM @ 50 kHz", zorder=0)

ax.set_xlabel("Active stages (AS)")
ax.set_ylabel("Power (µW)")
ax.set_title(r"$V_{DD}=1.0\,V, f_{sys}=8\,MHz, f_s=1\,MHz$")
ax.set_yscale('log')
ax.set_ylim(1,40)
ax.set_yticks([1,10])
ax.set_yticklabels(["1"," 10"])
ax.set_xticks(sorted(df["AS"].dropna().unique()))
ax.grid(True, alpha=0.3)
ax.legend(fontsize=8, bbox_to_anchor=(1.02, 1),borderaxespad=0, frameon=False)

plt.tight_layout()
plt.savefig("./SES_vs_CIC_power.png", dpi=400)
plt.show()

print(f"SES average = {ses_avg:.2f} µW")
print(f"SES range   = {ses_min:.2f}–{ses_max:.2f} µW")