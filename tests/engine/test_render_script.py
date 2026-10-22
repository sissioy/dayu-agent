"""render.py 脚本回归测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_render_module():
    """按文件路径加载 render.py 模块。"""
    module_path = Path(__file__).resolve().parents[2] / "dayu" / "render" / "render.py"
    spec = importlib.util.spec_from_file_location("render_script", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_resource_path_deduplicate_dirs(tmp_path):
    """资源路径应去重并保留顺序。"""
    render_mod = _load_render_module()

    same_dir = tmp_path / "same"
    same_dir.mkdir()
    input_md = same_dir / "a.md"
    input_md.write_text("# t", encoding="utf-8")

    result = render_mod._build_resource_path(input_md, same_dir, same_dir)
    parts = result.split(render_mod.os.pathsep)

    assert parts == [str(same_dir.resolve())]


def test_main_default_output_uses_input_sibling_docx(tmp_path, monkeypatch):
    """未提供输出路径时，默认输出应在输入同目录且后缀为 .docx。"""
    render_mod = _load_render_module()

    input_md = tmp_path / "report.md"
    input_md.write_text("# report", encoding="utf-8")
    expected_output = input_md.with_suffix(".docx").resolve()

    calls: dict[str, Path] = {}

    def fake_generate_word(input_path: Path, output_path: Path, assets_dir: Path) -> None:
        calls["input"] = input_path
        calls["output"] = output_path
        calls["assets"] = assets_dir

    monkeypatch.setattr(render_mod, "_generate_word", fake_generate_word)
    monkeypatch.setattr(render_mod, "_open_file", lambda path: None)
    monkeypatch.setattr(render_mod.sys, "argv", ["render.py", str(input_md)])

    exit_code = render_mod.main()

    assert exit_code == 0
    assert calls["input"] == input_md.resolve()
    assert calls["output"] == expected_output
    assert calls["assets"].name == "render"


def test_main_pdf_branch_generates_html_then_pdf(tmp_path, monkeypatch):
    """PDF 分支应先生成 HTML，再调用 PDF 转换。"""
    render_mod = _load_render_module()

    input_md = tmp_path / "note.md"
    input_md.write_text("# note", encoding="utf-8")
    pdf_output = tmp_path / "final.pdf"

    calls: dict[str, Path | bool] = {}

    def fake_generate_html(
        input_path: Path,
        html_path: Path,
        assets_dir: Path,
        *,
        two_column: bool = False,
    ) -> None:
        """记录 HTML 渲染参数。"""

        calls["input"] = input_path
        calls["html"] = html_path
        calls["assets"] = assets_dir
        calls["two_column"] = two_column

    def fake_convert_html_to_pdf(html_path: Path, pdf_path: Path) -> None:
        calls["pdf_html"] = html_path
        calls["pdf"] = pdf_path

    monkeypatch.setattr(render_mod, "_generate_html", fake_generate_html)
    monkeypatch.setattr(render_mod, "_convert_html_to_pdf", fake_convert_html_to_pdf)
    monkeypatch.setattr(render_mod, "_open_file", lambda path: None)
    monkeypatch.setattr(render_mod.sys, "argv", ["render.py", str(input_md), str(pdf_output)])

    exit_code = render_mod.main()

    assert exit_code == 0
    assert calls["input"] == input_md.resolve()
    assert calls["html"] == pdf_output.resolve().with_suffix(".html")
    assert calls["pdf_html"] == calls["html"]
    assert calls["pdf"] == pdf_output.resolve()
    assert calls["two_column"] is False


def test_main_two_column_pdf_enables_two_column_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """双栏 PDF 应在中间 HTML 阶段启用专用样式。

    Args:
        tmp_path: pytest 临时目录 fixture。
        monkeypatch: pytest 属性替换 fixture。

    Returns:
        无。

    Raises:
        AssertionError: 双栏参数未传入 HTML 生成阶段时抛出。
    """

    render_mod = _load_render_module()
    input_md = tmp_path / "report.md"
    input_md.write_text("# report", encoding="utf-8")
    pdf_output = tmp_path / "report.pdf"
    calls: dict[str, bool] = {}

    def fake_generate_html(
        input_path: Path,
        html_path: Path,
        assets_dir: Path,
        *,
        two_column: bool = False,
    ) -> None:
        """记录是否启用双栏。

        Args:
            input_path: 输入 Markdown 路径。
            html_path: 输出 HTML 路径。
            assets_dir: 渲染资源目录。
            two_column: 是否启用双栏。

        Returns:
            无。

        Raises:
            无。
        """

        _ = (input_path, html_path, assets_dir)
        calls["two_column"] = two_column

    monkeypatch.setattr(render_mod, "_generate_html", fake_generate_html)
    monkeypatch.setattr(render_mod, "_convert_html_to_pdf", lambda html_path, pdf_path: None)
    monkeypatch.setattr(render_mod, "_open_file", lambda path: None)
    monkeypatch.setattr(
        render_mod.sys,
        "argv",
        ["render.py", str(input_md), str(pdf_output), "--two-column"],
    )

    assert render_mod.main() == 0
    assert calls["two_column"] is True


def test_generate_html_two_column_adds_css(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """双栏 HTML 应把专用 CSS 交给 Pandoc。

    Args:
        tmp_path: pytest 临时目录 fixture。
        monkeypatch: pytest 属性替换 fixture。

    Returns:
        无。

    Raises:
        AssertionError: Pandoc 参数未包含双栏样式时抛出。
    """

    render_mod = _load_render_module()
    input_md = tmp_path / "report.md"
    input_md.write_text("# report", encoding="utf-8")
    output_html = tmp_path / "report.html"
    assert render_mod.__file__ is not None
    assets_dir = Path(render_mod.__file__).resolve().parent
    calls: dict[str, list[str]] = {}

    def fake_run_pandoc(args: list[str], *, cwd: Path) -> None:
        """记录 Pandoc 参数。

        Args:
            args: Pandoc 命令参数。
            cwd: Pandoc 工作目录。

        Returns:
            无。

        Raises:
            无。
        """

        _ = cwd
        calls["args"] = args

    monkeypatch.setattr(render_mod, "_run_pandoc", fake_run_pandoc)

    render_mod._generate_html(
        input_md,
        output_html,
        assets_dir,
        two_column=True,
    )

    assert f"--css={assets_dir / 'two-column.css'}" in calls["args"]


def test_main_usage_message_when_missing_argument(capsys, monkeypatch):
    """无参数时应返回 1 并输出约定 Usage。"""
    render_mod = _load_render_module()

    monkeypatch.setattr(render_mod.sys, "argv", ["render.py"])
    exit_code = render_mod.main()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Usage: dayu-render <input_markdown> [output_path]" in captured.err


def test_main_help_message(capsys, monkeypatch):
    """传入帮助参数时应返回 0 并输出标准帮助。"""
    render_mod = _load_render_module()

    monkeypatch.setattr(render_mod.sys, "argv", ["render.py", "--help"])
    exit_code = render_mod.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Usage: dayu-render <input_markdown> [output_path]" in captured.out
    assert "将 Markdown 渲染为 HTML / PDF / Word。" in captured.out


def test_generate_word_uses_hard_line_break_reader(tmp_path, monkeypatch):
    """Word 渲染应把普通换行作为硬换行传给 pandoc。"""
    render_mod = _load_render_module()

    input_md = tmp_path / "report.md"
    input_md.write_text("- 公司简介\n  第二行说明", encoding="utf-8")
    output_docx = tmp_path / "report.docx"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()

    calls: dict[str, object] = {}

    def fake_run_pandoc(args: list[str], *, cwd: Path) -> None:
        calls["args"] = args
        calls["cwd"] = cwd

    monkeypatch.setattr(render_mod, "_run_pandoc", fake_run_pandoc)

    render_mod._generate_word(input_md, output_docx, assets_dir)

    args = calls["args"]

    assert isinstance(args, list)
    assert "-f" in args
    assert args[args.index("-f") + 1] == "gfm+hard_line_breaks"
    assert calls["cwd"] == output_docx.parent
    assert args[-2:] == ["-o", str(output_docx)]
