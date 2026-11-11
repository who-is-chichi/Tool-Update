"""Step 1: read the four exported files and clean them. Every change is written to the log with a reason."""
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ToolError, pattern_to_strptime

REQUIRED = {
    "orders": ["order_id", "customer_id", "order_date", "order_type", "product_category",
               "subtotal_usd", "discount_usd", "shipping_charged_usd", "order_total_usd"],
    "customers": ["customer_id", "acquisition_date", "acquisition_channel"],
    "marketing_spend": ["month", "channel", "spend_usd"],
    "cs_notes": ["note_id", "customer_id", "note_date", "note_text"],
}
AMOUNTS = ["subtotal_usd", "discount_usd", "shipping_charged_usd", "order_total_usd"]
# The spend file's month column: YYYY-MM as exported; the others appear after re-saving the file in Excel.
SPEND_MONTH_FORMATS = ["%Y-%m", "%Y/%m", "%Y-%m-%d", "%m/%d/%Y", "%b-%y", "%b %Y", "%B %Y"]
# Money written as text: optional brackets (negative), minus sign and $, digits with or without thousands commas.
_AMOUNT = r"\(?-?\$?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?"


def find_file(folder, name):
    """Find e.g. orders.csv in the folder, also with a prefix ('2026-11-orders.csv') or a browser copy number
    ('orders (1).csv'). Hidden files such as '._orders.csv' are ignored."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ToolError(f"The folder '{folder}' doesn't exist. Check the folder name in the sidebar.")
    pat = re.compile(rf"(?:.*[-_ ])?{re.escape(name)}(?: \(\d+\))?")
    hits = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".csv"
            and not p.name.startswith(".") and pat.fullmatch(p.stem.lower())]
    if not hits:
        raise ToolError(f"Could not find {name}.csv in the folder '{folder}'.")
    if len(hits) > 1:
        raise ToolError(f"Found more than one {name} file in '{folder}': {sorted(h.name for h in hits)}. Keep only one.")
    return hits[0]


def _read_csv(path, **kw):
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""], **kw)


def read(folder, name, log):
    path = find_file(folder, name)
    encoding = "utf-8-sig"
    try:
        df = _read_csv(path, encoding=encoding)
    except UnicodeDecodeError:
        encoding = "cp1252"
        df = _read_csv(path, encoding=encoding)
        log.rule(path.name, "Text encoding", 0, "read as Windows text (cp1252)",
                 "The file was not saved as UTF-8, e.g. after re-saving it in Excel")
    except pd.errors.EmptyDataError:
        raise ToolError(f"{path.name} is empty. Check the export.")
    if len(df.columns) == 1 and ";" in str(df.columns[0]):
        df = _read_csv(path, encoding=encoding, sep=";")
        log.rule(path.name, "Column separator", 0, "read with ';' between columns",
                 "The file uses semicolons, a European Excel setting")
    df.columns = [str(c).strip().lower() for c in df.columns]          # columns are matched by name, any order
    missing = [c for c in REQUIRED[name] if c not in df.columns]
    if missing:
        raise ToolError(f"{path.name} is missing the column(s) {missing}. Check the export.")
    extra = [c for c in df.columns if c not in REQUIRED[name]]
    if extra:
        log.rule(path.name, "Extra columns", 0, f"ignored: {extra}", "Not needed for the metrics")
    return df


def to_amount(values):
    """Text -> number. Plain numbers as they are; '$1,234.56', '1 234.56' and '(12.50)' (= -12.50) are read too.
    Anything else, e.g. '12,50' with a decimal comma, stays unreadable rather than being guessed.
    Returns the numbers and how many values needed this extra reading."""
    text = values.astype("string").str.strip()
    as_float = lambda x: pd.Series(pd.to_numeric(x, errors="coerce").to_numpy(dtype="float64", na_value=np.nan),
                                   index=x.index)
    out = as_float(text)
    todo = (out.isna() & text.notna() & (text != "")).fillna(False).astype(bool)
    if not todo.any():
        return out, 0
    t = text[todo].str.replace(r"\s", "", regex=True)
    ok = t.str.fullmatch(_AMOUNT).fillna(False).astype(bool)
    neg = (t.str.startswith("(") & t.str.endswith(")")).fillna(False).astype(bool)
    num = as_float(t.str.replace(r"[()$,]", "", regex=True).where(ok))
    num = num.where(~neg, -num)
    out.loc[todo] = num
    return out, int(num.notna().sum())


def parse_dates(values, file, column, log, settings, blank_note="skipped"):
    """Try each date pattern in settings (date_formats) in turn; report rows that match none. Stop if too many fail."""
    out = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    text = values.astype("string").str.strip()
    blank = (text.isna() | (text == "")).fillna(True).astype(bool)
    text = text.mask(blank)
    used = {}
    for pattern in settings["date_formats"]:
        todo = out.isna() & text.notna()
        if not todo.any():
            break
        parsed = pd.to_datetime(text[todo], format=pattern_to_strptime(pattern), errors="coerce")
        ok = parsed.notna()
        if ok.any():
            out.loc[parsed.index[ok]] = parsed[ok]
            used[pattern] = int(ok.sum())
    if len(used) > 1:
        log.rule(file, f"Mixed date formats in {column}", sum(list(used.values())[1:]),
                 f"read each format separately: {used}", "Two source systems write dates differently")
    if blank.any():
        log.rule(file, f"Blank {column}", blank.sum(), blank_note, "No date given")
    bad = out.isna() & text.notna()
    if bad.any():
        n = int(bad.sum())
        share = n / max(len(values), 1)
        examples = ", ".join(f"'{x}'" for x in text[bad].drop_duplicates().head(3))
        hint = "add its pattern (for example DD.MM.YYYY) to settings.xlsx > assumptions > date_formats"
        if share > settings["max_unreadable_share"]:
            raise ToolError(f"{file}: {n:,} values in '{column}' ({share:.1%}) are not dates in a known format, "
                            f"e.g. {examples}. If the export changed its date format, {hint}, then run again.")
        log.warn(f"{file}: {n:,} rows had unreadable dates in '{column}' (e.g. {examples}) and were skipped. "
                 f"If that format is real, {hint}.")
    return out


def clean_orders(raw, settings, log):
    f = "orders.csv"
    o = raw.copy()
    o["customer_id"] = o["customer_id"].astype("string").str.strip()
    o["order_id"] = o["order_id"].astype("string").str.strip()
    log.rule(f, "Trim spaces in IDs", (raw["customer_id"].astype("string") != o["customer_id"]).sum(),
             "removed leading/trailing spaces", "' C001' and 'C001' are the same customer; left in, they create phantom customers")

    test = o["customer_id"].str.startswith(settings["test_id_prefix"], na=False)
    log.rule(f, "Test records", test.sum(), "removed", f"Customer IDs starting '{settings['test_id_prefix']}' are internal tests")
    o = o[~test]

    o["order_type"] = o["order_type"].astype("string").str.strip().str.lower()
    known = o["order_type"].isin(["order", "refund"]).fillna(False).astype(bool)
    if (~known).any():
        log.warn(f"orders.csv: {int((~known).sum())} rows had an unknown order_type "
                 f"{sorted(o.loc[~known, 'order_type'].dropna().unique())} and were skipped.")
    o = o[known]
    log.rule(f, "Refund rows", (o["order_type"] == "refund").sum(), "kept separately: reduce revenue, never count as an order",
             "The data dictionary only mentions 'order', but refunds share the original order ID")

    text = o[["subtotal_usd", "order_total_usd"]].astype("string")
    fixed = 0
    for c in AMOUNTS:
        o[c], n = to_amount(o[c])
        fixed += n
    if fixed:
        log.rule(f, "Amounts written as text", fixed, "read as numbers", "Some exports write money as '$1,234.56'")
    bad_amt = o[["subtotal_usd", "order_total_usd"]].isna().any(axis=1)
    if bad_amt.any():
        n = int(bad_amt.sum())
        share = n / max(len(o), 1)
        examples = ", ".join(f"'{x}'" for x in text[bad_amt].stack().drop_duplicates().head(3))
        if share > settings["max_unreadable_share"]:
            raise ToolError(f"orders.csv: {n:,} rows ({share:.1%}) have amounts that are not numbers, e.g. {examples}. "
                            "Check the export.")
        log.warn(f"orders.csv: {n:,} rows had unreadable amounts (e.g. {examples}) and were skipped.")
    o = o[~bad_amt]
    o[["discount_usd", "shipping_charged_usd"]] = o[["discount_usd", "shipping_charged_usd"]].fillna(0)

    o["date"] = parse_dates(o["order_date"], f, "order_date", log, settings)
    o = o[o["date"].notna()]

    key = ["order_id", "customer_id", "date", "order_type", "product_category", "subtotal_usd",
           "discount_usd", "shipping_charged_usd", "order_total_usd"]
    dup = o.duplicated(subset=key)
    log.rule(f, "Exact duplicate rows", dup.sum(), "removed", "The same line exported twice would look like a repeat purchase")
    o = o[~dup]

    mismatch = (o["subtotal_usd"] - o["discount_usd"] + o["shipping_charged_usd"] - o["order_total_usd"]).abs() > 0.02
    if mismatch.any():
        log.rule(f, "Total does not equal subtotal - discount + shipping", mismatch.sum(), "kept, flagged", "Checked arithmetic of each order")
    return o[key].reset_index(drop=True)


def clean_customers(raw, settings, log):
    f = "customers.csv"
    c = raw.copy()
    c["customer_id"] = c["customer_id"].astype("string").str.strip()
    test = c["customer_id"].str.startswith(settings["test_id_prefix"], na=False)
    log.rule(f, "Test records", test.sum(), "removed", f"Customer IDs starting '{settings['test_id_prefix']}' are internal tests")
    c = c[~test]

    dup = c["customer_id"].duplicated()
    conflict = c[c["customer_id"].duplicated(keep=False)].groupby("customer_id")[
        ["acquisition_date", "acquisition_channel"]].nunique().gt(1).any(axis=1).sum()
    log.rule(f, "Duplicate customer IDs", dup.sum(), "kept the first row",
             f"Same customer exported twice; {conflict} of them disagree on date or channel")
    c = c[~dup]

    c["acquisition_date"] = parse_dates(c["acquisition_date"], f, "acquisition_date", log, settings,
                                        blank_note="kept, date unknown")

    label = c["acquisition_channel"].astype("string").str.strip().fillna("(blank)").replace("", "(blank)")
    cmap = settings["channel_map"]
    c["channel"] = label.map(cmap)
    unknown = c["channel"].isna()
    if unknown.any():
        new = label[unknown].value_counts()
        for lab, n in new.items():
            log.warn(f"customers.csv: new channel name '{lab}' on {n} customers is not in the channel mapping. "
                     f"Treated as 'Unmapped' this run. Add it to settings.xlsx > channel_mapping and run again.")
        c.loc[unknown, "channel"] = "Unmapped"
    remapped = (label != c["channel"]) & ~unknown
    log.rule(f, "Channel labels mapped to channels", remapped.sum(),
             f"{label.nunique()} labels -> {c['channel'].nunique()} channels (see channel_mapping)",
             "The attribution tool writes the same channel several ways ('Meta', 'FB/IG', 'paid_social')")
    excl = c["channel"] == "EXCLUDE"
    log.rule(f, "Channel set to EXCLUDE", excl.sum(), "removed", "Internal accounts, not real customers")
    c = c[~excl]
    return c[["customer_id", "acquisition_date", "channel"]].reset_index(drop=True)


def clean_spend(raw, settings, log):
    f = "marketing_spend.csv"
    s = raw.copy()
    s["line"] = s["channel"].astype("string").str.strip()
    text = s["spend_usd"].astype("string").str.strip()
    s["spend_usd"], fixed = to_amount(s["spend_usd"])
    if fixed:
        log.rule(f, "Amounts written as text", fixed, "read as numbers", "Some exports write money as '$1,234.56'")
    blank = (text.isna() | (text == "")).fillna(True).astype(bool)
    bad = s["spend_usd"].isna() & ~blank
    if bad.any():
        first = s[bad].iloc[0]
        raise ToolError(f"marketing_spend.csv: {int(bad.sum())} spend value(s) are not numbers, e.g. "
                        f"{first['month']} '{first['line']}': '{text[bad].iloc[0]}'. Fix the export and run again.")
    if blank.any():
        lines = ", ".join(sorted(set(s.loc[blank, "line"].dropna())))
        log.rule(f, "Blank spend", blank.sum(), "read as $0", "No amount given for that month and line")
        log.warn(f"marketing_spend.csv: {int(blank.sum())} blank spend value(s) were read as $0 ({lines}). "
                 "Check the export if those lines spent money.")
    s["spend_usd"] = s["spend_usd"].fillna(0.0)

    mtext = s["month"].astype("string").str.strip()
    month = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for fmt in SPEND_MONTH_FORMATS:
        todo = month.isna() & mtext.notna()
        if not todo.any():
            break
        p = pd.to_datetime(mtext[todo], format=fmt, errors="coerce")
        month.loc[p.index[p.notna()]] = p[p.notna()]
    if month.isna().any():
        bad_m = month.isna()
        raise ToolError(f"marketing_spend.csv: {int(bad_m.sum())} rows have a month that is not like 2026-10 "
                        f"(e.g. '{mtext[bad_m].iloc[0]}').")
    other = (mtext != month.dt.strftime("%Y-%m")).fillna(True).astype(bool)
    if other.any():
        log.rule(f, "Month written in another format", other.sum(), "read as year-month", "e.g. 'Oct-26' after re-saving in Excel")
    s["month"] = month.dt.to_period("M")

    dup = s.duplicated(subset=["month", "line", "spend_usd"])
    if dup.any():
        log.rule(f, "Exact duplicate rows", dup.sum(), "removed", "The same line exported twice would double the spend")
        s = s[~dup]
    multi = s.groupby(["month", "line"]).size()
    multi = multi[multi > 1]
    if len(multi):
        m, line = multi.index[0]
        log.warn(f"marketing_spend.csv: {len(multi)} month/line pair(s) appear more than once with different amounts "
                 f"(e.g. '{line}' in {m}); they were added together. Check the export.")

    smap = settings["spend_map"]
    s["channel"] = s["line"].map(lambda x: smap.get(x, (x, "hold_flat"))[0])
    s["treatment"] = s["line"].map(lambda x: smap.get(x, (x, "hold_flat"))[1])
    for line in sorted(set(s["line"].dropna()) - set(smap)):
        log.warn(f"marketing_spend.csv: new spend line '{line}' is not in the spend mapping. Held flat, not reallocated. "
                 f"Add it to settings.xlsx > spend_mapping to include it.")
    merged = s.groupby("channel")["line"].nunique()
    for ch, n in merged[merged > 1].items():
        lines = sorted(s.loc[s["channel"] == ch, "line"].unique())
        log.rule(f, "Spend lines merged", (s["channel"] == ch).sum(), f"{lines} -> '{ch}'",
                 "Same channel under different names over time (e.g. renamed by finance)")
    return s.groupby(["month", "channel", "treatment"], as_index=False)["spend_usd"].sum()


def clean_notes(raw, settings, log):
    n = raw.copy()
    n["customer_id"] = n["customer_id"].astype("string").str.strip()
    n["note_date"] = parse_dates(n["note_date"], "cs_notes.csv", "note_date", log, settings)
    dup = n.duplicated(subset=["customer_id", "note_date", "note_text"])
    log.rule("cs_notes.csv", "Duplicate notes", dup.sum(), "removed", "Same note logged twice")
    return n[~dup].reset_index(drop=True)


def load_all(folder, settings, log):
    raw = {name: read(folder, name, log) for name in REQUIRED}
    return {
        "orders": clean_orders(raw["orders"], settings, log),
        "customers": clean_customers(raw["customers"], settings, log),
        "spend": clean_spend(raw["marketing_spend"], settings, log),
        "notes": clean_notes(raw["cs_notes"], settings, log),
    }
