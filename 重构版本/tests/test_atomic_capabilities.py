from robot_modbus_lite.atomic_capabilities import (
    atomic_capability_rows,
    atomic_capability_summary,
    export_atomic_capability_markdown,
)


def test_atomic_capability_rows_cover_supported_and_guarded_groups():
    rows = atomic_capability_rows()
    keys = {row["key"] for row in rows}

    assert {"joint_j", "virtual_v", "cartesian_c", "delay_d", "io", "memory_sp", "dashboard_q"} <= keys
    assert {"complex_guard", "func11_guard"} <= keys
    assert all(row["status"] in {"implemented", "basic", "guarded", "deferred"} for row in rows)


def test_atomic_capability_summary_counts_statuses():
    summary = atomic_capability_summary()

    assert summary["total"] >= 10
    assert summary["implemented"] >= 1
    assert summary["basic"] >= 1
    assert summary["guarded"] >= 1
    assert summary["deferred"] >= 1


def test_export_atomic_capability_markdown_is_reviewable():
    markdown = export_atomic_capability_markdown()

    assert "# 二次原子函数能力审计清单" in markdown
    assert "| J 类关节命令保护 |" in markdown
    assert "| Func11 连续插补执行 |" in markdown
    assert "保护性拒绝" in markdown
