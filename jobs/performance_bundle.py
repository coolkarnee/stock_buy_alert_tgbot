"""Build one performance artifact with separate stock and commodity sections."""

from pathlib import Path

import pandas as pd


ARTIFACT_DIR = Path("artifacts")
STOCK_FILE = ARTIFACT_DIR / "performance.csv"
COMMODITY_FILE = ARTIFACT_DIR / "commodity_alerts.csv"
COMMODITY_PERFORMANCE_FILE = ARTIFACT_DIR / "commodity_performance.csv"
COMBINED_FILE = ARTIFACT_DIR / "performance.csv"
STOCK_COPY = ARTIFACT_DIR / "stock_performance.csv"
WORKBOOK_FILE = ARTIFACT_DIR / "performance.xlsx"


def _read(path, asset_class, record_type):
    if not path.exists():
        return pd.DataFrame({"asset_class": [asset_class], "record_type": [record_type], "status": ["NO DATA"]})
    frame = pd.read_csv(path)
    if "asset_class" in frame.columns:
        frame["asset_class"] = asset_class
    else:
        frame.insert(0, "asset_class", asset_class)
    if "record_type" in frame.columns:
        frame["record_type"] = record_type
    else:
        frame.insert(1, "record_type", record_type)
    return frame


def build_bundle():
    stock = _read(STOCK_FILE, "stock", "performance")
    commodity = _read(COMMODITY_PERFORMANCE_FILE, "commodity", "performance")
    commodity_alerts = _read(COMMODITY_FILE, "commodity", "alert")
    STOCK_COPY.write_text(stock.drop(columns=["asset_class", "record_type"]).to_csv(index=False), encoding="utf-8")
    combined = pd.concat([stock, commodity], ignore_index=True, sort=False)
    combined.to_csv(COMBINED_FILE, index=False)
    with pd.ExcelWriter(WORKBOOK_FILE, engine="openpyxl") as writer:
        stock.to_excel(writer, sheet_name="Stock Performance", index=False)
        commodity.to_excel(writer, sheet_name="Commodity Performance", index=False)
        commodity_alerts.to_excel(writer, sheet_name="Commodity Alerts", index=False)
        combined.to_excel(writer, sheet_name="Combined", index=False)


if __name__ == "__main__":
    build_bundle()
