import xml.etree.ElementTree as ET

import pandas as pd

from src.collector import ApartmentDataCollector


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def make_xml(total_count, item_count, offset=0):
    root = ET.Element("response")
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "resultCode").text = "000"
    body = ET.SubElement(root, "body")
    ET.SubElement(body, "totalCount").text = str(total_count)
    items = ET.SubElement(body, "items")
    for index in range(offset, offset + item_count):
        item = ET.SubElement(items, "item")
        ET.SubElement(item, "dealAmount").text = str(index)
    return ET.tostring(root, encoding="unicode")


def make_collector():
    collector = ApartmentDataCollector.__new__(ApartmentDataCollector)
    collector.api_key = "test-key"
    collector.request_interval = 0
    return collector


def test_fetches_every_page(monkeypatch):
    requested_pages = []

    def fake_get(url, params, timeout):
        page = params["pageNo"]
        requested_pages.append(page)
        counts = {1: (1000, 0), 2: (1000, 1000), 3: (500, 2000)}
        count, offset = counts[page]
        return FakeResponse(make_xml(2500, count, offset))

    monkeypatch.setattr("src.collector.requests.get", fake_get)
    result = make_collector()._fetch_one_from("https://example.test", "11110", "202601")

    assert requested_pages == [1, 2, 3]
    assert len(result) == 2500
    assert result["수집_시군구코드"].eq("11110").all()
    assert result["수집_연월"].eq("202601").all()


def test_discards_partial_month_when_later_page_fails(monkeypatch):
    def fake_get(url, params, timeout):
        if params["pageNo"] == 1:
            return FakeResponse(make_xml(1500, 1000))
        return FakeResponse("server error", status_code=500)

    monkeypatch.setattr("src.collector.requests.get", fake_get)
    result = make_collector()._fetch_one_from("https://example.test", "11110", "202601")

    assert isinstance(result, pd.DataFrame)
    assert result.empty
    assert result.attrs["fetch_ok"] is False


def test_fetch_range_aborts_when_any_region_fails():
    collector = make_collector()

    def fake_fetch(sigungu_code, year_month):
        if sigungu_code == "bad":
            result = pd.DataFrame()
            result.attrs["fetch_ok"] = False
            return result
        result = pd.DataFrame({"value": [1]})
        result.attrs["fetch_ok"] = True
        return result

    result = collector.fetch_range(["good", "bad"], "202601", "202601", fetch_fn=fake_fetch)

    assert result.empty
    assert result.attrs["fetch_ok"] is False
