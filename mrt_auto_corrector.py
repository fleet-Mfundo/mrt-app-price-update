import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime
import io

st.set_page_config(page_title="MRT Export Table Fix", layout="wide")
st.title("🚗 MRT Export Table Correction Tool")
# --- Require name first ---
user = st.text_input("Enter your full name to continue:")
if not user.strip():
    st.warning("⚠️ Please enter your name first.")
    st.stop()

# --- Upload file ---
uploaded = st.file_uploader("Upload Excel file containing the 'Export' sheet", type=["xlsx"])
if not uploaded:
    st.stop()

# --- Read Export sheet ---
try:
    df = pd.read_excel(uploaded, sheet_name="Export", engine="openpyxl")
    df.columns = df.columns.map(str)

except Exception:
    st.error("❌ Could not find 'Export' sheet. Please ensure the sheet name is 'Export'.")
    st.stop()

st.success("✅ 'Export' sheet loaded successfully.")
st.subheader("Raw Preview (first 15 rows)")
st.dataframe(df.head(15))

# --- Identify key columns ---
id_cols = ["A", "B", "C", "D", "E", "F"]
avail_id_cols = [c for c in id_cols if c in df.columns]
if not avail_id_cols:
    st.error("No identifier columns (A–F) found.")
    st.stop()

id_col = st.selectbox("Select identifier column (e.g. E = Model)", avail_id_cols)
unique_ids = sorted(df[id_col].dropna().unique().tolist())
chosen_id = st.selectbox("Select line identifier value", unique_ids)

# --- Filter selected line ---
subset = df[df[id_col] == chosen_id].copy()
subset.reset_index(drop=True, inplace=True)

if subset.empty:
    st.error("No data found for the selected identifier.")
    st.stop()

st.success(f"Filtered to line: {chosen_id}")
st.dataframe(subset.head(10))

# --- Detect numeric month columns ---
idx_col = "index"
subset[idx_col] = pd.to_numeric(subset[idx_col], errors="coerce")
month_cols = [c for c in subset.columns if str(c).strip().isdigit()]
if not month_cols:
    st.error("No numeric month columns found.")
    st.stop()

for c in month_cols:
    subset[c] = pd.to_numeric(subset[c], errors="coerce")

km_vals = subset[idx_col].tolist()

# --- Inputs ---
month_in = st.number_input("Customer months", min_value=1, max_value=360, value=45)
km_in = st.number_input("Customer kilometres", min_value=0, max_value=1_000_000, value=60000, step=5000)

def ceiling_choice(x, choices):
    for c in sorted(choices):
        if c >= x:
            return c
    return sorted(choices)[-1]

month_choice = ceiling_choice(int(month_in), [int(m) for m in month_cols])
km_choice = ceiling_choice(int(km_in), km_vals)
st.info(f"Ceiling lookup → Months {month_in} → {month_choice}, KM {km_in} → {km_choice}")

# --- Choose method ---
method = st.radio("Select correction method:", ["Average-based (xₙ rule)", "Uplift-based (%)"], index=0)
uplift_pct = st.number_input("Uplift % (for Method 2 only)", min_value=0.1, max_value=10.0, value=1.0, step=0.1)

apply_update = st.checkbox("✅ Apply update directly to table (preview below will reflect change)", value=True)

if st.button("🔍 Detect & Preview"):
    col = str(month_choice)
    std_col = subset[col].to_numpy(dtype=float)
    n = len(std_col)

    start_idx = next((i for i, v in enumerate(km_vals) if v >= km_choice), len(km_vals)-1)
    start_val = std_col[start_idx]

    # Detect flat plateau
    end_idx = start_idx
    while end_idx + 1 < n and np.isclose(std_col[end_idx + 1], start_val, equal_nan=False):
        end_idx += 1

    # Find next higher xₙ
    next_idx = None
    for k in range(end_idx + 1, n):
        if std_col[k] > start_val:
            next_idx = k
            break

    if next_idx is None:
        st.warning("No higher value found below plateau.")
        target_val = start_val * (1 + uplift_pct / 100)
    else:
        target_val = std_col[next_idx]

    st.write(f"Target reference (xₙ): {target_val} (KM={km_vals[next_idx] if next_idx else 'End'})")

    std_updated = std_col.copy()
    changes = []

    if method == "Average-based (xₙ rule)":
        for i in range(start_idx + 1, end_idx + 1):
            old = std_col[i]
            new = round((old + target_val) / 2, 2)
            new = max(new, std_col[i - 1])  # enforce monotonic
            if not np.isclose(old, new):
                std_updated[i] = new
                changes.append([km_vals[i], month_choice, old, new, "Average-based correction"])
    elif method == "Uplift-based (%)":
        for i in range(start_idx, end_idx + 1):
            old = std_col[i]
            new = round(old * (1 + uplift_pct / 100), 2)
            new = max(new, std_col[i - 1] if i > 0 else new)
            if not np.isclose(old, new):
                std_updated[i] = new
                changes.append([km_vals[i], month_choice, old, new, f"Uplift {uplift_pct}%"])

    if apply_update:
        subset[col] = std_updated

    # --- Audit log ---
    log = pd.DataFrame(changes, columns=["Kilometres", "Month", "Old_Value", "New_Value", "Reason"])
    log["User"] = user
    log["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    st.subheader("Preview of Updated Rows")
    st.dataframe(subset.iloc[max(0, start_idx - 2):min(n, end_idx + 4)][[idx_col, col]])
    st.write(f"✅ Rows changed: {len(changes)}  (Method: {method})")

    # --- Merge back into full dataset ---
    corrected_df = df.copy()
    mask = corrected_df[id_col] == chosen_id
    rows_full = corrected_df[mask].shape[0]
    rows_subset = subset.shape[0]
    if rows_full != rows_subset:
        st.error("Row count mismatch when merging back; aborting.")
    else:
        # assifn corrected values by position - this cannot duplicate or miss rows
        corrected_df.loc[mask, col] = subset[col].values

    # Convert numeric column headers to int for Excel output
    def cast_headers(df):
        cols = []
        for c in df.columns:
            try:
                cols.append(int(c))
            except ValueError:
                cols.append(c)
        df.columns = cols
        return df

    corrected_df = cast_headers(corrected_df)

    # --- Export full dataset ---
    out_xlsx = io.BytesIO()
    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
        corrected_df.to_excel(writer, index=False, sheet_name="Export")
    out_xlsx.seek(0)

    st.download_button(
        "⬇️ Download Full Corrected Export Sheet",
        data=out_xlsx,
        file_name=f"Full_Corrected_Export_{method.replace(' ', '_')}.xlsx",
    )

    st.download_button(
        "⬇️ Download Audit Log (CSV)",
        data=log.to_csv(index=False),
        file_name="Audit_Log.csv",
        mime="text/csv",
    )
    st.subheader("Audit Log Preview")
    st.dataframe(log)
    if log.empty:
        st.info("No changes were made; audit log is empty.")


