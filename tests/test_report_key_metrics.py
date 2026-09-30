from backend.reporting.presentation import HumanReportAssembler


def _total(value):
    return {"metric_key": "metric:total", "metric_name": "scalar", "label": "审核结果总数", "value": value, "group": {}}


def _decision(decision, count, total):
    return [
        {"metric_key": f"metric:{decision}:count", "metric_name": "count", "value": count, "group": {"decision": decision}},
        {
            "metric_key": f"metric:{decision}:percentage",
            "metric_name": "percentage",
            "value": round(count / total * 100, 6),
            "group": {"decision": decision},
        },
    ]


def _risk(level, count):
    return {"metric_key": f"metric:risk:{level}", "metric_name": "count", "value": count, "group": {"risk_level": level}}


def _rows(metrics):
    return [(item.label, item.value, item.detail) for item in HumanReportAssembler()._key_metrics(metrics)]


def test_key_metrics_split_the_total_by_audit_decision():
    metrics = [
        _total(7),
        *_decision("pass", 1, 7),
        *_decision("reject", 4, 7),
        *_decision("review", 2, 7),
        _risk("high", 4),
        _risk("none", 1),
    ]

    assert _rows(metrics) == [
        ("分析内容", "7条", ""),
        ("建议拦截", "4条", "约57.1%"),
        ("进入复审", "2条", "约28.6%"),
        ("审核通过", "1条", "约14.3%"),
    ]
    reject = HumanReportAssembler()._key_metrics(metrics)[1]
    assert reject.metric_refs == ("metric:reject:count", "metric:reject:percentage")


def test_key_metrics_keep_every_decision_when_all_findings_are_high_risk():
    metrics = [_total(3), *_decision("reject", 3, 3), _risk("high", 3)]

    assert _rows(metrics) == [
        ("分析内容", "3条", ""),
        ("建议拦截", "3条", "约100%"),
        ("进入复审", "0条", ""),
        ("审核通过", "0条", ""),
    ]
    zero = HumanReportAssembler()._key_metrics(metrics)[2]
    assert zero.metric_refs == ()


def test_key_metrics_do_not_invent_zero_rows_without_a_total():
    assert _rows([*_decision("review", 2, 2)]) == [("进入复审", "2条", "约100%")]
