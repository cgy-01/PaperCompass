"""WSL may bind a port which Windows has excluded from localhost forwarding."""
import pytest

from scripts.local_port import parse_exclusions, select_port


def test_localized_headers_crlf_and_administered_ranges():
    output = '协议 tcp 端口排除范围\r\n开始端口 结束端口\r\n  8762 8861\r\n 8962 9061\r\n 50000 50059 *\r\n'.encode('gbk')
    assert parse_exclusions(output) == [(8762, 8861), (8962, 9061), (50000, 50059)]


def test_default_windows_reserved_port_uses_available_alternative():
    assert select_port(9000, [(8962, 9061)], can_bind=lambda port: True) == 8080


def test_explicit_reserved_port_is_not_silently_changed():
    with pytest.raises(ValueError, match='Windows 已保留端口 9000'):
        select_port(9000, [(8962, 9061)], explicit=True)


def test_fallback_skips_busy_and_excluded_ports():
    assert select_port(9000, [(8962, 9061), (18000, 18100)],
                       can_bind=lambda port: port != 8080) == 5173


def test_nonreserved_port_keeps_existing_startup_behavior():
    assert select_port(8765, [], explicit=True, can_bind=lambda _: False) == 8765


def test_no_fallback_is_a_clear_failure():
    with pytest.raises(ValueError, match='没有找到可用'):
        select_port(9000, [(8962, 9061)], can_bind=lambda _: False)
