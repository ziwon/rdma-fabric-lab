from fleet.values_keys import unknown_keys

DEFAULTS = {
    "kubeProxyReplacement": "false",
    "ipam": {"mode": "cluster-pool", "operator": {"clusterPoolIPv4MaskSize": 24}},
    "nodeSelector": {},
    "enableIPv4Masquerade": None,
    "devices": "",
}


def test_keys_of_the_chart_are_known():
    assert unknown_keys({"kubeProxyReplacement": True, "ipam": {"mode": "kubernetes"}, "devices": ["mgmt0"]}, DEFAULTS) == []


def test_a_misspelled_key_is_named_with_its_path():
    assert unknown_keys({"kubeProxyReplacment": True}, DEFAULTS) == ["kubeProxyReplacment is not a key of the chart"]
    assert unknown_keys({"ipam": {"operator": {"clusterPoolIPv4MaskSiz": 26}}}, DEFAULTS) == [
        "ipam.operator.clusterPoolIPv4MaskSiz is not a key of the chart"
    ]


def test_an_empty_map_or_null_default_takes_any_key():
    assert unknown_keys({"nodeSelector": {"rfl.example/node-class": "x"}, "enableIPv4Masquerade": {"x": 1}}, DEFAULTS) == []


def test_a_map_where_the_chart_has_a_scalar_is_named():
    assert unknown_keys({"devices": {"mgmt0": True}}, DEFAULTS) == ["devices is a map here but a str in the chart"]
