"""Parses REAL Cloud Monitoring protobuf points, not the fakes.

The fakes in conftest cannot catch a wrong assumption about the protobuf surface,
and this parser has one genuinely subtle case: protobuf scalar fields default to
0/False rather than being unset, so "which typed field carries the value" cannot
be answered by truthiness alone. A measured 0 (a failed step, a down Gateway)
must survive as 0 and never degrade into "no data".
"""

from __future__ import annotations

import pytest

from app.metrics import _point_epoch, _point_value

monitoring_v3 = pytest.importorskip("google.cloud.monitoring_v3")

EPOCH = 1787944000


def make_point(typed_value):
    return monitoring_v3.Point(
        interval=monitoring_v3.TimeInterval(end_time={"seconds": EPOCH}),
        value=typed_value,
    )


@pytest.mark.parametrize(
    ("typed_value", "expected"),
    [
        (monitoring_v3.TypedValue(int64_value=143), 143.0),
        (monitoring_v3.TypedValue(int64_value=0), 0.0),
        (monitoring_v3.TypedValue(double_value=743.2), 743.2),
        (monitoring_v3.TypedValue(double_value=0.0), 0.0),
        (monitoring_v3.TypedValue(bool_value=True), 1.0),
        (monitoring_v3.TypedValue(bool_value=False), 0.0),
    ],
)
def test_real_typed_values_parse(typed_value, expected):
    assert _point_value(make_point(typed_value)) == pytest.approx(expected)


def test_real_interval_epoch_parses():
    point = make_point(monitoring_v3.TypedValue(int64_value=1))
    assert _point_epoch(point) == EPOCH
