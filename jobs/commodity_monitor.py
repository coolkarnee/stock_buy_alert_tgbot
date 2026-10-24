"""Start the commodity futures Telegram alert workflow."""

from commodity.monitor import CommodityMonitor
from commodity.alerts import ensure_alert_file


if __name__ == "__main__":
    ensure_alert_file()
    monitor = CommodityMonitor()
    monitor.refresh()
    monitor.run_forever()
