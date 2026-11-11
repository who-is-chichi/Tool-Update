"""Shared pieces: the error type, the cleaning log, and reading and writing settings.xlsx."""
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import re

import pandas as pd


class ToolError(Exception):
    """A problem the user must fix (missing file, missing column...). Shown as plain text, never a stack trace."""


@dataclass
class Log:
    """Every cleaning rule and every warning, in the order they happened."""
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def rule(self, file, rule, rows_affected, action, why):
        self.rows.append({"file": file, "rule": rule, "rows affected": int(rows_affected),
                          "action": action, "why": why})

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def table(self):
        return pd.DataFrame(self.rows, columns=["file", "rule", "rows affected", "action", "why"])


# Settings added after the first version. An older settings.xlsx without them still works: the default is used, the
# run says so, and "Save these as the defaults" adds the row.   key: (default, unit, what it means)
NEW_SETTINGS = {
    "max_change_vs_last_year": (
        0.25, "share",
        "Guardrail. Each channel stays within plus or minus this share of its spend in the same month this year "
        "(0.25 = 75% to 125%); the 75% floor is the channel minimum. Keeps the plan near spend levels the data has "
        "seen and gives agencies time to adjust; the planner reruns every month."),
    "max_above_record_month": (
        0.10, "share",
        "Guardrail. A channel's recommended spend never exceeds its highest month in the data by more than this "
        "share; beyond that the response curve is a guess. Blank = no cap."),
    "hold_low_confidence_channels": (
        "Yes", "Yes/No",
        "Yes = a channel whose response to spend can't be measured reliably (its spend has varied little, or the "
        "estimate is noisy) is held at this year's spend."),
    "finance_as_of_month": (
        "2026-10", "YYYY-MM",
        "Month finance's reference figures were taken. The checks recompute the tool's figures at that month, so "
        "they compare like with like on any new month of data. Blank = latest data."),
    "date_formats": (
        "YYYY-MM-DD; MM/DD/YYYY; YYYY/MM/DD; DD-Mon-YYYY; YYYY-MM-DD HH:MM:SS", "text",
        "Date formats accepted in the files, tried in this order, separated by ';'. YYYY year, MM month, DD day, "
        "Mon = Jan..Dec; after a space, HH:MM:SS is a time. If the tool stops on unreadable dates, add the pattern "
        "it shows, for example DD.MM.YYYY."),
}
OLD_NAMES = {"max_monthly_change": "max_change_vs_last_year"}

# Customer-service note themes: (theme, keywords, a service problem?). First match wins; case is ignored.
# The team edits them in settings.xlsx > note_themes; this list is used only when that sheet is missing.
DEFAULT_THEMES = [
    ("Late shipment (warehouse backlog)", ["days late", "warehouse backlog", "shipped late"], True),
    ("Delivered but not received", ["not received", "delivered but", "carrier claim"], True),
    ("Damaged in transit", ["arrived damaged", "damaged"], True),
    ("Promo code problem", ["promo code did not apply", "code expired", "promo code still"], True),
    ("Referral credit not posted", ["referral credit did not post", "credit did not post"], True),
    ("Duplicate account", ["account merge", "two emails"], True),
    ("Fit and sizing", ["runs small", "runs large", "sizing", "size chart", "did not fit", "two sizes",
                        "exchange"], True),
    ("Colour not as shown in social video", ["looked different", "tiktok video"], True),
    ("Out of stock", ["back in stock", "out of stock"], True),
    ("Returns question", ["how returns work", "return request", "rma"], False),
    ("Address change", ["address change"], False),
    ("Unsubscribe", ["unsubscribe"], False),
    ("Discount question", ["student discount", "discount"], False),
    ("Price match", ["price match"], False),
    ("Product question", ["water resistant", "recycled materials", "warranty", "product details", "repair"], False),
    ("Compliment or colour request", ["compliment", "colorway", "colour"], False),
    ("Mission and foundation", ["foundation", "donation"], False),
    ("Referral question", ["refer-a-friend", "recommended us", "referral"], False),
    ("Gift order", ["gift"], False),
]

NUMBERS = ["gross_margin", "fulfillment_cost_per_order", "payment_processing_rate", "return_handling_cost_per_refund",
           "sample_rate", "ltv_horizon_months", "payback_window_months", "max_change_vs_last_year",
           "recent_cohort_months", "reference_cohort_months", "response_history_months", "max_unreadable_share"]
INTEGERS = ["ltv_horizon_months", "payback_window_months", "recent_cohort_months", "reference_cohort_months",
            "response_history_months"]
REQUIRED_SHEETS = ["assumptions", "channel_mapping", "spend_mapping", "finance_reference"]
TREATMENTS = ("reallocate", "hold_flat", "retention")


# ---------------------------------------------------------------- date patterns
_DATE_TOKENS = [("YYYY", "%Y"), ("MONTH", "%B"), ("MON", "%b"), ("YY", "%y"), ("MM", "%m"), ("DD", "%d")]
_TIME_TOKENS = [("HH", "%H"), ("MM", "%M"), ("SS", "%S")]


def _swap(part, tokens):
    out, i, up = "", 0, part.upper()
    while i < len(part):
        for tok, code in tokens:
            if up.startswith(tok, i):
                out += code
                i += len(tok)
                break
        else:
            out += part[i]
            i += 1
    return out


def pattern_to_strptime(pattern):
    """'DD.MM.YYYY' -> '%d.%m.%Y'. The time starts at HH, and MM after it means minutes ('YYYY-MM-DD HH:MM:SS').
    Raw % codes pass through unchanged."""
    p = str(pattern).strip()
    if "%" in p:
        return p
    cut = p.upper().find("HH")
    date_part, time_part = (p, "") if cut < 0 else (p[:cut], p[cut:])
    return _swap(date_part, _DATE_TOKENS) + _swap(time_part, _TIME_TOKENS)


def check_date_pattern(pattern):
    """A pattern must read back a full date (year, month and day). Returns the strptime code."""
    fmt = pattern_to_strptime(pattern)
    sample = datetime(2026, 11, 23, 14, 35, 52)
    try:
        back = datetime.strptime(sample.strftime(fmt), fmt)
    except Exception:  # noqa: BLE001  (any failure means the pattern is unusable)
        back = None
    if back is None or back.date() != sample.date():
        raise ToolError(f"The date pattern '{pattern}' in settings.xlsx > assumptions > date_formats can't be used. "
                        "Write it with YYYY, MM, DD (and Mon for Jan..Dec), for example DD.MM.YYYY.")
    return fmt


# ---------------------------------------------------------------- reading settings.xlsx
def _blank(v):
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    return isinstance(v, str) and v.strip() in ("", "nan", "None", "NaT")


def _yes(v, default=True):
    if isinstance(v, bool):
        return v
    if _blank(v):
        return default
    return str(v).strip().lower().startswith(("y", "t", "1"))


def _number(key, v):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ToolError(f"Setting '{key}' in settings.xlsx must be a number (it is '{v}').")


def _month(v):
    """'2026-10', an Excel date, or blank -> 'YYYY-MM' or None."""
    if _blank(v):
        return None
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return f"{v.year}-{v.month:02d}"
    t = str(v).strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?(?:\s.*)?", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1))}-{int(m.group(2)):02d}"
    try:
        ts = pd.to_datetime(t)
        return f"{ts.year}-{ts.month:02d}"
    except (ValueError, TypeError):
        raise ToolError(f"Setting 'finance_as_of_month' in settings.xlsx must be a month like 2026-10 (it is '{t}').")


def _themes(sheet):
    out = []
    for _, r in sheet.iterrows():
        theme, kws = r.iloc[0], r.iloc[1] if len(r) > 1 else None
        if _blank(theme) or _blank(kws):
            continue
        words = [k.strip() for k in str(kws).split(";") if k.strip()]
        out.append((str(theme).strip(), words, _yes(r.iloc[2] if len(r) > 2 else None, default=False)))
    return out


def load_settings(path):
    """Read settings.xlsx into plain Python: assumptions, the mapping tables, finance's figures and note themes."""
    path = Path(path)
    if not path.exists():
        raise ToolError(f"Settings file not found: {path}")
    try:
        xl = pd.read_excel(path, sheet_name=None)
    except PermissionError:
        raise ToolError("settings.xlsx can't be opened. Close it in Excel and try again.")
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"settings.xlsx can't be read ({e}). Restore it from the original folder.")
    for sheet in REQUIRED_SHEETS:
        if sheet not in xl:
            raise ToolError(f"settings.xlsx is missing the sheet '{sheet}'.")

    a = xl["assumptions"]
    if not {"setting", "value"} <= set(a.columns):
        raise ToolError("settings.xlsx > assumptions needs the columns 'setting' and 'value'.")
    s = {str(k).strip(): v for k, v in zip(a["setting"], a["value"]) if not _blank(k)}
    notes = []
    for old, new in OLD_NAMES.items():
        if old in s:
            s.setdefault(new, s.pop(old))
    for k, (default, _, _) in NEW_SETTINGS.items():
        if k not in s:
            s[k] = default
            notes.append(f"settings.xlsx has no '{k}' row, so the default ({default}) was used. "
                         "Click 'Save these as the defaults' in the app to add it.")
    for k in NUMBERS:
        if k not in s or _blank(s[k]):
            raise ToolError(f"Setting '{k}' in settings.xlsx must be a number.")
        s[k] = _number(k, s[k])
    for k in INTEGERS:
        s[k] = int(round(s[k]))
    budget = s.get("acquisition_budget_annual")
    budget = None if _blank(budget) else _number("acquisition_budget_annual", budget)
    s["acquisition_budget_annual"] = budget or None
    cap = s.get("max_above_record_month")
    s["max_above_record_month"] = None if _blank(cap) else _number("max_above_record_month", cap)
    s["returns_resold"] = _yes(s.get("returns_resold"))
    s["hold_low_confidence_channels"] = _yes(s.get("hold_low_confidence_channels"))
    s["test_id_prefix"] = str(s.get("test_id_prefix", "TEST")).strip()

    cm = xl["channel_mapping"]
    s["channel_map"] = {str(k).strip(): str(v).strip() for k, v in zip(cm.iloc[:, 0], cm.iloc[:, 1])
                        if not _blank(k) and not _blank(v)}
    sm = xl["spend_mapping"]
    s["spend_map"] = {str(k).strip(): (str(c).strip(), str(t).strip().lower())
                      for k, c, t in zip(sm.iloc[:, 0], sm.iloc[:, 1], sm.iloc[:, 2]) if not _blank(k) and not _blank(c)}
    for line, (_, t) in s["spend_map"].items():
        if t not in TREATMENTS:
            raise ToolError(f"settings.xlsx > spend_mapping: the treatment for '{line}' must be reallocate, hold_flat "
                            f"or retention (it is '{t}').")
    fr = xl["finance_reference"]
    s["finance"] = {str(k).strip(): _number(str(k), v) for k, v in zip(fr.iloc[:, 0], fr.iloc[:, 1])
                    if not _blank(k) and not _blank(v)}
    themes = xl.get("note_themes")
    s["note_themes"] = _themes(themes) if themes is not None and len(themes) else [tuple(t) for t in DEFAULT_THEMES]
    s["settings_notes"] = notes
    return check_settings(s)


def check_settings(s):
    """Checks shared by settings.xlsx and settings edited in the app: ranges, date patterns, finance month."""
    if not 0 < s["sample_rate"] <= 1:
        raise ToolError("Setting 'sample_rate' must be between 0 and 1 (0.25 = a 25% sample of customers).")
    if s["payback_window_months"] < 1 or s["ltv_horizon_months"] < 1:
        raise ToolError("The payback window and the value horizon must each be at least 1 month.")
    if not 0 <= s["max_change_vs_last_year"] <= 1:
        raise ToolError("Setting 'max_change_vs_last_year' must be between 0 and 1 (0.25 = 75% to 125%).")
    formats = s.get("date_formats")
    pats = list(formats) if isinstance(formats, (list, tuple)) else [p.strip() for p in str(formats).split(";")
                                                                     if p.strip()]
    if not pats:
        raise ToolError("Setting 'date_formats' is empty. Restore it, for example: YYYY-MM-DD; MM/DD/YYYY.")
    for p in pats:
        check_date_pattern(p)
    s["date_formats"] = pats
    s["finance_as_of_month"] = _month(s.get("finance_as_of_month"))
    return s


# ---------------------------------------------------------------- writing settings.xlsx
def _cell(v):
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    return v


def save_settings(path, s):
    """Write edited assumptions and mappings back into settings.xlsx, keeping its layout and descriptions.
    Rows added in later versions are added if missing, so an older settings file is brought up to date."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path)
    except PermissionError:
        raise ToolError("settings.xlsx is open in another program. Close it and click Save again.")
    ws = wb["assumptions"]
    seen = set()
    for row in ws.iter_rows(min_row=2):
        if row[0].value is None:
            continue
        key = str(row[0].value).strip()
        if key in OLD_NAMES:
            key = OLD_NAMES[key]
            row[0].value = key
            _, unit, desc = NEW_SETTINGS[key]
            if len(row) > 2:
                row[2].value = unit
            if len(row) > 3:
                row[3].value = desc
        if key in s and (key == "date_formats" or not isinstance(s[key], (dict, list, tuple))):
            row[1].value = _cell(s[key])
            seen.add(key)
    for key, (default, unit, desc) in NEW_SETTINGS.items():
        if key not in seen:
            ws.append([key, _cell(s.get(key, default)), unit, desc])
    for sheet, rows in [("channel_mapping", list(s["channel_map"].items())),
                        ("spend_mapping", [(k, c, t) for k, (c, t) in s["spend_map"].items()])]:
        sh = wb[sheet]
        sh.delete_rows(2, sh.max_row)
        for r in rows:
            if not _blank(r[0]) and not _blank(r[1]):
                sh.append(list(r))
    if "note_themes" not in wb.sheetnames:
        sh = wb.create_sheet("note_themes")
        sh.append(["theme", "keywords (separated by ;)", "service problem"])
        for theme, words, problem in s.get("note_themes") or DEFAULT_THEMES:
            sh.append([theme, "; ".join(words), "Yes" if problem else "No"])
    try:
        wb.save(path)
    except PermissionError:
        raise ToolError("settings.xlsx could not be saved because it is open in another program (probably Excel). "
                        "Close it and click Save again.")
